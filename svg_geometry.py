# svg_geometry.py
# Reads the final traced outline (the SVG that vtracer + export.py produce) back
# into polygon points, so the board builder is handed the real finished outline
# of each tool and not an earlier step.
#
# vtracer writes absolute M / L / C / Z commands. Each curve is sampled about
# every pixel (about 0.15 mm), so the polygon follows the traced curve to well
# under a hundredth of a millimetre.

from __future__ import annotations

import math
import re

import numpy as np

_TOKEN = re.compile(r"[MLCZmlczHhVvSsQqTtAa]|-?\d+\.?\d*(?:[eE][-+]?\d+)?")


def flatten_subpath(d: str, step_px: float = 1.0) -> np.ndarray:
    """One 'M ... Z' subpath -> (N, 2) points in the SVG's own (pixel) units.

    Raises ValueError for any command other than M, L, C, Z (the caller then
    says so instead of quietly using something else).
    """
    toks = _TOKEN.findall(d)
    pts: list[tuple[float, float]] = []
    cur = (0.0, 0.0)
    cmd = ""
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            if t not in ("M", "L", "C", "Z"):
                raise ValueError(f"unsupported SVG command '{t}'")
            cmd = t
            i += 1
            if cmd == "Z":
                continue
            continue
        if cmd in ("M", "L"):
            cur = (float(toks[i]), float(toks[i + 1]))
            pts.append(cur)
            i += 2
            cmd = "L"                       # extra pairs after M are line-tos
        elif cmd == "C":
            p1 = (float(toks[i]), float(toks[i + 1]))
            p2 = (float(toks[i + 2]), float(toks[i + 3]))
            p3 = (float(toks[i + 4]), float(toks[i + 5]))
            i += 6
            ctrl_len = (math.dist(cur, p1) + math.dist(p1, p2) + math.dist(p2, p3))
            n = int(min(200, max(4, math.ceil(ctrl_len / step_px))))
            u = np.linspace(0.0, 1.0, n + 1)[1:, None]
            a, b, c, e = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u ** 2 * (1 - u), u ** 3
            seg = a * np.array(cur) + b * np.array(p1) + c * np.array(p2) + e * np.array(p3)
            pts.extend(map(tuple, seg))
            cur = p3
        else:
            i += 1
    return np.array(pts, dtype=np.float64)


def flatten_paths(path_strings: list[str], step_px: float = 1.0) -> list[np.ndarray]:
    """Many 'M ... Z' subpaths (as export.tool_paths returns) -> list of rings."""
    rings = []
    for p in path_strings:
        r = flatten_subpath(p, step_px)
        if len(r) >= 3:
            rings.append(r)
    return rings
