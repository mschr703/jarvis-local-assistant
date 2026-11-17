"""Independently check the exported G07 STEP geometry and binary STL envelope."""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import build123d as bd
from OCP.BRepAdaptor import BRepAdaptor_Surface


def close(actual: float, expected: float, tolerance: float = 0.01) -> bool:
    return abs(actual - expected) <= tolerance


def verify_step(path: Path, dimensions: dict | None = None) -> dict:
    spec = dimensions or {"width": 60, "depth": 30, "thickness": 6,
                          "hole_diameter": 5, "hole_spacing": 40}
    width, depth, thickness = (spec[key] for key in ("width", "depth", "thickness"))
    diameter, spacing = spec["hole_diameter"], spec["hole_spacing"]
    shape = bd.import_step(str(path))
    bounds = shape.bounding_box().size
    assert all(close(value, expected) for value, expected in
               zip((bounds.X, bounds.Y, bounds.Z), (width, depth, thickness), strict=True))
    expected_volume = (width * depth - 2 * math.pi * (diameter / 2) ** 2) * thickness
    assert close(shape.volume, expected_volume)
    holes = []
    for face in shape.faces():
        if face.geom_type != bd.GeomType.CYLINDER:
            continue
        cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
        location = cylinder.Axis().Location()
        holes.append((location.X(), location.Y(), cylinder.Radius()))
    holes.sort()
    assert len(holes) == 2, holes
    assert all(close(value, expected) for hole, target in
               zip(holes, (-spacing / 2, spacing / 2), strict=True)
               for value, expected in zip(hole, (target, 0, diameter / 2), strict=True)), holes
    return {"bounds_mm": [bounds.X, bounds.Y, bounds.Z],
            "volume_mm3": shape.volume, "hole_axes_and_radii_mm": holes}


def verify_stl(path: Path, dimensions: dict | None = None) -> dict:
    spec = dimensions or {"width": 60, "depth": 30, "thickness": 6}
    data = path.read_bytes()
    assert len(data) >= 84, "STL too short"
    count = struct.unpack_from("<I", data, 80)[0]
    assert count >= 12 and len(data) == 84 + 50 * count, "invalid binary STL"
    limits = [[float("inf"), float("-inf")] for _ in range(3)]
    for index in range(count):
        offset = 84 + 50 * index
        for vertex in range(3):
            coordinates = struct.unpack_from("<fff", data, offset + 12 + 12 * vertex)
            for axis, value in enumerate(coordinates):
                assert math.isfinite(value)
                limits[axis][0] = min(limits[axis][0], value)
                limits[axis][1] = max(limits[axis][1], value)
    dimensions = [high - low for low, high in limits]
    assert all(close(value, expected, 0.1) for value, expected in
               zip(dimensions, (spec["width"], spec["depth"], spec["thickness"]), strict=True)), dimensions
    return {"triangles": count, "bounds_mm": dimensions}


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--single":
        fmt, path, spec = sys.argv[2], Path(sys.argv[3]), json.loads(sys.argv[4])
        if fmt not in {"step", "stl"}:
            raise SystemExit("Unsupported CAD export format")
        result = verify_step(path, spec) if fmt == "step" else verify_stl(path, spec)
        print(json.dumps(result))
    elif len(sys.argv) == 3:
        print(json.dumps({"step": verify_step(Path(sys.argv[1])),
                          "stl": verify_stl(Path(sys.argv[2]))}, indent=2))
    else:
        raise SystemExit("Usage: verify_holder.py STEP_FILE STL_FILE | --single FORMAT FILE DIMENSIONS_JSON")
