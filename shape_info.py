# shape_info.py
# Quick, reliable geometry facts about a scanned tool, for the board-building
# app to use (alignment, finger cutouts, grouping similar tools).
#
# Everything here is plain maths on the outline: no guessing about which end is
# the "head". Where the answer is uncertain (a round socket has no direction, a
# symmetric tool can't tell head from handle) a flag says so, and the board
# builder can ask the user instead of trusting a guess.
#
# Coordinates: millimetres, x to the right, y DOWN (same as SVG), origin at the
# top-left corner of the tool's bounding box.

from __future__ import annotations

import math

import cv2
import numpy as np

# Outline is simplified to within this tolerance (mm) before saving.
SIMPLIFY_MM = 0.05

# The traced outline (already smooth curves) is only thinned of redundant points.
TRACE_SIMPLIFY_MM = 0.005

# Below this length/width ratio the long axis is not meaningful (round-ish tool).
MIN_ELONGATION = 1.25
# If the centre of mass sits this close to the middle of the tool (fraction of
# its length) we can't tell which end is which.
SYMMETRY_TOLERANCE = 0.03


def analyze_shape(outlines_px: list[np.ndarray], pixels_per_mm: float,
                  simplify_mm: float = SIMPLIFY_MM) -> dict:
    """Return polygon + measurements for the tool, all in millimetres.

    Args:
        outlines_px: outlines in photo pixels, with NO clearance added.
        pixels_per_mm: scale from the ArUco marker.
    """
    ppm = float(pixels_per_mm)
    rings_px = []
    for o in outlines_px:
        c = np.asarray(o, dtype=np.float32).reshape(-1, 1, 2)
        simp = cv2.approxPolyDP(c, simplify_mm * ppm, True).reshape(-1, 2)
        if len(simp) >= 3:
            rings_px.append(simp.astype(np.float64))
    if not rings_px:
        raise ValueError("outline has no usable shape")

    allpts = np.vstack(rings_px)
    origin = allpts.min(axis=0)
    rings_mm = [(r - origin) / ppm for r in rings_px]
    pts_mm = np.vstack(rings_mm)
    bbox_mm = pts_mm.max(axis=0)                      # (width, height) of bounding box

    # Largest piece drives orientation and centre of mass.
    main = max(rings_mm, key=lambda r: abs(_area(r)))
    area = sum(abs(_area(r)) for r in rings_mm)
    m = cv2.moments(main.astype(np.float32))
    if m["m00"] == 0:
        cx, cy = main.mean(axis=0)
        theta = 0.0
    else:
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        theta = 0.5 * math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"])   # long axis

    axis = np.array([math.cos(theta), math.sin(theta)])
    perp = np.array([-axis[1], axis[0]])
    along, across = pts_mm @ axis, pts_mm @ perp
    length = float(along.max() - along.min())
    width = float(across.max() - across.min())
    mid_along = float((along.max() + along.min()) / 2)
    centroid_along = float(np.array([cx, cy]) @ axis)
    offset = centroid_along - mid_along                # + means mass toward +axis end

    elong = length / width if width > 1e-6 else float("inf")
    round_info = _roundness(main, area_main=abs(_area(main)))
    angle = ((math.degrees(theta) + 90.0) % 180.0) - 90.0   # -90..+90, from +x toward +y (down)
    return {
        "polygon_mm": [np.round(r, 3).tolist() for r in rings_mm],
        "area_mm2": round(float(area), 2),
        "bbox_mm": [round(float(bbox_mm[0]), 2), round(float(bbox_mm[1]), 2)],
        "centroid_mm": [round(float(cx), 2), round(float(cy), 2)],
        "principal_axis_deg": round(angle, 2) + 0.0,
        "length_mm": round(length, 2),
        "width_mm": round(width, 2),
        "elongation": round(min(elong, 999.0), 2),
        "axis_reliable": bool(elong >= MIN_ELONGATION),
        "centroid_offset_along_axis_mm": round(offset, 2),
        "direction_ambiguous": bool(abs(offset) < SYMMETRY_TOLERANCE * max(length, 1e-6)),
        **round_info,
    }


def _roundness(ring: np.ndarray, area_main: float) -> dict:
    """How close the outline is to a circle (for the board builder's "make round").

    Added fields only; the outline itself is never changed here.
      equiv_diameter_mm         circle with the same area as the outline
      min_circle_diameter_mm    smallest circle that contains the whole outline
      circularity               4*pi*area / perimeter^2  (1.0 = perfect circle)
      max_radial_dev_mm         worst gap between the outline and the same-area
                                circle centred on the outline's centre of mass
    """
    pts = ring.astype(np.float32)
    # smooth the pixel staircase a little so the perimeter isn't inflated by noise
    smooth = cv2.approxPolyDP(pts.reshape(-1, 1, 2), 0.25, True)
    perim = float(cv2.arcLength(smooth, True))
    (_, _), r_min = cv2.minEnclosingCircle(pts.reshape(-1, 1, 2))
    r_eq = math.sqrt(area_main / math.pi) if area_main > 0 else 0.0
    m = cv2.moments(pts)
    if m["m00"]:
        c = np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]])
    else:
        c = pts.mean(axis=0)
    dev = float(np.abs(np.hypot(*(ring - c).T) - r_eq).max()) if len(ring) else 0.0
    circ = 4.0 * math.pi * area_main / (perim * perim) if perim > 1e-9 else 0.0
    return {
        "equiv_diameter_mm": round(2 * r_eq, 2),
        "min_circle_diameter_mm": round(2 * float(r_min), 2),
        "circularity": round(min(circ, 1.0), 4),
        "max_radial_dev_mm": round(dev, 2),
    }


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
