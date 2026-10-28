import tempfile
import unittest
import tkinter as tk
from pathlib import Path
from unittest.mock import patch
from plateau_blend.auto import center_condition
from plateau_blend.auto_gui import (AutoGui, PLACE_PRESETS, free_output_path, user_error,
                                    stage_for, completion_message)
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
        self.assertEqual(gui.open_button.winfo_manager(),'pack')
        self.assertEqual(str(gui.open_button.cget('state')),'normal')


if __name__=='__main__': unittest.main()
