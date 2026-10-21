import tempfile
import unittest
from pathlib import Path
from plateau_blend.subset import subset_gml

GML = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="c" xmlns:bldg="b" xmlns:gml="g" xmlns:app="a">
\t<app:appearanceMember>
\t\t<app:Appearance>
\t\t\t<app:theme>rgbTexture</app:theme>
\t\t\t<app:surfaceDataMember>
\t\t\t\t<app:ParameterizedTexture>
\t\t\t\t\t<app:imageURI>m_appearance/near.jpg</app:imageURI>
\t\t\t\t\t<app:target uri="#poly_near"><app:TexCoordList>0 0</app:TexCoordList></app:target>
\t\t\t\t\t<app:target uri="#poly_far_shared"><app:TexCoordList>1 1</app:TexCoordList></app:target>
\t\t\t\t</app:ParameterizedTexture>
\t\t\t</app:surfaceDataMember>
\t\t\t<app:surfaceDataMember>
\t\t\t\t<app:ParameterizedTexture>
\t\t\t\t\t<app:imageURI>m_appearance/far.jpg</app:imageURI>
\t\t\t\t\t<app:target uri="#poly_far"><app:TexCoordList>0 0</app:TexCoordList></app:target>
\t\t\t\t</app:ParameterizedTexture>
\t\t\t</app:surfaceDataMember>
\t\t</app:Appearance>
\t</app:appearanceMember>
\t<core:cityObjectMember>
\t\t<bldg:Building gml:id="near"><gml:Polygon gml:id="poly_near"><gml:posList>34.8200 135.4300 10 34.8201 135.4301 10</gml:posList></gml:Polygon></bldg:Building>
\t</core:cityObjectMember>
\t<core:cityObjectMember>
\t\t<bldg:Building gml:id="far"><gml:Polygon gml:id="poly_far"><gml:posList>34.9000 135.5000 10 34.9001 135.5001 10</gml:posList></gml:Polygon>
\t\t<gml:Polygon gml:id="poly_far_shared"><gml:posList>34.9000 135.5000 10</gml:posList></gml:Polygon></bldg:Building>
\t</core:cityObjectMember>
</core:CityModel>
"""


class SubsetTests(unittest.TestCase):
    def test_keeps_nearby_buildings_and_only_their_texture_targets(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'m.gml'; src.write_text(GML,encoding='utf-8')
            before=src.read_bytes()
            out,report=subset_gml(src,Path(td)/'subset',(135.429,34.819,135.431,34.821))
            text=out.read_text(encoding='utf-8')
            self.assertEqual(src.read_bytes(),before)
            self.assertEqual((report['members_total'],report['members_kept'],report['textures_kept']),(2,1,1))
            self.assertIn('gml:id="near"',text); self.assertNotIn('gml:id="far"',text)
            self.assertIn('#poly_near',text); self.assertNotIn('#poly_far_shared',text)
            self.assertNotIn('far.jpg',text)
            self.assertTrue(text.rstrip().endswith('</core:CityModel>'))

    def test_gml_without_members_passes_through_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'m.gml'; src.write_text('<CityModel/>',encoding='utf-8')
            out,report=subset_gml(src,Path(td)/'subset',(135.429,34.819,135.431,34.821))
            self.assertEqual((out,report['members_total']),(src,0))

    def test_empty_selection_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'m.gml'; src.write_text(GML,encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'建物がありません'):
                subset_gml(src,Path(td)/'subset',(139.0,35.0,139.1,35.1))


if __name__=='__main__': unittest.main()
