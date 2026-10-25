import shutil, subprocess
import cv2, numpy as np, pytest
import config, markers, process, scan_service, storage


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(config, "ARUCO_MARKER_SIZE_MM", 20.0)


def _board(sizes_px):
    img = np.full((900, 1400, 3), 235, np.uint8)
    d = cv2.aruco.getPredefinedDictionary(process._ARUCO_DICT_MAP[config.ARUCO_DICT_NAME])
    for (mid, side, x, y) in sizes_px:
        m = cv2.aruco.generateImageMarker(d, mid, side)
        img[y - 20:y + side + 20, x - 20:x + side + 20] = 255
        img[y:y + side, x:x + side] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    return img


def test_default_is_the_original_marker():
    ms = markers.load()
    assert [(m.id, m.size_mm) for m in ms] == [(0, 20.0)]


def test_one_marker_leaves_scale_to_original_code():
    assert markers.combined_scale(_board([(0, 200, 100, 600)])) is None


def test_two_markers_average_and_actual_size_is_used():
    img = _board([(0, 200, 100, 600), (1, 250, 900, 600)])   # 10 px/mm and 10 px/mm if id1 = 25
    ms = [markers.Marker(0, 20, 20), markers.Marker(1, 20, 25)]   # printed 25 instead of 20
    s = markers.combined_scale(img, ms)
    assert s.ppm == pytest.approx(10.0, rel=1e-2) and s.spread < 1e-2
    bad = markers.combined_scale(img, [markers.Marker(0, 20, 20), markers.Marker(1, 20, 20)])
    assert bad.spread > markers.MAX_SPREAD          # un-corrected size disagrees -> warning


def test_registry_roundtrip_and_next_id():
    ms = markers.load() + [markers.Marker(1, 20, 25)]
    markers.save(ms)
    assert [(m.id, m.size_mm) for m in markers.load()] == [(0, 20.0), (1, 25.0)]
    assert markers.next_id(markers.load()) == 2
    assert markers.parse_mm("25 mm") == 25.0 and markers.parse_mm("3") is None


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler")
def test_pdf_marker_prints_at_exact_size(tmp_path):
    pdf = markers.print_pdf(markers.Marker(3, 30, 30), tmp_path)
    subprocess.run(["pdftoppm", "-r", "300", "-png", str(pdf), str(tmp_path / "p")], check=True)
    img = cv2.imread(str(next(tmp_path.glob("p*.png"))))
    found = markers.detect_all(img)
    assert list(found) == [3]
    pts = found[3]
    side = np.mean([np.linalg.norm(pts[(k + 1) % 4] - pts[k]) for k in range(4)])
    assert side / 300 * 25.4 == pytest.approx(30.0, abs=0.15)
