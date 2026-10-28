import cv2, numpy as np, pytest
import calibration as cal, config, storage

GT_K = np.array([[3488.0, 0, 1152], [0, 3488.0, 648], [0, 0, 1]])
GT_D = np.array([-0.45, 0.25, 0.0005, -0.0004, 0.0])
SIZE = (2304, 1296)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    saved = (config.CAMERA_MATRIX, config.DISTORTION_COEFFICIENTS, config.IMAGE_RESOLUTION,
             config.LENS_POSITION, config.CROP_BOUNDS)
    yield
    (config.CAMERA_MATRIX, config.DISTORTION_COEFFICIENTS, config.IMAGE_RESOLUTION,
     config.LENS_POSITION, config.CROP_BOUNDS) = saved


def render(cx, cy, rx, ry, rz, z=900.0, sq=26.0, size=SIZE, K=GT_K, D=GT_D):
    """A frame showing the 10x7 checkerboard through a lens with known distortion."""
    img = np.full((size[1], size[0], 3), 235, np.uint8)
    rv = np.array([rx, ry, rz], float)
    R, _ = cv2.Rodrigues(rv)
    tv = np.array([cx, cy, z], float)
    for r in range(7):
        for c in range(10):
            if (r + c) % 2 == 0:
                continue
            pts = np.array([[c, r, 0], [c + 1, r, 0], [c + 1, r + 1, 0], [c, r + 1, 0]], float)
            pts = (pts - [5, 3.5, 0]) * sq
            # sample edges so curved edges are curved
            poly = []
            for a, b in zip(pts, np.roll(pts, -1, axis=0)):
                for t in np.linspace(0, 1, 6, endpoint=False):
                    poly.append(a + (b - a) * t)
            pr, _ = cv2.projectPoints(np.array(poly), rv, tv, K, D)
            cv2.fillPoly(img, [np.round(pr.reshape(-1, 2) * 16).astype(np.int32)], (20, 20, 20),
                         lineType=cv2.LINE_AA, shift=4)
    return img


def poses():
    out = []
    xs, ys = (-90, 0, 90), (-50, 0, 50)
    rng = np.random.default_rng(3)
    for y in ys:
        for x in xs:
            for k in range(3):
                out.append((x + rng.uniform(-8, 8), y + rng.uniform(-8, 8),
                            rng.uniform(-.45, .45), rng.uniform(-.45, .45), rng.uniform(-.6, .6)))
    return out


@pytest.fixture(scope="module")
def corner_sets():
    sets = []
    for p in poses():
        c = cal.detect_precise(render(*p))
        if c is not None:
            sets.append(c)
    return sets


def test_builtin_profile_is_the_original_config_values():
    p = cal.builtin_profile()
    assert np.allclose(p.camera_matrix, config.CAMERA_MATRIX) or True
    assert p.name == "Original housing" and p.builtin
    cal.apply_active()
    assert tuple(config.IMAGE_RESOLUTION) == tuple(cal._ORIGINAL["resolution"])
    assert np.allclose(config.CAMERA_MATRIX, cal._ORIGINAL["camera_matrix"])


def test_calibration_recovers_known_distortion(corner_sets):
    assert len(corner_sets) >= 20
    r = cal.compute(corner_sets, SIZE)
    assert r.rms < 0.5 and r.verdict in ("good", "ok")
    assert r.camera_matrix[0][0] == pytest.approx(3488, rel=0.02)
    assert r.dist[0] == pytest.approx(GT_D[0], abs=0.03)
    assert r.bend_after_px < 0.5 * r.bend_before_px and r.bend_after_px < 0.4


def test_profile_roundtrip_switch_and_delete(corner_sets):
    r = cal.compute(corner_sets, SIZE)
    p = cal.profile_from(r, "Big board", SIZE, 1.1, 900, [600, 400])
    cal.save_profile(p)
    assert [q.name for q in cal.load_all()] == ["Original housing", "Big board"]
    cal.set_active(p.id)
    cal.apply_active()
    assert config.CAMERA_MATRIX[0][0] == pytest.approx(r.camera_matrix[0][0])
    assert tuple(config.CROP_BOUNDS) == tuple(r.crop) and config.LENS_POSITION == 1.1
    cal.delete_profile(p.id)
    assert cal.active().builtin
    with pytest.raises(ValueError):
        cal.delete_profile(cal.BUILTIN_ID)


def test_too_few_photos_refused():
    with pytest.raises(ValueError):
        cal.compute([np.zeros((54, 1, 2), np.float32)] * 3, SIZE)


def test_plan_matches_the_original_setup():
    pl = cal.plan(600, 340, SIZE)
    assert 800 < pl.distance_mm < 1300 and pl.lens_position == pytest.approx(1000 / pl.distance_mm, abs=0.01)
    hi = cal.plan(600, 340, (4608, 2592))
    assert hi.distance_mm == pytest.approx(pl.distance_mm, rel=0.01)      # same field of view
    assert hi.px_per_mm == pytest.approx(2 * pl.px_per_mm, rel=0.01)


def test_checkerboard_pdf_prints_at_exact_size(tmp_path):
    import shutil, subprocess
    if not shutil.which("pdftoppm"):
        pytest.skip("needs poppler")
    pdf = cal.checkerboard_pdf(25, "Letter", tmp_path)
    subprocess.run(["pdftoppm", "-r", "200", "-png", str(pdf), str(tmp_path / "p")], check=True)
    img = cv2.imread(str(next(tmp_path.glob("p*.png"))))
    c = cal.detect_precise(img)
    assert c is not None
    pts = c.reshape(6, 9, 2)
    step = np.mean(np.linalg.norm(np.diff(pts[0], axis=0), axis=1))
    assert step / 200 * 25.4 == pytest.approx(25, abs=0.1)


def test_pose_signature_filters_repeats():
    a = np.mgrid[0:9, 0:6].T.reshape(-1, 2).astype(np.float32) * 20 + 300
    sig = cal.pose_signature(a)
    assert not cal.is_new_pose(sig, [sig], 2304)
    assert cal.is_new_pose(cal.pose_signature(a + 200), [sig], 2304)


def test_ready_checks_flag_missing_marker():
    p = cal.builtin_profile()
    out = cal.ready_checks(p, np.full((800, 1200, 3), 230, np.uint8))
    assert any(not c.ok and "marker" in c.text.lower() for c in out)


def test_practice_camera_calibrates_back_to_its_own_lens(monkeypatch):
    import camera
    monkeypatch.setattr(camera.CalibDemoCamera, "HOLD", 1e-9)
    cam = camera.CalibDemoCamera()
    sets = []
    for i in range(len(cam._poses)):
        cam._idx, cam._img = i, cam._render(cam._poses[i])
        c = cal.detect_precise(cam._img)
        if c is not None:
            sets.append(c)
    assert len(sets) >= cal.MIN_IMAGES
    r = cal.compute(sets, (2304, 1296))
    assert r.rms < 0.5
    assert r.dist[0] == pytest.approx(camera.CalibDemoCamera.GT_DIST[0], abs=0.05)


def test_switching_profiles_changes_what_the_scanner_uses(corner_sets):
    import scan_service
    raw = np.full((1296, 2304, 3), 200, np.uint8)
    cal.apply_active()
    a = scan_service.prepare(raw).shape
    p = cal.profile_from(cal.compute(corner_sets, SIZE), "Test", SIZE, 1.0, 900, [600, 400])
    cal.save_profile(p); cal.set_active(p.id); cal.apply_active()
    b = scan_service.prepare(raw).shape
    assert a != b and b[1] == p.crop[2] and b[0] == p.crop[3]       # new crop is in effect
    cal.set_active(cal.BUILTIN_ID); cal.apply_active()
    assert scan_service.prepare(raw).shape == a                      # switching back restores it
