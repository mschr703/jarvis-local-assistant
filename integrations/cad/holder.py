"""G07 workshop pilot: a versioned, parametric two-hole mounting plate.

Run with the pinned local text-to-cad runtime: cad run integrations/cad/holder.py.
All dimensions are in millimetres. Change the constants, then rebuild.
"""

from __future__ import annotations

import math

import build123d as bd
from cad import model

MODEL_VERSION = "g07-holder-v1"
WIDTH_MM = 60.0
DEPTH_MM = 30.0
THICKNESS_MM = 6.0
HOLE_DIAMETER_MM = 5.0
HOLE_SPACING_MM = 40.0


def holder_body(width: float, depth: float, thickness: float,
                hole_diameter: float, hole_spacing: float) -> bd.Part:
    if (min(width, depth, thickness, hole_diameter) <= 0
            or hole_spacing <= 0
            or hole_spacing + hole_diameter >= width
            or hole_diameter >= depth):
        raise ValueError("Haltermaße oder Bohrungsabstand sind ungültig")
    plate = bd.Box(width, depth, thickness)
    for x in (-hole_spacing / 2, hole_spacing / 2):
        plate -= bd.Pos(x, 0, 0) * bd.Cylinder(hole_diameter / 2, thickness * 2)
    bounds = plate.bounding_box().size
    expected_volume = (width * depth - 2 * math.pi * (hole_diameter / 2) ** 2) * thickness
    if (abs(bounds.X - width) > 0.001 or abs(bounds.Y - depth) > 0.001
            or abs(bounds.Z - thickness) > 0.001
            or abs(plate.volume - expected_volume) > 0.01):
        raise ValueError("Erzeugte Geometrie weicht von den Maßen ab")
    plate.label = f"Halter mit zwei Bohrungen ({MODEL_VERSION})"
    plate.color = "#4a6fa5"
    return plate


@model
def holder():
    return holder_body(WIDTH_MM, DEPTH_MM, THICKNESS_MM,
                       HOLE_DIAMETER_MM, HOLE_SPACING_MM)


if __name__ == "__main__":
    holder()
