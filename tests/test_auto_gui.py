import tempfile
import unittest
import tkinter as tk
from pathlib import Path
from unittest.mock import patch
from plateau_blend.auto import center_condition
from plateau_blend.auto_gui import AutoGui, PLACE_PRESETS, free_output_path, user_error, stage_for
from plateau_blend.catalog import NoPlateauData


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


if __name__=='__main__': unittest.main()
