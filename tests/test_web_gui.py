"""BridgeApi の契約テスト。窓も Tk も作らず、重い処理はすべて差し替える。"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plateau_blend import web_gui
from plateau_blend.auto import center_condition
from plateau_blend.auto_gui import (BLENDER_DOWNLOAD_URL, CANCELLED_MESSAGE,
                                    PLACE_PRESETS, STAGES)
from plateau_blend.catalog import NoPlateauData
from plateau_blend.runtime import CancelledError
from plateau_blend.web_gui import BridgeApi, inspect_area, request_from_form


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / 'scene.blend'

    def form(self, **overrides):
        form = {
            'mode': 'center',
            'area': '35.6586, 139.7454',
            'size': '400',
            'output': str(self.output),
            'blender': '',
            'nusamai': '',
            'epsg': 'AUTO',
            'lod': 'textured_max_lod',
            'zoom': '18',
            'dem_res': '100',
            'axis': 'auto',
            'clip': True,
        }
        form.update(overrides)
        return form

    def collect(self, api, until, timeout=10.0):
        """poll() を回して until の kind が来るまで集める（窓の代わり）。"""
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events.extend(api.poll())
            if any(event['kind'] == until for event in events):
                return events
            time.sleep(0.01)
        self.fail(f'{until} イベントが来ませんでした: {events}')


class RequestFromFormTests(BridgeTestCase):
    def test_three_modes_follow_build_request_rules(self):
        center = request_from_form(self.form())
        self.assertEqual(center.condition,
                         center_condition('35.6586, 139.7454', default_size_m=400.0))
        self.assertEqual(center.output_blend, self.output.resolve())

        bbox = request_from_form(self.form(mode='bbox', area='139.74, 35.65, 139.75, 35.66'))
        self.assertEqual(bbox.condition, 'r:139.74,35.65,139.75,35.66')

        mesh = request_from_form(self.form(mode='mesh', area='5235 36'))
        self.assertEqual(mesh.condition, 'm:523536')

    def test_center_input_size_and_options(self):
        request = request_from_form(self.form(area='35.6586, 139.7454, 300', size='input'))
        self.assertEqual(request.condition,
                         center_condition('35.6586, 139.7454, 300', default_size_m=400.0))
        self.assertNotEqual(request.condition,
                            center_condition('35.6586, 139.7454', default_size_m=400.0))

        options = request_from_form(self.form(lod='min_lod', zoom='15', dem_res='60',
                                              axis='yxz', clip=False, epsg='EPSG:6674',
                                              blender=r'C:\Blender\blender.exe',
                                              nusamai=r'C:\tools\nusamai.exe'))
        self.assertEqual(options.lod, 'min_lod')
        self.assertEqual(options.photo_zoom, 15)
        self.assertEqual(options.terrain_resolution, 60)
        self.assertEqual(options.axis_override, 'yxz')
        self.assertFalse(options.clip_requested_area)
        self.assertEqual(options.blender_executable, Path(r'C:\Blender\blender.exe'))
        self.assertEqual(options.nusamai_executable, Path(r'C:\tools\nusamai.exe'))

    def test_center_card_size_overrides_the_third_number(self):
        card = request_from_form(self.form(area='35.6586, 139.7454, 300', size='200'))
        self.assertEqual(card.condition,
                         center_condition('35.6586, 139.7454', default_size_m=200))
        self.assertNotEqual(card.condition,
                            center_condition('35.6586, 139.7454, 300', default_size_m=200))

        keep = request_from_form(self.form(area='35.6586, 139.7454, 300', size='input'))
        self.assertEqual(keep.condition,
                         center_condition('35.6586, 139.7454, 300', default_size_m=400.0))

    def test_auto_name_is_opt_in_and_keeps_the_requested_path_by_default(self):
        requested = request_from_form(self.form(place_name='東京タワー'))
        self.assertEqual(requested.output_blend, self.output.resolve())

        named = request_from_form(self.form(place_name='東京タワー', auto_name=True))
        self.assertEqual(named.output_blend.name, '東京タワー_400m.blend')
        self.assertEqual(named.output_blend.parent, self.output.resolve().parent)

    def test_bad_input_raises_value_error(self):
        for form in (self.form(area=''), self.form(size='huge'), self.form(zoom='abc'),
                     self.form(mode='nonsense')):
            with self.subTest(form=form):
                with self.assertRaises(ValueError):
                    request_from_form(form)


class InspectAreaTests(BridgeTestCase):
    def test_plain_latlon_has_no_explicit_size(self):
        info = inspect_area(self.form(area='35.6586, 139.7454'))
        self.assertTrue(info['ok'])
        self.assertEqual(info['mode'], 'center')
        self.assertIsNone(info['explicit_size_m'])
        self.assertEqual(info['error'], '')

    def test_third_number_is_the_explicit_size(self):
        info = inspect_area(self.form(area='35.6586, 139.7454, 300'))
        self.assertTrue(info['ok'])
        self.assertEqual(info['explicit_size_m'], 300.0)

    def test_maps_url_zoom_is_not_a_size(self):
        info = inspect_area(self.form(
            area='https://www.google.com/maps/@35.6586,139.7454,17z'))
        self.assertTrue(info['ok'])
        self.assertIsNone(info['explicit_size_m'])

    def test_dms_has_no_explicit_size(self):
        info = inspect_area(self.form(area='35°41\'22.6"N 139°41\'30.1"E'))
        self.assertTrue(info['ok'])
        self.assertIsNone(info['explicit_size_m'])

    def test_empty_and_outside_japan_are_not_ok(self):
        empty = inspect_area(self.form(area=''))
        self.assertFalse(empty['ok'])
        self.assertIn('場所', empty['error'])

        far = inspect_area(self.form(area='48.85, 2.35'))
        self.assertFalse(far['ok'])
        self.assertTrue(far['error'])

    def test_bbox_and_mesh_forms(self):
        self.assertTrue(inspect_area(self.form(mode='bbox',
                                               area='139.74, 35.65, 139.75, 35.66'))['ok'])
        self.assertTrue(inspect_area(self.form(mode='mesh', area='5235 36'))['ok'])
        self.assertFalse(inspect_area(self.form(mode='mesh', area='abc'))['ok'])


class InitialStateTests(BridgeTestCase):
    def test_state_is_json_and_lists_the_tk_wording(self):
        state = BridgeApi().initial_state()
        json.dumps(state)
        self.assertEqual(len(state['places']), len(PLACE_PRESETS))
        self.assertEqual(len(state['stages']), 4)
        self.assertEqual(state['stages'][0]['text'], STAGES[0][1])
        self.assertEqual(state['default_size'], '400')
        self.assertTrue(state['default_output'].endswith('scene.blend'))
        self.assertEqual(state['version'], web_gui.__version__)
        self.assertIn('documents', state['outputs'])
        self.assertIn('app', state['outputs'])

    def test_module_does_not_import_tk_or_webview_at_load_time(self):
        source = Path(web_gui.__file__).read_text(encoding='utf-8')
        head = source.split('def main', 1)[0]
        self.assertNotIn('import tkinter', head)
        self.assertNotIn('import webview', head)


class ScanAndPlaceTests(BridgeTestCase):
    def test_scan_event_carries_both_executables(self):
        api = BridgeApi()
        blender = Path(r'C:\Blender\blender.exe')
        nusamai = Path(r'C:\tools\nusamai.exe')
        with patch.object(web_gui, 'blender_candidates', return_value=[blender]), \
             patch.object(web_gui, 'find_nusamai', return_value=nusamai):
            self.assertTrue(api.scan()['started'])
            events = self.collect(api, 'scan')
        data = [event for event in events if event['kind'] == 'scan'][0]['data']
        self.assertEqual(data['blender'], [str(blender)])
        self.assertEqual(data['nusamai'], str(nusamai))

    def test_check_place_reports_catalog_result(self):
        api = BridgeApi()
        source = SimpleNamespace(city='港区', year=2023, size=10 * 1048576)
        with patch.object(web_gui, 'preview', return_value=[source, source]):
            self.assertTrue(api.check_place(self.form())['started'])
            events = self.collect(api, 'place')
        data = [event for event in events if event['kind'] == 'place'][0]['data']
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'ok')
        self.assertIn('港区', data['text'])
        self.assertIn('2件', data['text'])

    def test_check_place_echoes_request_id_and_marks_missing_data(self):
        api = BridgeApi()
        with patch.object(web_gui, 'preview', side_effect=NoPlateauData()):
            reply = api.check_place(self.form(), 41)
            self.assertEqual(reply['request_id'], 41)
            events = self.collect(api, 'place')
        data = [event for event in events if event['kind'] == 'place'][0]['data']
        self.assertEqual(data['request_id'], 41)
        self.assertEqual(data['status'], 'nodata')
        self.assertFalse(data['ok'])
        self.assertIn('この場所にはデータがありません', data['text'])

    def test_check_place_does_not_call_network_failure_missing_data(self):
        api = BridgeApi()
        with patch.object(web_gui, 'preview', side_effect=OSError('通信できません')):
            api.check_place(self.form(), 7)
            events = self.collect(api, 'place')
        data = [event for event in events if event['kind'] == 'place'][0]['data']
        self.assertEqual(data['status'], 'unknown')
        self.assertEqual(data['request_id'], 7)
        self.assertNotIn('データがありません', data['text'])
        self.assertIn('通信', data['text'])


class PlanTests(BridgeTestCase):
    def test_plan_returns_a_free_name_for_an_existing_file(self):
        api = BridgeApi()
        fresh = api.plan(self.form())
        self.assertNotIn('error', fresh)
        self.assertFalse(fresh['renamed'])
        self.assertEqual(fresh['output'], str(self.output.resolve()))

        self.output.write_bytes(b'x')
        renamed = api.plan(self.form())
        self.assertTrue(renamed['renamed'])
        self.assertEqual(Path(renamed['output']).name, 'scene_2.blend')
        self.assertIn('scene_2.blend', renamed['rename_note'])

    def test_plan_reports_input_errors_as_text(self):
        self.assertIn('error', BridgeApi().plan(self.form(area='')))

    def test_plan_does_not_probe_a_network_output(self):
        # OneDrive/ネットワーク上で exists が固まると、画面が Failed to fetch になる。
        api = BridgeApi()
        plan = api.plan(self.form(output=r'\\server\share\scene.blend'))
        self.assertNotIn('error', plan)
        self.assertFalse(plan['renamed'])
        self.assertIn('ネットワーク', plan['note'])
        self.assertEqual(plan['output'], r'\\server\share\scene.blend')

    def test_plan_reports_the_actual_area(self):
        api = BridgeApi()
        self.assertEqual(api.plan(self.form(size='400'))['area'], '400m')
        self.assertEqual(api.plan(self.form(area='35.6586, 139.7454, 300',
                                            size='input'))['area'], '300m')
        self.assertEqual(api.plan(self.form(area='35.6586, 139.7454, 300',
                                            size='200'))['area'], '200m')
        self.assertEqual(api.plan(self.form(mode='bbox',
                                            area='139.74, 35.65, 139.75, 35.66'))['area'],
                         '入力した範囲')

    def test_plan_uses_the_preset_name_and_actual_size_for_auto_naming(self):
        api = BridgeApi()
        plan = api.plan(self.form(place_name='東京タワー', auto_name=True))
        self.assertEqual(plan['output_name'], '東京タワー_400m.blend')
        self.assertEqual(Path(plan['output']).parent, self.output.resolve().parent)

        explicit = api.plan(self.form(place_name='東京タワー',
                                      area='35.6586, 139.7454, 300',
                                      size='input', auto_name=True))
        self.assertEqual(explicit['output_name'], '東京タワー_300m.blend')

    def test_auto_name_keeps_scene_blend_for_unnamed_or_non_center_forms(self):
        api = BridgeApi()
        cases = [
            {},
            {'place_name': '東京タワー'},
            {'auto_name': True},
            {'auto_name': True, 'place_name': '東京タワー', 'mode': 'bbox',
             'area': '139.74, 35.65, 139.75, 35.66'},
            {'auto_name': True, 'place_name': '東京タワー', 'mode': 'mesh',
             'area': '5235 36'},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertEqual(api.plan(self.form(**overrides))['output_name'],
                                 'scene.blend')

    def test_auto_name_sanitizes_reserved_and_bounded_windows_names(self):
        api = BridgeApi()
        self.assertEqual(api.plan(self.form(place_name='東京/タワー',
                                            auto_name=True))['output_name'],
                         '東京_タワー_400m.blend')
        self.assertEqual(api.plan(self.form(place_name='CON.東京',
                                            auto_name=True))['output_name'],
                         'CON_東京_400m.blend')
        self.assertEqual(api.plan(self.form(place_name='...',
                                            auto_name=True))['output_name'],
                         'scene.blend')
        name = api.plan(self.form(place_name='東' * 200,
                                  auto_name=True))['output_name']
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith('_400m.blend'))
        self.assertIn('東', name)

    def test_explicit_name_wins_even_when_it_is_scene_blend(self):
        api = BridgeApi()
        self.assertEqual(api.plan(self.form(place_name='東京タワー',
                                            auto_name=False))['output_name'],
                         'scene.blend')
        self.assertEqual(api.plan(self.form(place_name='東京タワー', size='800',
                                            auto_name=False))['output_name'],
                         'scene.blend')
        named = Path(self._tmp.name) / '私の街.blend'
        self.assertEqual(api.plan(self.form(output=str(named), place_name='東京タワー',
                                            auto_name=False))['output_name'],
                         '私の街.blend')


class GenerateTests(BridgeTestCase):
    def test_second_start_is_ignored_while_running(self):
        api = BridgeApi()
        gate = threading.Event()
        entered = threading.Event()
        calls = []

        def fake_generate(request, *, log, cancel):
            calls.append(request)
            log('[CATALOG] テスト')
            entered.set()
            gate.wait(10)
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            first = api.start_generate(self.form())
            self.assertTrue(first['started'])
            self.assertTrue(entered.wait(10))
            second = api.start_generate(self.form(output=str(self.output) + '_other.blend'))
            self.assertFalse(second['started'])
            self.assertEqual(second.get('reason'), 'running')
            gate.set()
            self.collect(api, 'done')

        self.assertEqual(len(calls), 1)
        self.assertFalse(api.running)

    def test_two_threads_start_only_one_job(self):
        api = BridgeApi()
        gate = threading.Event()
        entered = threading.Event()
        calls = []

        def fake_generate(request, *, log, cancel):
            calls.append(request)
            entered.set()
            gate.wait(10)
            return Path(request.output_blend)

        replies = []

        def start(index):
            replies.append(api.start_generate(
                self.form(output=str(Path(self._tmp.name) / f'scene_{index}.blend'))))

        with patch.object(web_gui, 'generate', fake_generate):
            threads = [threading.Thread(target=start, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.assertTrue(entered.wait(10))
            gate.set()
            self.collect(api, 'done')

        self.assertEqual(sum(1 for reply in replies if reply.get('started')), 1)
        self.assertEqual(len(calls), 1)

    def test_stage_events_only_move_forward(self):
        api = BridgeApi()

        def fake_generate(request, *, log, cancel):
            for line in ('[CATALOG] 検索', '[WARN] 注意', '[CONVERTER] 変換',
                         '[GSI DEM] 地形', '[BLENDER] 保存'):
                log(line)
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            self.assertTrue(api.start_generate(self.form())['started'])
            events = self.collect(api, 'done')

        stages = [event['data']['stage'] for event in events if event['kind'] == 'stage']
        self.assertEqual(stages, sorted(stages))
        self.assertEqual(stages[0], 1)
        self.assertEqual(stages[-1], 4)

    def test_done_carries_job_id_and_saved_path(self):
        api = BridgeApi()

        def fake_generate(request, *, log, cancel):
            log('[CATALOG] 検索')
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            started = api.start_generate(self.form())
            self.assertTrue(started['started'])
            self.assertTrue(started['job'])
            events = self.collect(api, 'done')

        job_ids = {event.get('job_id') for event in events
                   if event['kind'] in ('stage', 'result', 'done')}
        self.assertEqual(job_ids, {started['job']})
        done = [event for event in events if event['kind'] == 'done'][0]['data']
        self.assertIsInstance(done, dict)
        self.assertEqual(done['output'], str(self.output.resolve()))

    def test_plan_and_generation_agree_on_the_automatic_name(self):
        api = BridgeApi()
        form = self.form(place_name='東京タワー', auto_name=True)
        plan = api.plan(form)
        seen = []

        def fake_generate(request, *, log, cancel):
            seen.append(request.output_blend)
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            started = api.start_generate(form)
            self.assertTrue(started['started'])
            events = self.collect(api, 'done')

        self.assertEqual(seen, [Path(plan['output'])])
        done = [event for event in events if event['kind'] == 'done'][0]['data']
        self.assertEqual(done['output'], plan['output'])

    def test_automatic_name_collision_is_resolved_in_plan_and_generation(self):
        api = BridgeApi()
        form = self.form(place_name='東京タワー', auto_name=True)
        first = api.plan(form)
        self.assertEqual(first['output_name'], '東京タワー_400m.blend')
        Path(first['output']).write_bytes(b'x')

        renamed = api.plan(form)
        self.assertTrue(renamed['renamed'])
        self.assertEqual(renamed['output_name'], '東京タワー_400m_2.blend')

        seen = []

        def fake_generate(request, *, log, cancel):
            seen.append(request.output_blend)
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            started = api.start_generate(form)
            self.assertTrue(started['started'])
            self.collect(api, 'done')

        self.assertEqual(seen, [Path(renamed['output'])])

    def test_cancel_surfaces_as_cancelled_not_error(self):
        api = BridgeApi()
        entered = threading.Event()

        def fake_generate(request, *, log, cancel):
            log('[CATALOG] 検索')
            entered.set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if cancel.cancelled:
                    raise CancelledError('中断')
                time.sleep(0.01)
            raise AssertionError('cancel was never requested')

        with patch.object(web_gui, 'generate', fake_generate):
            self.assertTrue(api.start_generate(self.form())['started'])
            self.assertTrue(entered.wait(10))
            self.assertTrue(api.cancel())
            events = self.collect(api, 'cancelled')

        self.assertFalse(any(event['kind'] == 'error' for event in events))
        data = [event for event in events if event['kind'] == 'cancelled'][0]['data']
        self.assertEqual(data, CANCELLED_MESSAGE)
        deadline = time.monotonic() + 2
        while api.running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(api.running)

    def test_cancel_without_a_run_is_a_no_op(self):
        self.assertFalse(BridgeApi().cancel())

    def test_cancel_shows_cancelled_before_the_worker_stops(self):
        api = BridgeApi()
        entered = threading.Event()
        release = threading.Event()

        def fake_generate(request, *, log, cancel):
            entered.set()
            release.wait(10)
            return Path(request.output_blend)

        with patch.object(web_gui, 'generate', fake_generate):
            self.assertTrue(api.start_generate(self.form())['started'])
            self.assertTrue(entered.wait(10))
            self.assertTrue(api.cancel())
            events = api.poll()
            # 画面には即時に中止を返すが、workerはまだ止まっていない。
            self.assertTrue(api.running)
            release.set()
            deadline = time.monotonic() + 2
            while api.running and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertTrue(any(event['kind'] == 'cancelled' for event in events))
        self.assertFalse(api.running)


class OpenUrlTests(BridgeTestCase):
    def test_only_maps_and_blender_are_opened(self):
        api = BridgeApi()
        with patch.object(web_gui.webbrowser, 'open', return_value=True) as opened:
            self.assertTrue(api.open_url('maps'))
            self.assertEqual(opened.call_args[0][0], web_gui.MAPS_URL)
            self.assertTrue(api.open_url('blender'))
            self.assertEqual(opened.call_args[0][0], BLENDER_DOWNLOAD_URL)
            self.assertFalse(api.open_url('file:///C:/Windows/System32'))
            self.assertFalse(api.open_url('https://example.com'))
            self.assertFalse(api.open_url(None))
        self.assertEqual(opened.call_count, 2)

    def test_maps_opens_the_point_only_inside_japan(self):
        api = BridgeApi()
        with patch.object(web_gui.webbrowser, 'open', return_value=True) as opened:
            self.assertTrue(api.open_url('maps', 35.6586, 139.7454))
            self.assertEqual(opened.call_args[0][0],
                             'https://www.google.com/maps/@35.6586,139.7454,16z')
            self.assertTrue(api.open_url('maps', 48.85, 2.35))
            self.assertEqual(opened.call_args[0][0], web_gui.MAPS_URL)
            self.assertTrue(api.open_url('maps', 'x', 'y'))
            self.assertEqual(opened.call_args[0][0], web_gui.MAPS_URL)


class OpenResultTests(BridgeTestCase):
    def setUp(self):
        super().setUp()
        self.api = BridgeApi()
        self.api._result = self.output

    def test_no_generated_result_returns_an_error(self):
        self.api._result = None
        result = self.api.open_result()
        self.assertFalse(result['ok'])
        self.assertIn('開く生成結果がありません', result['error'])

    def test_missing_result_file_returns_an_error_without_opening(self):
        with patch('plateau_blend.runtime.open_with_default_app') as opener:
            result = self.api.open_result()
        self.assertFalse(result['ok'])
        self.assertIn('保存した .blend が見つかりません', result['error'])
        self.assertIn(str(self.output), result['error'])
        opener.assert_not_called()

    def test_windows_opens_saved_path_with_the_default_app(self):
        output = self.output.with_name('東京の 街.blend')
        output.write_bytes(b'blend')
        self.api._result = output
        self.api._form = {'blender': str(output.with_name('old-blender.exe'))}
        with (patch('plateau_blend.runtime.sys.platform', 'win32'),
              patch('plateau_blend.runtime.os.startfile', create=True) as startfile):
            result = self.api.open_result()
        self.assertEqual(result, {'ok': True})
        startfile.assert_called_once_with(str(output))

    def test_missing_association_leaves_the_saved_file_and_folder_available(self):
        self.output.write_bytes(b'blend')
        error = OSError('No association')
        error.winerror = 1155
        with (patch('plateau_blend.runtime.sys.platform', 'win32'),
              patch('plateau_blend.runtime.os.startfile', create=True) as startfile):
            startfile.side_effect = [error, None]
            result = self.api.open_result()
            self.assertFalse(result['ok'])
            self.assertIn('既定のアプリで Blender を選んでください', result['error'])
            self.assertTrue(self.api.open_folder())
            startfile.assert_called_with(str(self.output.parent))
        self.assertEqual(self.output.read_bytes(), b'blend')

    def test_os_error_is_returned_to_the_caller(self):
        self.output.write_bytes(b'blend')
        with patch('plateau_blend.runtime.open_with_default_app',
                   side_effect=PermissionError('アクセス拒否')):
            result = self.api.open_result()
        self.assertFalse(result['ok'])
        self.assertIn('保存した .blend を開けませんでした', result['error'])
        self.assertIn('アクセス拒否', result['error'])


class BlenderStatusTests(BridgeTestCase):
    def test_only_an_existing_file_is_ok(self):
        api = BridgeApi()
        self.assertFalse(api.blender_status('')['ok'])
        self.assertFalse(api.blender_status(None)['ok'])
        self.assertFalse(api.blender_status(str(Path(self._tmp.name) / 'nope.exe'))['ok'])
        self.assertFalse(api.blender_status(str(Path(self._tmp.name)))['ok'])

        real = Path(self._tmp.name) / 'blender.exe'
        real.write_bytes(b'')
        status = api.blender_status(str(real))
        self.assertTrue(status['ok'])
        self.assertEqual(status['path'], str(real))


class OutputFolderTests(BridgeTestCase):
    def test_switches_to_the_app_folder_and_keeps_the_file_name(self):
        api = BridgeApi()
        app = Path(self._tmp.name) / '作った街'
        with patch.object(web_gui, '_app_folder', return_value=app):
            result = api.use_output_folder('app', str(self.output))
        self.assertNotIn('error', result)
        self.assertEqual(result['output'], str(app / 'scene.blend'))
        self.assertTrue(app.is_dir())

    def test_switches_to_documents(self):
        api = BridgeApi()
        documents = Path(self._tmp.name) / 'Documents' / 'PLATEAU'
        with patch.object(web_gui, '_documents_output', return_value=documents / 'scene.blend'):
            result = api.use_output_folder('documents', str(self.output))
        self.assertEqual(result['output'], str(documents / 'scene.blend'))

    def test_unwritable_app_folder_falls_back_to_documents(self):
        api = BridgeApi()
        blocked = Path(self._tmp.name) / 'blocked'
        blocked.write_bytes(b'')
        documents = Path(self._tmp.name) / 'Documents' / 'PLATEAU'
        with patch.object(web_gui, '_app_folder', return_value=blocked / '作った街'), \
             patch.object(web_gui, '_documents_output', return_value=documents / 'scene.blend'):
            result = api.use_output_folder('app', str(self.output))
        self.assertIn('error', result)
        self.assertIn('ドキュメント', result['error'])
        self.assertEqual(result['output'], str(documents / 'scene.blend'))
        self.assertEqual(result['kind'], 'documents')

    def test_default_is_the_app_folder_when_writable(self):
        app = Path(self._tmp.name) / '作った街'
        with patch.object(web_gui, '_app_folder', return_value=app):
            self.assertEqual(web_gui._default_kind(), 'app')
            self.assertEqual(web_gui._default_output(), app / 'scene.blend')
        self.assertFalse(app.exists())  # 既定を決めるだけでは作らない

    def test_default_skips_an_app_folder_name_taken_by_a_file(self):
        taken = Path(self._tmp.name) / '作った街'
        taken.write_bytes(b'')
        documents = Path(self._tmp.name) / 'Documents' / 'PLATEAU' / 'scene.blend'
        with patch.object(web_gui, '_app_folder', return_value=taken), \
             patch.object(web_gui, '_documents_output', return_value=documents):
            self.assertEqual(web_gui._default_kind(), 'documents')

    def test_default_falls_back_to_documents_when_the_app_folder_is_read_only(self):
        blocked = Path(self._tmp.name) / 'blocked'
        blocked.write_bytes(b'')
        documents = Path(self._tmp.name) / 'Documents' / 'PLATEAU' / 'scene.blend'
        with patch.object(web_gui, '_app_folder', return_value=blocked / '作った街'), \
             patch.object(web_gui, '_documents_output', return_value=documents):
            self.assertEqual(web_gui._default_kind(), 'documents')
            self.assertEqual(web_gui._default_output(), documents)

    def test_unknown_kind_is_refused(self):
        self.assertIsNone(BridgeApi().use_output_folder('other', 'scene.blend'))

    def test_slow_documents_are_used_without_a_write_probe(self):
        # OneDrive へリダイレクトされたドキュメント（Office PC 実機）。確認で HTTP を止めない。
        api = BridgeApi()
        documents = Path(self._tmp.name) / 'OneDrive' / 'Documents' / 'PLATEAU'
        with patch.object(web_gui, '_documents_output', return_value=documents / 'scene.blend'), \
             patch.object(web_gui.app_paths, 'is_slow_path', return_value=True), \
             patch.object(web_gui.app_paths, 'writable_quick',
                          side_effect=AssertionError('probe must be skipped')):
            result = api.use_output_folder('documents', str(self.output))
        self.assertNotIn('error', result)
        self.assertEqual(result['output'], str(documents / 'scene.blend'))
        self.assertIn('warning', result)

    def test_probe_timeout_is_used_with_a_warning(self):
        api = BridgeApi()
        app = Path(self._tmp.name) / '作った街'
        with patch.object(web_gui, '_app_folder', return_value=app), \
             patch.object(web_gui.app_paths, 'writable_quick', return_value=None):
            result = api.use_output_folder('app', str(self.output))
        self.assertEqual((result['kind'], result['output']), ('app', str(app / 'scene.blend')))
        self.assertIn('warning', result)

    def test_switch_errors_are_returned_not_raised(self):
        api = BridgeApi()
        with patch.object(web_gui, '_app_folder', side_effect=OSError('boom')):
            result = api.use_output_folder('app', str(self.output))
        self.assertIn('boom', result['error'])

    def test_default_does_not_probe_a_slow_app_folder(self):
        app = Path(self._tmp.name) / 'OneDrive' / '作った街'
        with patch.object(web_gui, '_app_folder', return_value=app), \
             patch.object(web_gui.app_paths, 'is_slow_path', return_value=True), \
             patch.object(web_gui.app_paths, 'writable_quick',
                          side_effect=AssertionError('probe must be skipped')):
            self.assertEqual(web_gui._default_kind(), 'documents')


class SmokeTestTests(BridgeTestCase):
    def test_smoke_result_is_reported(self):
        api = BridgeApi()
        target = Path(self._tmp.name) / 'diagnostics' / 'smoke.blend'
        with patch.object(web_gui, 'run_smoke', return_value=target):
            self.assertTrue(api.smoke_test(self.form()))
            events = self.collect(api, 'done')
        results = [event['data'] for event in events if event['kind'] == 'smoke_result']
        self.assertEqual(results, [str(target)])
        self.assertFalse(api.running)


class FakeDialogs:
    """tkinter を開かずに、ファイル選択の依頼内容と戻り値だけを差し替える。"""

    def __init__(self, save_result=None, open_result=None):
        self.save_result = save_result
        self.open_result = open_result
        self.save_calls = []
        self.open_calls = []

    def save(self, directory, filename, file_types):
        self.save_calls.append((directory, filename, list(file_types)))
        return self.save_result

    def open(self, directory, file_types):
        self.open_calls.append((directory, list(file_types)))
        return self.open_result


class DialogTests(BridgeTestCase):
    def test_choose_output_appends_blend_and_opens_the_current_folder(self):
        api = BridgeApi()
        dialogs = FakeDialogs(save_result=str(Path(self._tmp.name) / 'chosen'))
        api._dialogs = dialogs

        picked = api.choose_output(str(self.output))

        self.assertEqual(picked, str(Path(self._tmp.name) / 'chosen') + '.blend')
        directory, filename, file_types = dialogs.save_calls[0]
        self.assertEqual(Path(directory), self.output.parent)
        self.assertEqual(filename, 'scene.blend')
        self.assertIn(('Blender (*.blend)', '*.blend'), file_types)

    def test_choose_output_keeps_an_existing_blend_extension(self):
        api = BridgeApi()
        api._dialogs = FakeDialogs(save_result=str(self.output))
        self.assertEqual(api.choose_output(), str(self.output))

    def test_choose_file_uses_the_open_dialog_for_both_kinds(self):
        api = BridgeApi()
        path = str(Path(self._tmp.name) / 'blender.exe')
        dialogs = FakeDialogs(open_result=path)
        api._dialogs = dialogs

        self.assertEqual(api.choose_file('blender'), path)
        self.assertEqual(api.choose_file('nusamai'), path)
        self.assertIsNone(api.choose_file('other'))
        self.assertEqual(len(dialogs.open_calls), 2)
        self.assertIn(('Windows executable (*.exe)', '*.exe'), dialogs.open_calls[0][1])

    def test_save_log_writes_the_chosen_file(self):
        api = BridgeApi()
        target = Path(self._tmp.name) / 'bridge-log.txt'
        dialogs = FakeDialogs(save_result=str(target))
        api._dialogs = dialogs
        api._log('保存される行')

        self.assertEqual(api.save_log(), str(target))
        self.assertIn('保存される行', target.read_text(encoding='utf-8'))
        self.assertEqual(dialogs.save_calls[0][1], 'plateau_bridge_log.txt')
        self.assertIn(('テキスト (*.txt)', '*.txt'), dialogs.save_calls[0][2])

    def test_without_dialogs_the_selection_paths_return_none(self):
        api = BridgeApi()
        self.assertIsNone(api.choose_output(str(self.output)))
        self.assertIsNone(api.choose_file('blender'))
        self.assertIsNone(api.save_log())

    def test_a_broken_dialog_is_logged_and_returns_none(self):
        class Broken(FakeDialogs):
            def save(self, directory, filename, file_types):
                raise RuntimeError('窓が開かない')

        api = BridgeApi()
        api._dialogs = Broken()
        self.assertIsNone(api.choose_output(str(self.output)))
        events = api.poll()
        self.assertTrue(any(event['kind'] == 'log' and '窓が開かない' in event['data']
                            for event in events))


class EdgeAndFallbackTests(BridgeTestCase):
    def test_find_edge_prefers_the_program_files_candidate(self):
        edge = Path(self._tmp.name) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
        edge.parent.mkdir(parents=True)
        edge.write_bytes(b'')
        registry_calls = []

        found = web_gui._find_edge(
            environment={'ProgramFiles(x86)': '', 'ProgramFiles': self._tmp.name},
            registry_lookup=lambda: registry_calls.append(True))

        self.assertEqual(found, edge)
        self.assertEqual(registry_calls, [])

    def test_find_edge_falls_back_to_the_registry(self):
        fallback = Path(self._tmp.name) / 'reg' / 'msedge.exe'
        found = web_gui._find_edge(environment={}, registry_lookup=lambda: fallback)
        self.assertEqual(found, fallback)

    def test_edge_args_open_an_app_window_without_sync_or_extensions(self):
        args = web_gui._edge_args(Path('msedge.exe'), 'http://127.0.0.1:1/?t=x',
                                  Path('prof'), 900, 780)
        self.assertEqual(args[0], 'msedge.exe')
        self.assertIn('--app=http://127.0.0.1:1/?t=x', args)
        self.assertIn(f'--user-data-dir={Path("prof")}', args)
        self.assertIn('--disable-sync', args)
        self.assertIn('--disable-extensions', args)
        self.assertIn('--window-size=900,780', args)

    def test_session_outcome_uses_process_only_before_first_contact(self):
        self.assertEqual(web_gui._session_outcome(10, 0, None), 'wait')
        self.assertEqual(web_gui._session_outcome(61, 0, None), 'failed')
        self.assertEqual(web_gui._session_outcome(100, 0, 10), 'wait')
        self.assertEqual(web_gui._session_outcome(200, 0, 10), 'closed')

        process = object()
        self.assertEqual(web_gui._session_outcome(
            100, 0, 10, process=process, process_alive=True), 'wait')
        # Edge が生きていても心拍が途切れたら閉じる（本体は起動し直した別プロセスのことが多い）
        self.assertEqual(web_gui._session_outcome(
            200, 0, 10, process=process, process_alive=True), 'closed')
        # 最初の連絡の後に起動プロセスが抜けても（本体への引き渡し）終わらない。心拍と bye で見る
        self.assertEqual(web_gui._session_outcome(
            10.2, 0, 10.1, process=process, process_alive=False), 'wait')
        self.assertEqual(web_gui._session_outcome(
            100, 0, 10, process=process, process_alive=False), 'wait')
        self.assertEqual(web_gui._session_outcome(
            200, 0, 10, process=process, process_alive=False), 'closed')
        self.assertEqual(web_gui._session_outcome(
            14.5, 0, 10, process=process, process_alive=False, bye_at=11), 'closed')
        self.assertEqual(web_gui._session_outcome(
            200, 0, None, process=process, process_alive=False), 'failed')
        self.assertEqual(web_gui._session_outcome(
            61, 0, None, process=process, process_alive=True), 'failed')

    def test_session_outcome_keeps_the_server_when_the_page_was_served(self):
        # 画面は出ている。ここで止めると窓だけ残り、全操作が Failed to fetch になる。
        self.assertEqual(web_gui._session_outcome(61, 0, None, page_served_at=5), 'wait')
        self.assertEqual(web_gui._session_outcome(
            61, 0, None, process=object(), process_alive=False, page_served_at=5), 'wait')
        self.assertEqual(web_gui._session_outcome(181, 0, None, page_served_at=5), 'failed')
        self.assertEqual(web_gui._session_outcome(61, 0, None, page_served_at=None), 'failed')

    def test_server_ready_does_not_count_as_the_page(self):
        api = BridgeApi()
        web = Path(self._tmp.name) / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<html></html>', encoding='utf-8')
        (web / 'app.js').write_text('', encoding='utf-8')
        from plateau_blend.web_server import BridgeServer
        server = BridgeServer(api, web_dir=web)
        server.start()
        self.addCleanup(server.stop)
        self.assertTrue(web_gui._server_ready(server.port, server.token))
        self.assertIsNone(server.page_served_at)

    def test_session_outcome_closes_after_bye_unless_contact_returns(self):
        # 閉じた: 最後の連絡 10 → bye 11 → 3秒戻らない
        self.assertEqual(web_gui._session_outcome(13, 0, 10, bye_at=11), 'wait')
        self.assertEqual(web_gui._session_outcome(14.5, 0, 10, bye_at=11), 'closed')
        # 再読込: bye 11 の後に連絡 12 が戻った
        self.assertEqual(web_gui._session_outcome(30, 0, 12, bye_at=11), 'wait')
        # Edge が生きていても bye で閉じる（起動し直しで本体が別プロセスになるため）
        self.assertEqual(web_gui._session_outcome(
            14.5, 0, 10, process=object(), process_alive=True, bye_at=11), 'closed')

    def test_fallback_log_is_written_under_the_logs_folder(self):
        logs = Path(self._tmp.name) / 'logs'
        with patch.object(web_gui, '_fallback_dir', return_value=logs):
            written = web_gui.write_fallback_log('理由です', 'traceです')
        self.assertIsNotNone(written)
        text = written.read_text(encoding='utf-8')
        self.assertIn('理由です', text)
        self.assertIn('traceです', text)


class GenerateRecoveryTests(BridgeTestCase):
    def error_data(self, exc):
        api = BridgeApi()

        def fake_generate(request, *, log, cancel):
            raise exc

        with patch.object(web_gui, 'generate', fake_generate):
            self.assertTrue(api.start_generate(self.form())['started'])
            events = self.collect(api, 'error')
        return [event['data'] for event in events if event['kind'] == 'error'][0]

    def test_missing_data_points_to_the_place_step(self):
        data = self.error_data(NoPlateauData())
        self.assertEqual(data['recovery'], 'place')
        self.assertEqual(data['message'], str(NoPlateauData()))

    def test_output_write_permission_points_to_the_save_step(self):
        for target in (self.output, self.output.parent, self.output.with_suffix('.work') / 'auto' / 'city.gml'):
            with self.subTest(target=target):
                data = self.error_data(PermissionError(13, 'denied', str(target)))
                self.assertEqual(data['recovery'], 'output')
                self.assertTrue(data['message'])

    def test_disk_full_at_output_is_also_output(self):
        data = self.error_data(OSError(28, 'no space', str(self.output)))
        self.assertEqual(data['recovery'], 'output')

    def test_unrelated_permission_is_unknown(self):
        target = self.output.parent / 'unrelated-tool.exe'
        data = self.error_data(PermissionError(13, 'denied', str(target)))
        self.assertEqual(data['recovery'], 'unknown')


class RangePreviewTests(BridgeTestCase):
    """固定の地理院タイルプレビュー。取得や生成には触れない。"""

    def test_center_400_and_800_agree_with_their_bbox_geometry(self):
        api = BridgeApi()
        spans = []
        for size in (400, 800):
            with self.subTest(size=size):
                form = self.form(size=str(size))
                center = api.range_preview(form)
                area = request_from_form(form).condition.removeprefix('r:')
                box = api.range_preview(self.form(mode='bbox', area=area))
                self.assertTrue(center['ok'])
                self.assertEqual({**center, 'mode': 'bbox'}, box)
                bounds = center['bounds']
                spans.append(bounds['east'] - bounds['west'])
        self.assertAlmostEqual(spans[1], 2 * spans[0], delta=0.00001)

    def test_tiles_are_capped_and_positions_are_valid(self):
        result = BridgeApi().range_preview(
            self.form(mode='bbox', area='139.70,35.60,139.90,35.80'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['viewport'], {'width': 512, 'height': 320})
        self.assertLessEqual(len(result['tiles']), 9)
        self.assertGreater(len(result['tiles']), 0)
        limit = 1 << result['zoom']
        for tile in result['tiles']:
            self.assertEqual(tile['z'], result['zoom'])
            self.assertIsInstance(tile['x'], int)
            self.assertIsInstance(tile['y'], int)
            self.assertTrue(0 <= tile['x'] < limit)
            self.assertTrue(0 <= tile['y'] < limit)
        bounds = result['bounds']
        self.assertLess(bounds['west'], bounds['east'])
        self.assertLess(bounds['south'], bounds['north'])

    def test_mesh_has_no_rectangle_preview(self):
        result = BridgeApi().range_preview(self.form(mode='mesh', area='5235 36'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['supported'])
        self.assertIn('メッシュ', result['error'])

    def test_invalid_bounds_return_a_friendly_error(self):
        api = BridgeApi()
        reversed_box = api.range_preview(
            self.form(mode='bbox', area='139.75,35.65,139.74,35.66'))
        self.assertFalse(reversed_box['ok'])
        self.assertTrue(reversed_box['error'])
        bad = api.range_preview(self.form(mode='bbox', area='139.74,35.65,nan,35.66'))
        self.assertFalse(bad['ok'])
        self.assertIn('有限', bad['error'])
        outside = api.range_preview(self.form(mode='bbox', area='139,85,140,86'))
        self.assertFalse(outside['ok'])
        self.assertIn('表示範囲外', outside['error'])


if __name__ == '__main__':
    unittest.main()
