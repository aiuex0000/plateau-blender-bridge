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
        self.assertFalse(api.running)

    def test_cancel_without_a_run_is_a_no_op(self):
        self.assertFalse(BridgeApi().cancel())


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
        with patch.object(web_gui, '_default_output', return_value=documents / 'scene.blend'):
            result = api.use_output_folder('documents', str(self.output))
        self.assertEqual(result['output'], str(documents / 'scene.blend'))

    def test_unwritable_app_folder_falls_back_to_documents(self):
        api = BridgeApi()
        blocked = Path(self._tmp.name) / 'blocked'
        blocked.write_bytes(b'')
        documents = Path(self._tmp.name) / 'Documents' / 'PLATEAU'
        with patch.object(web_gui, '_app_folder', return_value=blocked / '作った街'), \
             patch.object(web_gui, '_default_output', return_value=documents / 'scene.blend'):
            result = api.use_output_folder('app', str(self.output))
        self.assertIn('error', result)
        self.assertIn('ドキュメント', result['error'])
        self.assertEqual(result['output'], str(documents / 'scene.blend'))
        self.assertEqual(result['kind'], 'documents')

    def test_unknown_kind_is_refused(self):
        self.assertIsNone(BridgeApi().use_output_folder('other', 'scene.blend'))


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

    def test_session_outcome_uses_heartbeat_for_browser_and_process_for_edge(self):
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
        self.assertEqual(web_gui._session_outcome(
            200, 0, 10, process=process, process_alive=False), 'opened')
        self.assertEqual(web_gui._session_outcome(
            200, 0, None, process=process, process_alive=False), 'failed')
        self.assertEqual(web_gui._session_outcome(
            61, 0, None, process=process, process_alive=True), 'failed')

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


if __name__ == '__main__':
    unittest.main()
