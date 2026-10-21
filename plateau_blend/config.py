"""Project config. All relative file paths are relative to JSON file location."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import re

@dataclass(frozen=True)
class Config:
    blender_executable: Path
    building_objs: tuple[Path, ...]
    terrain_objs: tuple[Path, ...]
    aerial_png: Path
    world_file: Path
    epsg: str
    image_epsg: str
    output_blend: Path
    building_axis: str = 'xyz'
    terrain_axis: str = 'xyz'
    imagery_attribution: str = ''
    imagery_terms_url: str = ''
    allow_partial_imagery: bool = False
    allow_suspect_imagery: bool = False
    pack_image: bool = True

    @classmethod
    def load(cls, json_path: Path) -> 'Config':
        json_path = json_path.resolve()
        data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        def file(s):
            p = Path(s).expanduser()
            return (json_path.parent / p).resolve() if not p.is_absolute() else p.resolve()
        required = ('blender_executable', 'aerial_png', 'world_file', 'output_blend', 'epsg', 'image_epsg')
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise ValueError('設定必須項目がありません: ' + ', '.join(missing))
        buildings = tuple(file(x) for x in data.get('building_objs', []))
        terrains = tuple(file(x) for x in data.get('terrain_objs', []))
        if not buildings or not terrains:
            raise ValueError('building_objsとterrain_objsは各1件以上必要です')
        for key in ('building_axis','terrain_axis'):
            if data.get(key, 'xyz') not in ('xyz', 'yxz'):
                raise ValueError(f'{key} は xyz / yxz のみ')
        epsg = str(data['epsg']).upper().strip()
        image_epsg = str(data['image_epsg']).upper().strip()
        if not re.fullmatch(r'EPSG:[1-9][0-9]+', epsg) or not re.fullmatch(r'EPSG:[1-9][0-9]+', image_epsg):
            raise ValueError('EPSG:6674 のようにEPSGコードを明示してください')
        if epsg != image_epsg:
            raise ValueError('MVPではOBJと航空写真のEPSG一致が必須。CRS変換は未実装です')
        paths = (*buildings, *terrains, file(data['aerial_png']), file(data['world_file']))
        if len(set(paths)) != len(paths):
            raise ValueError('重複した入力ファイルがあります')
        return cls(blender_executable=file(data['blender_executable']),
                   building_objs=buildings, terrain_objs=terrains,
                   aerial_png=file(data['aerial_png']), world_file=file(data['world_file']),
                   epsg=epsg, image_epsg=image_epsg, output_blend=file(data['output_blend']),
                   building_axis=data.get('building_axis', 'xyz'),
                   terrain_axis=data.get('terrain_axis', 'xyz'),
                   imagery_attribution=str(data.get('imagery_attribution', '')),
                   imagery_terms_url=str(data.get('imagery_terms_url', '')),
                   allow_partial_imagery=bool(data.get('allow_partial_imagery', False)),
                   allow_suspect_imagery=bool(data.get('allow_suspect_imagery', False)),
                   pack_image=bool(data.get('pack_image', True)))
