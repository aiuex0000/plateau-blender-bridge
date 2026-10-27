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

    def test_bad_size_and_text_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'一辺'):center_condition('35.6896, 139.6917, 10')
        with self.assertRaisesRegex(ValueError,'新宿駅'):center_condition('新宿駅')
        with self.assertRaisesRegex(ValueError,'日本'):center_condition('48.85, 2.35')

    def same_center(self, text):
        west,south,east,north=map(float,center_condition(text)[2:].split(','))
        self.assertAlmostEqual((south+north)/2,35.6896,places=3)
        self.assertAlmostEqual((west+east)/2,139.6917,places=3)

    def test_lenient_forms_from_real_pastes(self):
        self.same_center('139.6917, 35.6896')                      # lon first
        self.same_center('３５．６８９６，　１３９．６９１７')            # full-width IME input
        self.same_center('(35.6896, 139.6917)')
        self.same_center('35°41\'22.6"N 139°41\'30.1"E')           # Google Maps DMS
        self.same_center('https://www.google.com/maps/@35.6896,139.6917,17z')

    def test_swap_is_reported_not_silent(self):
        # Master decided (2026-10-27): swap lon-first input, and show that it was swapped.
        notes=[]
        center_condition('139.6917, 35.6896',notes=notes)
        self.assertEqual(notes,['経度・緯度の順と判断して、緯度 35.6896・経度 139.692 として使います'])
        notes=[]
        center_condition('35.6896, 139.6917',notes=notes)
        self.assertEqual(notes,[])

    def test_four_numbers_are_a_rectangle(self):
        # Master pasted the old r: range into the centre box (2026-10-21).
        self.assertEqual(center_condition('139.690,35.686,139.694,35.690'),'r:139.690000,35.686000,139.694000,35.690000')
        self.assertEqual(center_condition('r:139.690,35.686,139.694,35.690'),'r:139.690000,35.686000,139.694000,35.690000')
        self.assertEqual(center_condition('35.686,139.690,35.690,139.694'),'r:139.690000,35.686000,139.694000,35.690000')


if __name__=='__main__': unittest.main()
