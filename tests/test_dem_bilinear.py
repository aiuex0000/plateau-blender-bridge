import unittest
from pathlib import Path
from PIL import Image
from plateau_blend.gsi import GsiTiles, fractional_tile


def dem_tile(value_cm):
    v=value_cm & 0xFFFFFF
    return Image.new('RGB',(256,256),((v>>16)&255,(v>>8)&255,v&255))


class FakeTiles(GsiTiles):
    def __init__(self, tiles):
        super().__init__(Path('.'))
        self.fake=tiles

    def get(self,layer,z,x,y,required=False):
        return self.fake.get((layer,x,y))


class BilinearDemTests(unittest.TestCase):
    def setUp(self):
        self.tx,self.ty=map(int,fractional_tile(139.692,35.688,14))

    def lonlat(self, gx, gy):
        # Inverse of fractional_tile for a global pixel coordinate at z14.
        import math
        n=2**14
        lon=(gx/256)/n*360-180
        lat=math.degrees(math.atan(math.sinh(math.pi*(1-2*(gy/256)/n))))
        return lon,lat

    def test_interpolates_across_tile_border_instead_of_stepping(self):
        tiles=FakeTiles({('dem_png',self.tx,self.ty):dem_tile(3000),('dem_png',self.tx+1,self.ty):dem_tile(4000)})
        # Halfway between the last pixel centre of the left tile and the first of the right one.
        lon,lat=self.lonlat((self.tx+1)*256,self.ty*256+100.5)
        self.assertAlmostEqual(tiles.height(lon,lat),35.0,places=2)
        lon,lat=self.lonlat((self.tx+1)*256-0.25,self.ty*256+100.5)
        self.assertAlmostEqual(tiles.height(lon,lat),32.5,places=2)

    def test_no_data_neighbour_keeps_nearest_value(self):
        tiles=FakeTiles({('dem_png',self.tx,self.ty):dem_tile(3000),('dem_png',self.tx+1,self.ty):dem_tile(0x800000)})
        lon,lat=self.lonlat((self.tx+1)*256-0.25,self.ty*256+100.5)
        self.assertAlmostEqual(tiles.height(lon,lat),30.0,places=2)


class TerrainTriangulationTests(unittest.TestCase):
    def test_every_triangle_stays_in_its_cell_and_faces_up(self):
        import tempfile
        from plateau_blend.gsi import make_terrain

        class Flat:
            cancel=None
            def height(self,lon,lat,zoom): return 10.0

        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'t.obj'
            # Sheared grid (i-1,j+1) left 1 sliver per row spanning the whole width: Shinjuku stripes.
            make_terrain(Flat(),(0,0,30,20),'EPSG:6677',out,resolution=3,log=lambda *_:None)
            lines=out.read_text().splitlines()
        vs=[tuple(map(float,l.split()[1:3])) for l in lines if l.startswith('v ')]
        faces=[[int(x)-1 for x in l.split()[1:]] for l in lines if l.startswith('f ')]
        self.assertEqual({i for f in faces for i in f},set(range(len(vs))))
        cell=10.0
        for f in faces:
            (x0,y0),(x1,y1),(x2,y2)=(vs[i] for i in f)
            self.assertLessEqual(max(x0,x1,x2)-min(x0,x1,x2),cell+1e-6)
            self.assertLessEqual(max(y0,y1,y2)-min(y0,y1,y2),cell+1e-6)
            self.assertGreater((x1-x0)*(y2-y0)-(y1-y0)*(x2-x0),0)


if __name__=='__main__': unittest.main()
