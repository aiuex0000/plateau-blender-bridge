import tempfile
import unittest
from pathlib import Path
from plateau_blend.obj import inspect_obj, normalize_obj, overlaps_xy


class OBJTests(unittest.TestCase):
    def test_large_coords_and_swap_normals_winding(self):
        with tempfile.TemporaryDirectory() as d:
            src,dst = Path(d)/'test.obj',Path(d)/'dest'/ 'test.obj'
            content = 'v 1300000.125 250000.125 30\nv 1300001 250000 30\nv 1300002 250000 30\nvn 1 0 0\nf 1//1 2//1 3//1\n'
            src.write_text(content)
            stats=inspect_obj(src,'yxz')
            self.assertEqual(stats.bbox[0],250000.)
            warnings=[]
            n=normalize_obj(src,dst,(250000,1300000,0),'yxz',warnings)
            self.assertEqual(n,3)
            text=dst.read_text()
            self.assertIn('v 0.125 0.125 30',text)
            self.assertIn('vn 0 1 0',text)
            self.assertIn('f 3//1 2//1 1//1',text)
            self.assertIn('1300000.125',src.read_text())

    def test_empty_and_nonoverlap(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'empty.obj';path.write_text('f 1 2 3\n')
            with self.assertRaises(ValueError): inspect_obj(path)
        self.assertFalse(overlaps_xy((0,0,0,10,10,0),(20,20,0,30,30,0)))

if __name__=='__main__': unittest.main()

class MaterialsTests(unittest.TestCase):
    def test_relative_parent_material_and_texture_are_staged_safely(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'input').mkdir();(root/'resources').mkdir()
            image=root/'resources'/'texture with spaces.png'
            image.write_bytes(b'not a real PNG, only a copy-test fixture')
            mtl=root/'resources'/'test.mtl'
            mtl.write_text('newmtl test\nmap_Kd texture with spaces.png\n')
            src=root/'input'/'model.obj'
            src.write_text('mtllib ../resources/test.mtl\nv 100 200 1\nv 101 200 1\nv 100 201 1\nusemtl test\nf 1 2 3\n')
            dest=root/'output'/'model.obj'
            warnings=[]
            normalize_obj(src,dest,(100,200,0),'xyz',warnings)
            mat_line=dest.read_text().splitlines()[0]
            self.assertTrue(mat_line.startswith('mtllib material_'))
            staged_mtl=dest.parent/mat_line.removeprefix('mtllib ')
            self.assertTrue(staged_mtl.is_file())
            image_line=staged_mtl.read_text().splitlines()[1]
            self.assertTrue(image_line.startswith('map_Kd texture_'))
            self.assertEqual((dest.parent/image_line.removeprefix('map_Kd ')).read_bytes(),image.read_bytes())
            self.assertEqual(warnings, [])
            self.assertIn('mtllib ../resources/test.mtl',src.read_text())


class MissingTextureTests(unittest.TestCase):
    def test_missing_and_resolved_map(self):
        from plateau_blend.obj import missing_material_images
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            (folder/'x.obj').write_text('mtllib material.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            (folder/'material.mtl').write_text('newmtl test\nmap_Kd imagery.png\n')
            self.assertEqual(len(missing_material_images(folder/'x.obj')),1)
            (folder/'imagery.png').write_bytes(b'stub')
            self.assertEqual(missing_material_images(folder/'x.obj'),[])
