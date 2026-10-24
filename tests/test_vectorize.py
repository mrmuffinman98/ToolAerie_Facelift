# tests/test_vectorize.py
# Unit tests for vectorize.py
#
# Run from the project root:
#   python -m pytest tests/test_vectorize.py -v

import sys
import os
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import vectorize


def _make_circle_mask(size: int = 200, radius: int = 80) -> np.ndarray:
    """Create a binary mask with a filled white circle on black background."""
    import cv2
    mask = np.zeros((size, size), dtype=np.uint8)
    centre = (size // 2, size // 2)
    cv2.circle(mask, centre, radius, 255, thickness=-1)
    return mask


def _make_rectangle_mask(
    width: int = 300,
    height: int = 200,
    rect: tuple = (50, 50, 200, 100),
) -> np.ndarray:
    """Create a binary mask with a filled white rectangle on black background."""
    mask = np.zeros((height, width), dtype=np.uint8)
    x, y, w, h = rect
    mask[y : y + h, x : x + w] = 255
    return mask


class TestBitmapToPaths:
    def test_returns_path_object(self):
        """bitmap_to_paths should return a Potrace Path without error."""
        mask = _make_circle_mask()
        path = vectorize.bitmap_to_paths(mask, pixels_per_mm=10.0)
        # Should be truthy (has at least one curve).
        assert path is not None

    def test_circle_produces_curves(self):
        """A circle mask should produce at least one smooth curve."""
        mask = _make_circle_mask()
        path = vectorize.bitmap_to_paths(mask, pixels_per_mm=10.0)
        curves = list(path.curves)
        assert len(curves) >= 1, "Expected at least one curve for a circle"

    def test_rectangle_produces_curves(self):
        """A rectangle mask should produce at least one curve."""
        mask = _make_rectangle_mask()
        path = vectorize.bitmap_to_paths(mask, pixels_per_mm=10.0)
        curves = list(path.curves)
        assert len(curves) >= 1

    def test_scale_applied(self):
        """Coordinates should be in mm, not pixels, after scaling."""
        pixels_per_mm = 10.0
        mask = _make_rectangle_mask(width=300, height=200, rect=(50, 50, 200, 100))
        path = vectorize.bitmap_to_paths(mask, pixels_per_mm=pixels_per_mm)

        # In the original mask the rectangle spans ~200 pixels wide → 20 mm.
        # Curve start point x should be well under 300 (pixel space max).
        for curve in path.curves:
            assert curve.start_point.x < 300 / pixels_per_mm + 5, (
                "Coordinate looks like it is still in pixel space, not mm"
            )
            break  # only need to check one

    def test_empty_mask_does_not_crash(self):
        """An all-black mask should return a path with zero curves gracefully."""
        mask = np.zeros((100, 100), dtype=np.uint8)
        # Potrace on an empty bitmap returns a path with no curves — should not raise.
        path = vectorize.bitmap_to_paths(mask, pixels_per_mm=10.0)
        curves = list(path.curves)
        assert len(curves) == 0

    def test_different_scale_factors(self):
        """Changing pixels_per_mm should scale coordinates proportionally."""
        mask = _make_circle_mask()

        path_5 = vectorize.bitmap_to_paths(mask.copy(), pixels_per_mm=5.0)
        path_10 = vectorize.bitmap_to_paths(mask.copy(), pixels_per_mm=10.0)

        curves_5 = list(path_5.curves)
        curves_10 = list(path_10.curves)

        if curves_5 and curves_10:
            x5 = curves_5[0].start_point.x
            x10 = curves_10[0].start_point.x
            # At 5 px/mm coordinates should be roughly double those at 10 px/mm.
            assert abs(x5 / x10 - 2.0) < 0.5, (
                f"Scale ratio unexpected: x5={x5:.2f}, x10={x10:.2f}"
            )
