"""World-file affine arithmetic. Georeferencing is in a single projected XY CRS."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math

@dataclass(frozen=True)
class WorldFile:
    a: float
    d: float
    b: float
    e: float
    c: float
    f: float

    @classmethod
    def read(cls, path: Path) -> 'WorldFile':
        values = [float(s.strip()) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
        if len(values) != 6 or not all(map(math.isfinite, values)):
            raise ValueError('ワールドファイルは有限な数値6行 (A,D,B,E,C,F) が必要です')
        v = cls(*values)
        if abs(v.det) < 1e-18:
            raise ValueError('PGWのアフィン行列が逆行列を持ちません')
        return v

    @property
    def det(self) -> float:
        return self.a * self.e - self.b * self.d

    def xy(self, col: float, row: float) -> tuple[float, float]:
        return (self.a * col + self.b * row + self.c,
                self.d * col + self.e * row + self.f)

    def pixel(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = x - self.c, y - self.f
        return ((self.e * dx - self.b * dy) / self.det,
                (-self.d * dx + self.a * dy) / self.det)

    def uv(self, x: float, y: float, width: int, height: int) -> tuple[float, float]:
        col, row = self.pixel(x, y)
        # PGW's C/F are the *centres* of the first pixel.
        return (col + 0.5) / width, 1.0 - (row + 0.5) / height

    def sample_coverage(self, bbox: tuple[float, float, float, float], width: int,
                        height: int, grid: int = 9) -> float:
        xmin, ymin, xmax, ymax = bbox
        inside = 0
        for iy in range(grid):
            for ix in range(grid):
                x = xmin + (xmax - xmin) * ix / (grid - 1)
                y = ymin + (ymax - ymin) * iy / (grid - 1)
                col, row = self.pixel(x, y)
                if -0.5 - 1e-6 <= col <= width - 0.5 + 1e-6 and -0.5 - 1e-6 <= row <= height - 0.5 + 1e-6:
                    inside += 1
        return inside / (grid * grid)

    def shifted(self, ox: float, oy: float) -> 'WorldFile':
        """Express the same affine in world XY minus common Blender origin."""
        return WorldFile(self.a, self.d, self.b, self.e, self.c - ox, self.f - oy)
