"""The drawer.json / .toolaerie hand-off to the board-building app (see FORMAT.md)."""
import json
import zipfile

import pytest

import camera
import config
import scan_service as ss
import shape_info
from session import Session


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    cam = camera.DemoCamera()
    s = Session("Drawer 1")
    for _ in range(4):                       # wrench, screwdriver, pliers, socket
        s.add_tool(ss.scan(cam.frame(), ss.Settings(offset_mm=1.5)))
        cam.next_tool()
    dest = tmp_path / "My Toolbox" / "Drawer 1"
    s.save_to(dest)
    return s, dest


def test_package_is_one_zip_with_everything(saved):
    s, dest = saved
    pkg = dest / "Drawer 1.toolaerie"
    names = zipfile.ZipFile(pkg).namelist()
    assert "drawer.json" in names and "Drawer_1_combined.svg" in names
    assert sum(n.startswith("tools/") for n in names) == 4
    assert sum(n.startswith("photos/") for n in names) == 4
    assert not any(n.endswith(".toolaerie") for n in names)      # never packs itself


def test_manifest_header(saved):
    _, dest = saved
    m = json.loads((dest / "drawer.json").read_text())
    assert m["format"] == "toolaerie-drawer"
    assert m["schema_version"] == config.MANIFEST_SCHEMA_VERSION
    assert m["units"] == "mm"
    assert m["drawer"]["name"] == "Drawer 1" and m["drawer"]["toolbox"] == "My Toolbox"
    assert m["scan"]["marker_size_mm"] == config.ARUCO_MARKER_SIZE_MM


def test_geometry_has_no_clearance_but_svg_does(saved):
    _, dest = saved
    m = json.loads((dest / "drawer.json").read_text())
    wrench = m["tools"][0]
    g = wrench["geometry"]
    assert wrench["clearance_mm_in_svg"] == 1.5
    # true wrench ~173 mm long; the SVG canvas adds clearance + margin on top
    assert 170 < g["length_mm"] < 176
    assert wrench["svg_size_mm"][0] > g["bbox_mm"][0] + 2 * 1.5
    # polygon fits exactly inside its bounding box, starting at the origin
    pts = [p for ring in g["polygon_mm"] for p in ring]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert min(xs) == pytest.approx(0, abs=0.01) and max(xs) == pytest.approx(g["bbox_mm"][0], abs=0.01)
    assert min(ys) == pytest.approx(0, abs=0.01)


def test_tool_ids_are_unique_and_stable(saved):
    s, dest = saved
    ids = [t["id"] for t in json.loads((dest / "drawer.json").read_text())["tools"]]
    assert len(set(ids)) == 4 and all(ids)
    s.save_to(dest, replace=True)                                # re-save
    again = [t["id"] for t in json.loads((dest / "drawer.json").read_text())["tools"]]
    assert again == ids


def test_axis_and_direction_hints(saved):
    _, dest = saved
    tools = json.loads((dest / "drawer.json").read_text())["tools"]
    wrench, screwdriver, pliers, socket = (t["geometry"] for t in tools)
    for g in (wrench, screwdriver):
        assert abs(g["principal_axis_deg"]) < 2           # both were laid horizontally
        assert g["axis_reliable"]
    assert not screwdriver["direction_ambiguous"]         # handle end is heavier: head/handle known
    assert screwdriver["centroid_offset_along_axis_mm"] > 10
    assert wrench["direction_ambiguous"]                  # symmetric two-ended wrench
    assert not socket["axis_reliable"]                    # round: no meaningful axis


def test_shape_info_rotation_invariance():
    import numpy as np
    # 100 x 20 mm rectangle, rotated 30 degrees, at 5 px/mm
    ppm = 5.0
    rect = np.array([[0, 0], [100, 0], [100, 20], [0, 20]], float) * ppm
    a = np.radians(30)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    g = shape_info.analyze_shape([rect @ R.T + 300], ppm)
    assert g["length_mm"] == pytest.approx(100, abs=0.2)
    assert g["width_mm"] == pytest.approx(20, abs=0.2)
    assert g["principal_axis_deg"] == pytest.approx(30, abs=0.5)
    assert g["area_mm2"] == pytest.approx(2000, rel=0.01)
    assert g["direction_ambiguous"]                       # a plain bar has no head
