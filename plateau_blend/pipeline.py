"""Phase-1 orchestration. Never edits source files; atomic .blend publication."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import hashlib
import json
import os
import subprocess
import uuid
from PIL import Image, UnidentifiedImageError
from .config import Config
from .geo import WorldFile
from .obj import ObjStats, inspect_obj, normalize_obj, union_bbox, overlaps_xy, obj_dependencies, has_used_diffuse_texture
from .runtime import CancelToken, run_logged
from .artifact_cache import valid as cache_valid, mark as cache_mark
from .verification import validate_report

Log = Callable[[str], None]
BLEND_MAGICS = (b'BLENDER', b'\x28\xb5\x2f\xfd', b'\x1f\x8b')  # raw, zstd, gzip


def _bbox2(s: tuple[float, ...]) -> tuple[float, float, float, float]:
    return (s[0], s[1], s[3], s[4])


def _stat_fingerprint(path: Path) -> dict:
    st = path.stat()
    return {'path': str(path), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def inspect_image(path: Path) -> dict:
    with Image.open(path) as im:
        if im.format != 'PNG':
            raise ValueError(f'{path}: PNG形式でありません')
        width, height = im.size
        if width < 2 or height < 2:
            raise ValueError('航空写真の幅・高さが小さすぎます')
        im.thumbnail((256, 256))
        pix = im.convert('RGBA')
        count = pix.width * pix.height
        pixels = pix.load()
        data = [pixels[x, y] for y in range(pix.height) for x in range(pix.width)]
        black = sum(1 for r,g,b,a in data if r <= 6 and g <= 6 and b <= 6 and a > 0)
        transparent = sum(1 for _,_,_,a in data if a <= 5)
        return {'width': width, 'height': height, 'near_black_ratio': round(black / count, 4),
                'transparent_ratio': round(transparent / count, 4)}


def validate(config: Config, *, allow_managed_inputs: bool = False) -> dict:
    for path in (*config.building_objs, *config.terrain_objs, config.aerial_png, config.world_file):
        if not path.is_file():
            raise FileNotFoundError(f'入力ファイル不在: {path}')
    if config.output_blend.suffix.lower() != '.blend':
        raise ValueError('出力は .blend 拡張子を指定してください')
    work = config.output_blend.with_suffix('.work')
    def conflicts(p: Path) -> bool:
        managed = allow_managed_inputs and (work / 'auto') in p.parents
        return (p == config.output_blend or p == config.output_blend.with_suffix('.provenance.json')
                or ((work in p.parents or p == work) and not managed))
    if any(conflicts(p) for p in
           (*config.building_objs, *config.terrain_objs, config.aerial_png, config.world_file)):
        raise ValueError('入力と出力・作業フォルダのパスが衝突しています')
    wf = WorldFile.read(config.world_file)
    image = inspect_image(config.aerial_png)
    building_stats = [inspect_obj(p, config.building_axis) for p in config.building_objs]
    terrain_stats = [inspect_obj(p, config.terrain_axis) for p in config.terrain_objs]
    b = union_bbox(building_stats)
    t = union_bbox(terrain_stats)
    combined = union_bbox([*building_stats, *terrain_stats])
    if not overlaps_xy(b, t):
        raise ValueError('建物と地形のXY範囲が交差しません。EPSG/軸順/変換を確認してください')
    coverage = wf.sample_coverage(_bbox2(t), image['width'], image['height'])
    if coverage < 1 and not config.allow_partial_imagery:
        raise ValueError(f'航空写真が地形を覆いません (9×9標本: {coverage:.1%})。PGW・CRS・範囲を確認。許容時は allow_partial_imagery=true')
    suspicious = max(image['near_black_ratio'], image['transparent_ratio'])
    if suspicious >= 0.30 and not config.allow_suspect_imagery:
        raise ValueError(f'航空写真の黒/透明領域が30%以上です ({suspicious:.1%})。画像を確認してください')
    if not config.imagery_attribution:
        raise ValueError('航空写真の出典 (imagery_attribution) が未指定です')
    if not config.imagery_terms_url:
        raise ValueError('航空写真の利用条件URL (imagery_terms_url) が未指定です')
    warnings = []
    if coverage < 1:
        warnings.append(f'航空写真は地形に対し一部欠落: {coverage:.1%} (9×9標本)')
    if suspicious >= 0.01:
        warnings.append(f'画像に黒/透明領域を検出: 黒 {image["near_black_ratio"]:.1%}, 透明 {image["transparent_ratio"]:.1%}（写真内の暗所も検出対象）')
    if max(combined[3] - combined[0], combined[4] - combined[1]) > 20000:
        warnings.append('モデルのXY幅が20km超。Blender浮動小数精度とメモリ負荷に注意')
    origin = ((combined[0] + combined[3]) / 2, (combined[1] + combined[4]) / 2, 0.0)
    return {'epsg': config.epsg, 'origin': origin, 'building_stats': building_stats,
            'terrain_stats': terrain_stats, 'building_bbox': b, 'terrain_bbox': t,
            'scene_bbox': combined, 'coverage': coverage, 'image': image, 'warnings': warnings,
            'world_file': wf}


def report(preflight: dict) -> dict:
    return {
        'epsg': preflight['epsg'], 'origin_xy': list(preflight['origin'])[:2],
        'buildings_bbox_xyz': list(preflight['building_bbox']),
        'terrain_bbox_xyz': list(preflight['terrain_bbox']),
        'buildings_vertices': sum(s.vertices for s in preflight['building_stats']),
        'terrain_vertices': sum(s.vertices for s in preflight['terrain_stats']),
        'image': preflight['image'], 'image_terrain_coverage_sample': preflight['coverage'],
        'warnings': preflight['warnings'],
        'checks': {'crs': '同一CRSのユーザー申告のみ。実データのEPSGは自動検証不可',
                   'coverage': '地形バウンディングボックスの9×9標本',
                   'imagery_black': '画像縮小標本の黒/透明画素率、タイル欠損の厳密検出ではない'}
    }


def _stage_obj(src: Path, role: str, axis: str, origin: tuple[float, float, float],
               cache: Path, warnings: list[str], log: Log, ordinal: int,
               *, cancel: CancelToken | None = None) -> Path:
    """Atomically stage an OBJ and verify *all* outputs before cache reuse.

    v0.6 accepted any file with a ready.json marker, including an OBJ later
    truncated by a crash or modified out-of-band. v0.7 uses a content manifest.
    Failed/cancelled staging leaves no success marker. No source is modified.
    """
    if cancel: cancel.check()
    deps = obj_dependencies(src)
    fingerprint = {'v': 3, 'input': _stat_fingerprint(src),
                   'dependencies': [_stat_fingerprint(p) for p in deps],
                   'axis': axis, 'origin': list(origin)}
    key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:20]
    d = cache / f'{role}_{ordinal}_{key}'
    dest = d / src.name
    marker = d / 'ready.json'
    cache_key = ['normalized-obj-v3', fingerprint]
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding='utf-8'))
            paths = [d / name for name in payload['generated']]
            # Require exactly one OBJ, and constrain manifest members to one directory.
            if (payload.get('fingerprint') == fingerprint and
                paths and paths[0] == dest and
                all(p.parent == d and p.name not in ('.', '..') for p in paths) and
                cache_valid(d / 'content.json', cache_key, paths)):
                log(f'[CACHE] SHA-256検証済み正規化OBJ: {src.name}')
                warnings.extend(payload.get('warnings', []))
                return dest
        except (OSError, ValueError, TypeError, KeyError):
            pass
    # Only ever delete our own dedicated cache directory.
    if d.exists():
        import shutil
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    log(f'[OBJ] {role} {src.name} : 座標原点を減算中')
    local_warnings: list[str] = []
    staged = d / (src.name + '.part')
    try:
        normalize_obj(src, staged, origin, axis, local_warnings, cancel=cancel)
        if cancel: cancel.check()
        staged.replace(dest)
        outputs = [dest] + sorted(p for p in d.iterdir() if p.is_file() and p != dest and
                                   not p.name.endswith('.part') and p.name not in ('ready.json','content.json'))
        cache_mark(d / 'content.json', cache_key, outputs, {'source': str(src)})
        if cancel: cancel.check()
        metadata = {'fingerprint': fingerprint, 'warnings': local_warnings,
                    'generated': [p.name for p in outputs]}
        tmp = d / 'ready.json.part'
        tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(marker)
        warnings.extend(local_warnings)
        return dest
    except BaseException:
        # An incomplete write may remain in this cache directory, but it will
        # not be reused; previous completion markers have been removed.
        marker.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)




def _publish_verified_blend(stage: Path, output: Path, *, overwrite: bool) -> None:
    """Publish atomically. Default mode *cannot* overwrite a concurrently created file.

    Stage and destination are on the same filesystem (the .work directory is
    adjacent to output). Atomic hard-link creation provides O_EXCL semantics on
    NTFS/ext4/APFS; unlike os.replace(), it fails if output already exists.
    If the filesystem lacks hard-link support, preserve the staged Blender file
    for troubleshooting and explicitly refuse to bypass the no-overwrite rule.
    """
    if overwrite:
        os.replace(stage, output)
        return
    try:
        os.link(stage, output)
    except FileExistsError as exc:
        raise FileExistsError(f'完成直前に出力先が作成されました（上書き禁止）: {output}') from exc
    except OSError as exc:
        raise OSError(f'安全な出力確定ができません。保存先のファイルシステムがハードリンクをサポートするか確認してください。'
                      f'中間 .blend は維持します: {stage} / {exc}') from exc
    stage.unlink()

def run(config: Config, *, overwrite_output: bool = False, log: Log = print,
        allow_managed_inputs: bool = False, extra_provenance: dict | None = None,
        cancel: CancelToken | None = None) -> Path:
    if cancel: cancel.check()
    if config.output_blend.exists() and not overwrite_output:
        raise FileExistsError(f'出力が既にあります（上書き禁止）: {config.output_blend}')
    if not config.blender_executable.is_file():
        raise FileNotFoundError(f'Blender実行ファイルがありません: {config.blender_executable}')
    log('[CHECK] 入力・CRS・画像重複範囲の確認')
    p = validate(config, allow_managed_inputs=allow_managed_inputs)
    for warning in p['warnings']:
        log('[WARN] ' + warning)
    output = config.output_blend
    work = output.with_suffix('.work')
    work.mkdir(parents=True, exist_ok=True)
    warnings = list(p['warnings'])
    if cancel: cancel.check()
    staged_b = [_stage_obj(src, 'buildings', config.building_axis, p['origin'], work / 'cache', warnings, log, i, cancel=cancel)
                for i,src in enumerate(config.building_objs)]
    if cancel: cancel.check()
    staged_t = [_stage_obj(src, 'terrain', config.terrain_axis, p['origin'], work / 'cache', warnings, log, i, cancel=cancel)
                for i,src in enumerate(config.terrain_objs)]
    key = uuid.uuid4().hex[:12]
    stage_blend = work / f'building_{key}.blend'
    scene = {
        'version': 1, 'crs': config.epsg, 'origin_xyz': list(p['origin']),
        'buildings': [{'path': str(path), 'original_path': str(src),
                       'expected_bbox_local': [v - p['origin'][i%3] for i,v in enumerate(stat.bbox)],
                       'requires_diffuse_texture': has_used_diffuse_texture(path)}
                      for path,src,stat in zip(staged_b,config.building_objs,p['building_stats'])],
        'terrain': [{'path': str(path), 'original_path': str(src),
                     'expected_bbox_local': [v - p['origin'][i%3] for i,v in enumerate(stat.bbox)]}
                    for path,src,stat in zip(staged_t,config.terrain_objs,p['terrain_stats'])],
        'world_file_local': [p['world_file'].shifted(p['origin'][0],p['origin'][1]).__dict__[s]
                             for s in ('a','d','b','e','c','f')],
        'aerial_png': str(config.aerial_png), 'image_size': [p['image']['width'],p['image']['height']],
        'imagery_attribution': config.imagery_attribution,
        'imagery_terms_url': config.imagery_terms_url,
        'pack_image': config.pack_image, 'target_blend': str(stage_blend),
        'input_report': report(p), 'warnings': warnings,
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'acquisition': extra_provenance or {'mode':'manual'},
    }
    manifest = work / 'manifest.json'
    manifest.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding='utf-8')
    script = Path(__file__).with_name('blender_scene.py')
    command = [str(config.blender_executable), '--background', '--factory-startup',
               '--python-exit-code', '11', '--python', str(script), '--', str(manifest)]
    log('[BLENDER] バックグラウンド実行を開始')
    log('[CMD] ' + subprocess.list2cmdline(command))
    logpath = work / 'last_blender.log'
    run_logged(command,logpath,log,cancel=cancel)
    if cancel: cancel.check()
    if not stage_blend.exists() or stage_blend.stat().st_size < 7:
        raise RuntimeError(f'Blender出力が存在しません: {stage_blend}')
    with stage_blend.open('rb') as f:
        # compress=True saves zstd-wrapped .blend (Blender 3.0+); older gzip also exists.
        if not f.read(7).startswith(BLEND_MAGICS):
            raise RuntimeError('出力の先頭が.blend（BLENDER/zstd/gzip）ではありません（破損の可能性）')
    if cancel: cancel.check()
    # A valid BLENDER magic header does NOT prove the scene can be re-opened.
    # Check a second, independent Blender process before publishing the file.
    verify_path=work / f'verify_{key}.json'
    verify_script=Path(__file__).with_name('blender_verify.py')
    verify_command=[str(config.blender_executable),'--background','--factory-startup',
                    str(stage_blend),'--python-exit-code','12','--python',str(verify_script),
                    '--',str(verify_path)]
    log('[VERIFY] 保存済み .blend を別のBlenderプロセスで開き直して検証')
    run_logged(verify_command,work/'last_verify.log',log,cancel=cancel)
    if cancel: cancel.check()
    if not verify_path.is_file():
        raise RuntimeError('Blender再読込検証レポートがありません。last_verify.logを確認してください')
    try:
        validate_report(json.loads(verify_path.read_text(encoding='utf-8')),scene)
    except (ValueError,TypeError,KeyError) as exc:
        raise RuntimeError(f'再読込検証に失敗: {exc}') from exc
    log('[VERIFY] 建物・地形・UV・埋め込み画像・CRS・座標一致を確認')
    # Never replace the existing result unless caller explicitly requested it.
    if output.exists() and not overwrite_output:
        raise FileExistsError(f'処理中に出力が作られました: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    _publish_verified_blend(stage_blend, output, overwrite=overwrite_output)
    provenance = output.with_suffix('.provenance.json')
    provenance.write_text(json.dumps({**scene, 'final_blend': str(output)}, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f'[DONE] {output}')
    return output
