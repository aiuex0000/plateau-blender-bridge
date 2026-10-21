import math
import unittest
from plateau_blend.auto import center_condition


class CenterConditionTests(unittest.TestCase):
    def test_google_maps_copy_becomes_square_bbox(self):
        cond=center_condition('35.68960, 139.69170')
        west,south,east,north=map(float,cond[2:].split(','))
        self.assertAlmostEqual((west+east)/2,139.6917,places=6)
        self.assertAlmostEqual((south+north)/2,35.6896,places=6)
        self.assertAlmostEqual((north-south)*111_320,400,delta=1)
        self.assertAlmostEqual((east-west)*111_320*math.cos(math.radians(35.6896)),400,delta=1)

    def test_optional_size_and_japanese_comma(self):
        cond=center_condition('34.82、135.43、200')
        south,north=map(float,cond[2:].split(',')[1::2])
        self.assertAlmostEqual((north-south)*111_320,200,delta=1)

    def test_swapped_order_and_bad_size_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'緯度, 経度'):center_condition('139.6917, 35.6896')
        with self.assertRaisesRegex(ValueError,'一辺'):center_condition('35.6896, 139.6917, 10')
        with self.assertRaises(ValueError):center_condition('新宿駅')


if __name__=='__main__': unittest.main()
