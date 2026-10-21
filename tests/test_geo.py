import math
import tempfile
import unittest
from pathlib import Path
from plateau_blend.geo import WorldFile


class WorldFileTests(unittest.TestCase):
    def test_pixel_uv_center_and_edges(self):
        wf = WorldFile(2, 0, 0, -2, 100, 200)
        self.assertEqual(wf.xy(0, 0), (100, 200))
        self.assertEqual(wf.pixel(104, 196), (2, 2))
        self.assertAlmostEqual(wf.uv(100, 200, 10, 20)[0], 0.05)
        self.assertAlmostEqual(wf.uv(100, 200, 10, 20)[1], 0.975)
        self.assertEqual(wf.sample_coverage((99, 181, 119, 201), 10, 10), 1.0)

    def test_sheared_rotated_inverse(self):
        wf = WorldFile(0.4, -0.1, 0.04, -0.4, 1234.4, 5678.8)
        for col, row in [(0,0),(400,300),(-3.2,91.23)]:
            x, y = wf.xy(col,row)
            c, r = wf.pixel(x,y)
            self.assertAlmostEqual(c, col, places=8)
            self.assertAlmostEqual(r, row, places=8)
        new = wf.shifted(1000, 5000)
        self.assertAlmostEqual(new.xy(400, 300)[0], wf.xy(400,300)[0]-1000)

    def test_pgw_order_and_singular(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d)/'photo.pgw'
            file.write_text('2\n0.1\n0.2\n-2\n100\n200\n')
            wf = WorldFile.read(file)
            self.assertEqual(wf, WorldFile(2,.1,.2,-2,100,200))
            file.write_text('1\n2\n2\n4\n100\n100\n')
            with self.assertRaises(ValueError): WorldFile.read(file)

if __name__=='__main__': unittest.main()
