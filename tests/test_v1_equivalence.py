"""The new app must give exactly the same result as the original v1 pipeline.

The original path is main.py's: process.process_image() -> vectorize -> export.
The app wraps the SAME functions (it only caches the undistort lookup tables and
lets settings be changed on screen), so for the same photo and same settings the
mask, the scale and the saved SVG must be identical.
"""
import cv2
import numpy as np
import pytest

import camera
import config
import export
import process
import scan_service as ss
import vectorize
from session import Session


@pytest.fixture(params=[0, 1, 2, 3])
def photo(request, tmp_path):
    """A JPEG on disk, like a Pi capture (JPEG compression and all)."""
    cam = camera.DemoCamera()
    for _ in range(request.param):
        cam.next_tool()
    path = tmp_path / "capture.jpg"
    cv2.imwrite(str(path), cam.frame(), [cv2.IMWRITE_JPEG_QUALITY, 95])
    return str(path)


def test_undistort_and_crop_identical(photo):
    img = cv2.imread(photo)
    v1 = process.crop(process.undistort(img))
    new = ss.prepare(img)
    assert np.array_equal(v1, new)


@pytest.mark.parametrize("offset", [0.0, 1.5])
def test_mask_and_scale_identical(photo, offset, monkeypatch):
    monkeypatch.setattr(config, "OUTLINE_OFFSET_MM", offset)
    v1_mask, v1_ppm = process.process_image(photo)               # original path
    new = ss.scan(cv2.imread(photo), ss.Settings(offset_mm=offset))
    assert new.pixels_per_mm == v1_ppm
    assert np.array_equal(new.mask, v1_mask)


def test_saved_svg_identical(photo, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTLINE_OFFSET_MM", 1.5)
    monkeypatch.setattr(config, "SVG_OUTPUT_PATH", str(tmp_path / "v1"))
    mask, ppm = process.process_image(photo)
    v1_svg = export.svg_to_file(vectorize.bitmap_to_svg_string(mask), ppm,
                                output_filename="v1.svg")

    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    s = Session("Drawer 1")
    rec = s.add_tool(ss.scan(cv2.imread(photo), ss.Settings(offset_mm=1.5)))
    new_svg = s.folder / rec.svg_file
    assert open(v1_svg).read() == new_svg.read_text()
