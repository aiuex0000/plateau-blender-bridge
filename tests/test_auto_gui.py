import tempfile
import unittest
import tkinter as tk
from pathlib import Path
from unittest.mock import patch
from plateau_blend.auto import center_condition
from plateau_blend.auto_gui import (AutoGui, PLACE_PRESETS, free_output_path, user_error,
                                    stage_for, completion_message, initial_geometry,
                                    CANCELLED_MESSAGE, BLENDER_DOWNLOAD_URL)
from plateau_blend.catalog import NoPlateauData
from plateau_blend.runtime import enable_dpi_awareness
from plateau_blend import __version__


class AutoGuiHelperTests(unittest.TestCase):
    def test_presets_are_valid_center_conditions(self):
        for name,coordinates,size in PLACE_PRESETS:
            with self.subTest(name=name):
                center_condition(coordinates)
                self.assertIn(size,('200','400','800'))

    def test_selecting_and_editing_presets(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        self.assertEqual(str(gui.preset.cget('state')),'readonly')
        self.assertEqual(gui.preset.get(),'')
        for index,(name,coordinates,size) in enumerate(PLACE_PRESETS):
            with self.subTest(name=name):
                gui.mode.set('mesh');gui.update_area()
                gui.place_info.set('previous result')
                output=gui.output_var.get()
                gui.preset.current(index)
                gui.preset.event_generate('<<ComboboxSelected>>')
                self.assertEqual((gui.mode.get(),gui.area.get(),gui.size.get()),('center',coordinates,size))
                self.assertEqual(gui.preset.get(),name)
                self.assertEqual(gui.place_info.get(),'')
                self.assertEqual(gui.output_var.get(),output)
                gui.area_entry.insert('end','0')
                self.assertEqual(gui.preset.get(),'')

    def test_stage_prefixes_and_monotonic_progress(self):
        groups=(
            ('[CATALOG]','[PLAN]','[CRS]'),
            ('[CONVERTER]','[SUBSET]','[CLIP]','[DEDUP]','[AXIS]','[BBOX]','[TEXTURE example]'),
            ('[GSI DEM]','[GSI PHOTO]'),
            ('[BLENDER]','[OBJ]','[VERIFY]','[CHECK]'),
        )
        for expected,prefixes in enumerate(groups,1):
            for prefix in prefixes:
                with self.subTest(prefix=prefix):
                    self.assertEqual(stage_for(prefix+' message',1),expected)
                    self.assertEqual(stage_for(prefix+' message',4),4)
        for line in ('[CACHE]','[WARN]','[CMD]','[DONE]',' [BLENDER]','ordinary text'):
            for current in range(1,5):
                with self.subTest(line=line,current=current):
                    self.assertEqual(stage_for(line,current),current)

    def test_existing_output_gets_a_free_name(self):
        # The default Documents/PLATEAU/scene.blend already existed, so a first run stopped (2026-10-27).
        with tempfile.TemporaryDirectory() as td:
            scene=Path(td)/'scene.blend'
            self.assertEqual(free_output_path(scene),scene)
            scene.write_bytes(b'x')
            self.assertEqual(free_output_path(scene).name,'scene_2.blend')
            (Path(td)/'scene_2.blend').write_bytes(b'x')
            self.assertEqual(free_output_path(scene).name,'scene_3.blend')

    def test_no_data_message_is_plain(self):
        self.assertTrue(user_error(NoPlateauData()).startswith('この場所には'))
        self.assertEqual(user_error(OSError('boom')),'OSError: boom')

    def test_completion_message_names_the_saved_file(self):
        text=completion_message(Path(r'C:\Users\You\Documents\PLATEAU\scene_2.blend'))
        self.assertIn('作成できました',text)
        self.assertIn('scene_2.blend',text)

    def test_dpi_awareness_helper_never_raises(self):
        self.assertIsInstance(enable_dpi_awareness(),bool)

    def test_version_is_a_release_number(self):
        parts=__version__.split('.')
        self.assertEqual(len(parts),3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_save_destination_shows_file_name_first(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        self.assertEqual(gui.output_name.get(),'scene.blend')
        gui.output_var.set(r'D:\街\新宿.blend')
        self.assertEqual(gui.output_name.get(),'新宿.blend')
        # The full path stays reachable from the advanced panel entry.
        self.assertEqual(gui.output_entry.get(),r'D:\街\新宿.blend')
        # Before a successful run there is nothing to open, so the button stays hidden.
        self.assertEqual(gui.open_button.winfo_manager(),'')

    def test_generation_completion_names_the_output(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        target=Path(r'C:\Users\You\Documents\PLATEAU\scene_3.blend')
        with patch('plateau_blend.auto_gui.messagebox.showinfo') as info:
            gui.running=True
            gui.queue.put(('result',str(target)))
            gui.queue.put(('done','完了'))
            gui.poll()
        self.assertIn('作成できました',gui.status.get())
        self.assertIn('scene_3.blend',gui.status.get())
        self.assertEqual(info.call_count,1)
        self.assertIn('scene_3.blend',info.call_args[0][1])
        self.assertEqual(gui.open_button.winfo_manager(),'grid')
        self.assertEqual(gui.open_button.grid_info()['columnspan'],2)
        self.assertEqual(str(gui.open_button.cget('state')),'normal')

    def test_cancel_is_reported_as_stopped_not_failed(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        with patch('plateau_blend.auto_gui.messagebox.showinfo') as info, \
             patch('plateau_blend.auto_gui.messagebox.showerror') as error:
            gui.running=True
            gui.queue.put(('cancelled',CANCELLED_MESSAGE))
            gui.poll()
        error.assert_not_called()
        self.assertEqual(info.call_args[0][0],'中止しました')
        self.assertNotIn('Error',gui.status.get())
        self.assertNotIn('失敗',gui.status.get())
        self.assertFalse(gui.running)
        self.assertEqual(str(gui.generate_button.cget('state')),'normal')

    def test_initial_window_fits_small_screens(self):
        self.assertEqual(initial_geometry(1920,1080),'900x780')
        self.assertEqual(initial_geometry(1366,768),'900x638')
        self.assertEqual(initial_geometry(1024,600),'900x580')

    def test_radio_group_is_one_tab_stop_with_arrow_keys(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        # Tk's traversal calls the takefocus command with the widget path appended.
        stops=[r for r in gui.size_radios if int(gui.tk.call(r.cget('takefocus'),str(r)))]
        self.assertEqual(stops,[gui.size_radios[1]])
        self.assertEqual(gui.size.get(),'400')
        self.assertTrue(gui.size_radios[1].bind('<Right>'))
        gui.step_radio(gui.size_radios,gui.size,['200','400','800','input'],1)
        self.assertEqual(gui.size.get(),'800')
        gui.step_radio(gui.size_radios,gui.size,['200','400','800','input'],-1)
        gui.step_radio(gui.size_radios,gui.size,['200','400','800','input'],-1)
        self.assertEqual(gui.size.get(),'200')
        gui.step_radio(gui.size_radios,gui.size,['200','400','800','input'],1)
        gui.step_radio(gui.size_radios,gui.size,['200','400','800','input'],1)
        # The Tab stop follows the new choice.
        self.assertEqual(int(gui.tk.call(gui.size_radios[2].cget('takefocus'),str(gui.size_radios[2]))),1)

    def test_existing_dir_climbs_to_a_real_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(AutoGui.existing_dir(Path(tmp)/'missing'/'deeper'),tmp)

    def test_blender_link_is_hidden_until_blender_is_missing(self):
        try:
            with patch.object(AutoGui,'scan_dependencies'):
                gui=AutoGui()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(gui.destroy)
        gui.withdraw()
        self.assertEqual(gui.blender_link_button.winfo_manager(),'')
        gui.queue.put(('scan',([],Path('n.exe'))))
        gui.poll()
        self.assertEqual(gui.blender_link_button.winfo_manager(),'grid')
        self.assertIn('公式サイト',gui.status.get())
        with patch('plateau_blend.auto_gui.webbrowser.open') as open_url:
            gui.blender_link_button.invoke()
        open_url.assert_called_once_with(BLENDER_DOWNLOAD_URL)
        gui.blender_var.set(r'C:\b\blender.exe')
        self.assertEqual(gui.blender_link_button.winfo_manager(),'')


if __name__=='__main__': unittest.main()
