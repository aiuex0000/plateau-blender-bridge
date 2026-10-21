import tempfile
import unittest
from pathlib import Path
from plateau_blend.converter import command, yup_to_zup


class NusamaiObjLayoutTests(unittest.TestCase):
    def test_command_keeps_surveyed_coordinates(self):
        argv=command(Path('nusamai.exe'),[Path('a.gml')],Path('out'),'min_lod',split=False)
        self.assertIn('center_at_origin=false',argv)
        self.assertEqual(argv[argv.index('center_at_origin=false')-1],'-o')

    def test_yup_obj_is_rotated_to_zup_without_touching_faces(self):
        # nusamai writes [x, z, -y]: easting -52000, height 12, northing -130000.
        with tempfile.TemporaryDirectory() as td:
            obj=Path(td)/'bldg.obj'
            obj.write_text('mtllib bldg.mtl\nv -52000 12 130000\nvt 0.5 0.25\nvn 0 1 0\nusemtl m\nf 1/1/1\n',encoding='utf-8')
            self.assertEqual(yup_to_zup(obj),1)
            lines=obj.read_text(encoding='utf-8').splitlines()
            self.assertEqual(lines[1],'v -52000 -130000 12')
            self.assertEqual(lines[2],'vt 0.5 0.25')
            self.assertEqual(lines[3],'vn 0 -0 1')
            self.assertEqual(lines[5],'f 1/1/1')
            self.assertFalse(list(Path(td).glob('*.tmp')))


if __name__=='__main__': unittest.main()
