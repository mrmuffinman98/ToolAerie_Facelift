"""Tests for the scanning engine and session export (no camera, no display)."""
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import camera
import config
import scan_service as ss
from session import Session


@pytest.fixture
def cam():
    return camera.DemoCamera()


def test_demo_scan_measures_real_size(cam):
    r = ss.scan(cam.frame(), ss.Settings(offset_mm=0.0))
    # demo wrench is ~150 mm between jaw centres plus two heads: ~176 mm
    assert 170 < r.width_mm < 182
    assert r.pieces == 1
    assert 6.2 < r.pixels_per_mm < 6.8          # demo scene is 6.5 px/mm


def test_clearance_grows_outline(cam):
    f = cam.frame()
    a = ss.scan(f, ss.Settings(offset_mm=0.0))
    b = ss.scan(f, ss.Settings(offset_mm=3.0))
    assert b.width_mm == pytest.approx(a.width_mm + 6.0, abs=1.5)


def test_missing_marker_gives_friendly_error():
    blank = np.full((1296, 2304, 3), 235, np.uint8)
    with pytest.raises(ss.ScanError, match="Marker not found"):
        ss.scan(blank)


def test_settings_are_clamped():
    s = ss.Settings(offset_mm=-4, adaptive_block=50, adaptive_c=999).clamped()
    assert s.offset_mm == 0 and s.adaptive_block % 2 == 1 and s.adaptive_c == 40


def test_config_restored_after_analyze(cam):
    before = (config.OUTLINE_OFFSET_MM, config.ADAPTIVE_C)
    ss.scan(cam.frame(), ss.Settings(offset_mm=2.0, adaptive_c=12))
    assert (config.OUTLINE_OFFSET_MM, config.ADAPTIVE_C) == before


def _session_with_tools(tmp_path, n=3):
    cam = camera.DemoCamera()
    s = Session("t", root=str(tmp_path))
    for _ in range(n):
        s.add_tool(ss.scan(cam.frame(), ss.Settings(offset_mm=1.0)))
        cam.next_tool()
    return s


def test_session_saves_files_and_combined_svg(tmp_path):
    s = _session_with_tools(tmp_path)
    out = s.export_combined(gap_mm=10)
    assert out.exists()
    assert len(list((s.folder / "tools").glob("*.svg"))) == 3
    assert len(list((s.folder / "photos").glob("*.jpg"))) == 3
    root = ET.parse(out).getroot()
    w = float(root.get("width").removesuffix("mm"))
    h = float(root.get("height").removesuffix("mm"))
    assert root.get("viewBox") == f"0 0 {w:.3f} {h:.3f}"     # 1 unit = 1 mm
    groups = [g for g in root if g.tag.endswith("}g")]
    assert len(groups) == 3


def test_layout_has_gaps_and_no_overlap(tmp_path):
    s = _session_with_tools(tmp_path)
    placements, W, H = s.layout(gap_mm=12)
    for a, b in zip(placements, placements[1:]):
        gap = b.y_mm - (a.y_mm + a.tool.height_mm)
        assert gap == pytest.approx(12, abs=1e-6)
    last = placements[-1]
    assert H == pytest.approx(last.y_mm + last.tool.height_mm + config.COMBINED_MARGIN_MM)


def test_rows_mode_wraps(tmp_path):
    s = _session_with_tools(tmp_path)
    placements, W, H = s.layout(gap_mm=10, max_width_mm=250)
    assert len({p.y_mm for p in placements}) > 1               # wrapped onto 2+ rows
    assert all(p.x_mm + p.tool.width_mm <= 250 + 1e-6 for p in placements)


def test_undo_removes_files(tmp_path):
    s = _session_with_tools(tmp_path, 2)
    rec = s.remove_last()
    assert not (s.folder / rec.svg_file).exists()
    assert len(s.tools) == 1


def test_sessions_never_overwrite(tmp_path):
    a = Session("same", root=str(tmp_path))
    b = Session("same", root=str(tmp_path))
    assert a.folder != b.folder
