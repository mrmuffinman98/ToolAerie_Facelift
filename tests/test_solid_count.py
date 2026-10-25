"""Solid outlines, tool count, and roundness fields."""
import cv2
import numpy as np
import pytest

import camera
import config
import scan_service as ss
import shape_info
import solidify
from session import Session


def _big_tool_image():
    """Bright board, a big dark tool with a bright slot in it."""
    img = np.full((1096, 2004, 3), 220, np.uint8)
    cv2.ellipse(img, (900, 500), (420, 260), 0, 0, 360, (40, 40, 40), -1)
    cv2.ellipse(img, (860, 480), (110, 30), 30, 0, 360, (220, 220, 220), -1)   # slot
    return img


def test_already_solid_mask_is_left_exactly_alone():
    img = _big_tool_image()
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.ellipse(mask, (900, 500), (420, 260), 0, 0, 360, 255, -1)
    out, changed = solidify.make_solid(img, mask)
    assert not changed
    assert out is mask


def test_hollow_edge_band_becomes_solid():
    img = _big_tool_image()
    band = np.zeros(img.shape[:2], np.uint8)                  # what a gappy band looks like
    cv2.ellipse(band, (900, 500), (409, 249), 0, 0, 360, 255, 22)   # band just inside the tool edge
    band[:, 1250:1330] = 0                                    # the gap
    solid_in, _ = cv2.findContours(band, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled_by_original_logic = np.zeros_like(band)
    cv2.drawContours(filled_by_original_logic, solid_in, -1, 255, -1)
    out, changed = solidify.make_solid(img, filled_by_original_logic)
    true_area = np.pi * 420 * 260
    assert changed
    assert abs((out > 0).sum() - true_area) / true_area < 0.03
    cnts, _ = cv2.findContours(out, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    assert len(cnts) == 1


def _two_tool_frame():
    cam = camera.DemoCamera()
    a = cam._render(0)                       # wrench
    b = cam._render(1)                       # screwdriver
    tool_b = (b.min(axis=2) < 120)
    tool_b[650:830, 380:600] = False         # not the marker
    tool_b = np.roll(tool_b, 260, axis=0)
    b = np.roll(b, 260, axis=0)
    out = a.copy()
    out[tool_b] = b[tool_b]
    return out


def _quiet(f, *a, **k):
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        return f(*a, **k)


def test_tool_count_two_keeps_two_and_numbers_left_to_right():
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=2))
    assert len(r.parts) == 2 and r.found == 2
    xs = [np.vstack(p.outlines)[:, 0].mean() for p in r.parts]
    assert xs == sorted(xs)
    assert not any("only" in w for w in r.warnings)


def test_tool_count_one_never_returns_more_than_one():
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=1))
    assert len(r.parts) == 1 and r.found == 2
    assert len(r.ignored_outlines) == 1
    assert any("Ignored 1 other piece" in w for w in r.warnings)


def test_tool_count_too_high_warns_but_does_not_invent_tools():
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=4))
    assert len(r.parts) == 2
    assert any("You said 4" in w for w in r.warnings)


def test_add_scan_saves_each_tool_and_undo_removes_the_whole_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=2, offset_mm=1.0))
    s = Session("Drawer", root=str(tmp_path))
    recs = _quiet(s.add_scan, r, "wrench")
    assert [x.name for x in recs] == ["wrench 1", "wrench 2"]
    assert len(s.tools) == 2
    for rec in recs:
        assert (s.folder / rec.svg_file).exists()
        assert len(rec.geometry["polygon_mm"]) == 1           # one solid ring each
    removed = s.remove_last()
    assert len(removed) == 2 and not s.tools


def test_roundness_of_a_socket():
    cam = camera.DemoCamera()
    for _ in range(3):
        cam.next_tool()                      # socket
    r = _quiet(ss.scan, cam.frame(), ss.Settings())
    g = shape_info.analyze_shape(r.raw_outlines, r.pixels_per_mm)
    assert g["circularity"] > 0.95
    assert g["equiv_diameter_mm"] == pytest.approx(26.0, abs=1.0)
    assert g["max_radial_dev_mm"] < 0.6
    assert g["min_circle_diameter_mm"] >= g["equiv_diameter_mm"] - 0.1


def test_roundness_of_a_long_tool_is_low():
    cam = camera.DemoCamera()
    r = _quiet(ss.scan, cam.frame(), ss.Settings())
    g = shape_info.analyze_shape(r.raw_outlines, r.pixels_per_mm)
    assert g["circularity"] < 0.5


def test_masks_and_steps_are_saved_and_kept_out_of_the_package(tmp_path, monkeypatch):
    import zipfile
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(config, "SAVE_STEPS", True)
    frame = _two_tool_frame()
    img = ss.prepare(frame)
    r = _quiet(ss.analyze, img, ss.Settings(expected=2, offset_mm=1.0))
    r.raw_frame = frame
    s = Session("Drawer", root=str(tmp_path))
    recs = _quiet(s.add_scan, r, "")
    for rec in recs:                                           # pure black/white mask per tool
        m = cv2.imread(str(s.folder / rec.mask_file), cv2.IMREAD_UNCHANGED)
        assert set(np.unique(m)) <= {0, 255} and (m == 255).any()
    steps = s.folder / "steps" / "scan_01"
    names = {p.name for p in steps.iterdir()}
    assert {"1_raw_camera.png", "2_corrected_cropped.png", "3_edge_threshold.png",
            "4_original_mask.png", "5_solid_mask.png", "info.json"} <= names
    assert any(n.startswith("6_final_mask_") for n in names)
    assert any(n.startswith("7_overlay_") for n in names)

    dest = tmp_path / "saved" / "Drawer"
    s.save_to(dest)
    assert (dest / "steps" / "scan_01" / "info.json").exists()      # in the folder...
    with zipfile.ZipFile(next(dest.glob("*.toolaerie"))) as z:      # ...but not in the one-file package
        assert not any(n.startswith("steps/") for n in z.namelist())
        assert any(n.startswith("masks/") for n in z.namelist())
    s.remove_last()
    assert not steps.exists()
    assert not list((s.folder / "masks").iterdir())


def test_steps_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(config, "SAVE_STEPS", False)
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=2))
    s = Session("Drawer", root=str(tmp_path))
    _quiet(s.add_scan, r, "")
    assert not (s.folder / "steps").exists()
    assert list((s.folder / "masks").iterdir())               # final masks are always kept


def test_builder_outline_comes_from_the_final_traced_svg(tmp_path, monkeypatch):
    """polygon_mm must be the saved outlines/*.svg (vtracer + export), read back."""
    import re
    import export
    import svg_geometry
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    img = ss.prepare(_two_tool_frame())
    r = _quiet(ss.analyze, img, ss.Settings(expected=2, offset_mm=1.5))
    s = Session("Drawer", root=str(tmp_path))
    recs = _quiet(s.add_scan, r, "")
    for rec in recs:
        g = rec.geometry
        assert g["source"].startswith("final traced svg")
        assert abs(g["trace_edge_shift_mm"]) < 0.3
        # read the saved file back from disk and rebuild the polygon: must be the same shape
        svg = (s.folder / rec.outline_file).read_text()
        d = re.search(r'<path[^>]* d="([^"]+)"', svg).group(1)
        paths = [p.strip() + " Z" for p in re.split(r"Z\s*", d) if p.strip()]
        rings = svg_geometry.flatten_paths(paths)
        again = shape_info.analyze_shape(rings, rec.pixels_per_mm,
                                         simplify_mm=shape_info.TRACE_SIMPLIFY_MM)
        assert again["polygon_mm"] == g["polygon_mm"]
        # the laser-ready file keeps the clearance; the outline file does not
        assert rec.svg_file != rec.outline_file
        assert g["area_mm2"] < rec.width_mm * rec.height_mm


def test_svg_reader_handles_curves_and_lines():
    import svg_geometry
    ring = svg_geometry.flatten_subpath("M0 0 L10 0 C10 5 5 10 0 10 Z")
    assert ring[0].tolist() == [0, 0] and ring[-1].tolist() == [0, 10]
    with pytest.raises(ValueError):
        svg_geometry.flatten_subpath("M0 0 A5 5 0 0 1 10 0 Z")
