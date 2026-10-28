# calibration.py
# Camera profiles and the guided lens calibration behind the "Camera setup" menu.
#
# The scan pipeline is unchanged. It still reads config.CAMERA_MATRIX,
# config.DISTORTION_COEFFICIENTS, config.IMAGE_RESOLUTION, config.LENS_POSITION and
# config.CROP_BOUNDS. This module only decides WHICH values those hold: the ones
# from the active profile. The values that used to be pasted into config.py are
# kept, untouched, as the built-in profile "Original housing".
#
# The calibration itself is the same OpenCV method as camera_calibration.py:
# a 9x6 inner-corner checkerboard, findChessboardCorners + cornerSubPix at full
# resolution, cv2.calibrateCamera with default settings.
#
# No Tk in here, so it can be unit tested.

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np

import config
import storage

CHECKER = (9, 6)               # inner corners (columns, rows), as camera_calibration.py
SQUARES = (CHECKER[0] + 1, CHECKER[1] + 1)      # 10 x 7 printed squares
MIN_IMAGES = 15                # fewest usable photos we accept
GOOD_IMAGES = 24               # what the guide aims for
MAX_IMAGES = 40
GRID = 3                       # coverage map is GRID x GRID
MIN_PER_CELL = 2
RMS_GOOD, RMS_OK = 0.5, 1.0    # reprojection error limits (px), as camera_calibration.py
RESOLUTIONS = {"Standard (2304 x 1296)": (2304, 1296), "High (4608 x 2592)": (4608, 2592)}
BUILTIN_ID = "original"

# The values config.py held when the program started. Taken once, at import,
# before any profile is applied.
_ORIGINAL = dict(
    camera_matrix=np.asarray(config.CAMERA_MATRIX, dtype=float).tolist(),
    dist=np.asarray(config.DISTORTION_COEFFICIENTS, dtype=float).flatten().tolist(),
    resolution=[int(v) for v in config.IMAGE_RESOLUTION],
    lens_position=float(config.LENS_POSITION),
    crop=[int(v) for v in config.CROP_BOUNDS],
)


@dataclass
class Profile:
    id: str
    name: str
    camera_matrix: list
    dist: list
    resolution: list
    lens_position: float
    crop: list
    distance_mm: float | None = None
    board_mm: list | None = None          # [width, depth] of the lit board
    created: str = ""
    rms: float | None = None
    n_images: int | None = None
    bend_before_px: float | None = None
    bend_after_px: float | None = None
    verified: str = ""                    # date the Ready check passed
    builtin: bool = False

    @property
    def focal_px(self) -> float:
        return float(self.camera_matrix[0][0])

    def summary(self) -> str:
        w, h = self.resolution
        bits = [f"{w}x{h}"]
        if self.distance_mm:
            bits.append(f"camera {self.distance_mm / 1000:.2f} m from board")
        if self.board_mm:
            bits.append(f"board {self.board_mm[0]:g} x {self.board_mm[1]:g} mm")
        if self.rms is not None:
            bits.append(f"error {self.rms:.2f} px")
        return ", ".join(bits)


def builtin_profile() -> Profile:
    return Profile(id=BUILTIN_ID, name="Original housing", builtin=True,
                   created="original setup", **_ORIGINAL)


# --- storage ---------------------------------------------------------------

def _store_path() -> Path:
    return storage._settings_path().parent / "camera_profiles.json"


def images_dir(profile_id: str) -> Path:
    return _store_path().parent / "calibration" / profile_id


def _read() -> dict:
    try:
        return json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def load_all() -> list[Profile]:
    out = [builtin_profile()]
    for raw in _read().get("profiles", []):
        try:
            p = Profile(**{k: v for k, v in raw.items() if k in Profile.__dataclass_fields__})
            p.builtin = False
            if np.asarray(p.camera_matrix).shape == (3, 3) and len(p.dist) >= 4 \
                    and len(p.resolution) == 2 and len(p.crop) == 4:
                out.append(p)
        except TypeError:
            continue
    return out


def active() -> Profile:
    wanted = _read().get("active", BUILTIN_ID)
    profiles = load_all()
    return next((p for p in profiles if p.id == wanted), profiles[0])


def make_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"
    ids = {p.id for p in load_all()}
    pid, n = base, 2
    while pid in ids or pid == BUILTIN_ID:
        pid, n = f"{base}-{n}", n + 1
    return pid


def save_profile(p: Profile) -> None:
    if p.builtin:
        raise ValueError("The original profile can't be changed.")
    data = _read()
    others = [r for r in data.get("profiles", []) if r.get("id") != p.id]
    data["profiles"] = others + [asdict(p)]
    data.setdefault("active", BUILTIN_ID)
    _write(data)


def set_active(profile_id: str) -> Profile:
    profiles = load_all()
    p = next((q for q in profiles if q.id == profile_id), profiles[0])
    data = _read()
    data["active"] = p.id
    _write(data)
    return p


def delete_profile(profile_id: str) -> None:
    if profile_id == BUILTIN_ID:
        raise ValueError("The original profile can't be deleted.")
    data = _read()
    data["profiles"] = [r for r in data.get("profiles", []) if r.get("id") != profile_id]
    if data.get("active") == profile_id:
        data["active"] = BUILTIN_ID
    _write(data)
    shutil.rmtree(images_dir(profile_id), ignore_errors=True)


def rename_profile(profile_id: str, name: str) -> None:
    for p in load_all():
        if p.id == profile_id and not p.builtin and name.strip():
            p.name = name.strip()
            save_profile(p)


# --- applying a profile ----------------------------------------------------

def apply(p: Profile) -> None:
    """Point config.py's values at this profile (what the pipeline reads)."""
    config.CAMERA_MATRIX = np.asarray(p.camera_matrix, dtype=np.float64)
    config.DISTORTION_COEFFICIENTS = np.asarray(p.dist, dtype=np.float64)
    config.IMAGE_RESOLUTION = (int(p.resolution[0]), int(p.resolution[1]))
    config.LENS_POSITION = float(p.lens_position)
    config.CROP_BOUNDS = tuple(int(v) for v in p.crop)
    try:                                # the scanner caches lookup tables per image size
        import scan_service
        scan_service.clear_undistort_cache()
    except ImportError:
        pass


def apply_active() -> Profile:
    p = active()
    apply(p)
    return p


# --- planning --------------------------------------------------------------

@dataclass
class Plan:
    distance_mm: float
    lens_position: float          # Pi camera focus value = 1 / distance in metres
    px_per_mm: float
    view_w_mm: float
    view_h_mm: float


def focal_for(resolution) -> float:
    """Focal length in pixels at this resolution (same lens, same field of view)."""
    f0 = _ORIGINAL["camera_matrix"][0][0]
    return f0 * resolution[0] / _ORIGINAL["resolution"][0]


def plan(board_w_mm: float, board_d_mm: float, resolution, margin: float = 1.25,
         focal_px: float | None = None) -> Plan:
    """How far the camera has to be to see a board with some margin around it."""
    W, H = resolution
    f = focal_px or focal_for(resolution)
    dist = f * max(board_w_mm * margin / W, board_d_mm * margin / H)
    return plan_at(dist, resolution, focal_px=f)


def plan_at(distance_mm: float, resolution, focal_px: float | None = None) -> Plan:
    W, H = resolution
    f = focal_px or focal_for(resolution)
    d = max(distance_mm, 50.0)
    return Plan(d, round(min(15.0, max(0.0, 1000.0 / d)), 2), f / d, d * W / f, d * H / f)


PAPERS = {"Letter": (279.4, 215.9), "Tabloid (11x17)": (431.8, 279.4)}   # landscape, mm


def square_size_mm(view_w_mm: float, paper: str = "Letter") -> float:
    """Checkerboard square size: the pattern should fill ~40% of the camera's view,
    but must fit the paper. Rounded down to whole 5 mm."""
    pw, ph = PAPERS[paper]
    want = 0.40 * view_w_mm / SQUARES[0]
    fit = min((pw - 20) / SQUARES[0], (ph - 40) / SQUARES[1])
    s = min(want, fit)
    return max(5.0, float(int(s // 5) * 5)) if s >= 10 else max(5.0, round(s, 1))


def checkerboard_pdf(square_mm: float, paper: str = "Letter", out_dir: Path | None = None) -> Path:
    """One landscape page with the 10 x 7 checkerboard at exactly square_mm squares."""
    MM = 72.0 / 25.4
    pw, ph = (v * MM for v in PAPERS[paper])
    bw, bh = SQUARES[0] * square_mm * MM, SQUARES[1] * square_mm * MM
    if bw > pw - 10 * MM or bh > ph - 30 * MM:
        raise ValueError("That checkerboard doesn't fit the paper. Use bigger paper "
                         "or a smaller square size.")
    out_dir = Path(out_dir) if out_dir else storage.default_toolbox() / "Calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Checkerboard_{square_mm:g}mm_squares_{paper.split()[0]}.pdf"
    x0, y0 = (pw - bw) / 2, 22 * MM
    sq = square_mm * MM
    ops = ["0 g"]
    for r in range(SQUARES[1]):
        for c in range(SQUARES[0]):
            if (r + c) % 2 == 0:
                ops.append(f"{x0 + c * sq:.3f} {y0 + (SQUARES[1] - 1 - r) * sq:.3f} "
                           f"{sq:.3f} {sq:.3f} re f")

    def text(y, s, font="F1", pt=10):
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"BT /{font} {pt} Tf {(pw - len(s) * pt * 0.5) / 2:.2f} {y:.2f} Td ({s}) Tj ET")

    text(ph - 12 * MM, f"Tool Aerie calibration checkerboard - squares are {square_mm:g} mm", "F2", 13)
    text(ph - 20 * MM, "Print at 100% / Actual size. Glue it flat to something stiff (no ripples). "
                       "Keep all squares and the white edge.")
    text(10 * MM, "The squares' exact size does not matter for the calibration. Flatness does.")
    pdf_write(out, pw, ph, ops)
    return out


def pdf_write(path: Path, pw: float, ph: float, ops: list[str]) -> None:
    """Write a one-page PDF (Helvetica F1 / Helvetica-Bold F2) from drawing operators."""
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:g} {ph:g}] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>".encode(),
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"]
    buf = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    Path(path).write_bytes(bytes(buf))


# --- finding the checkerboard ----------------------------------------------

_LIVE_FLAGS = (cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
               | cv2.CALIB_CB_FAST_CHECK)
_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)


def detect_live(frame_bgr: np.ndarray, max_w: int = 960):
    """Quick look for the checkerboard (for the live guide). Returns corners in
    full-frame pixels, or None. Not precise: used only for guidance."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    k = min(1.0, max_w / gray.shape[1])
    small = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else gray
    ok, c = cv2.findChessboardCorners(small, CHECKER, _LIVE_FLAGS)
    return (c / k).astype(np.float32) if ok else None


def detect_precise(frame_bgr: np.ndarray):
    """The measurement used for the calibration: same as camera_calibration.py
    (full-resolution findChessboardCorners + cornerSubPix 11x11)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    ok, corners = cv2.findChessboardCorners(gray, CHECKER, None)
    if not ok:
        return None
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), _CRITERIA)


def pose_signature(corners: np.ndarray) -> tuple:
    """(centre x, centre y, angle deg, size px): used to tell two shots apart."""
    pts = corners.reshape(-1, 2)
    v = pts[CHECKER[0] - 1] - pts[0]
    return (float(pts[:, 0].mean()), float(pts[:, 1].mean()),
            float(np.degrees(np.arctan2(v[1], v[0]))), float(np.linalg.norm(v)))


def is_new_pose(sig: tuple, previous: list[tuple], frame_w: int) -> bool:
    """A shot counts only if the pattern moved, turned or changed size since every
    recent shot, so ten photos of the same pose don't pass for ten poses."""
    for p in previous[-3:]:
        d = np.hypot(sig[0] - p[0], sig[1] - p[1]) / frame_w
        dang = abs((sig[2] - p[2] + 180) % 360 - 180)
        dsize = abs(sig[3] - p[3]) / max(p[3], 1.0)
        if d < 0.04 and dang < 8 and dsize < 0.08:
            return False
    return True


def coverage(corner_sets: list[np.ndarray], size: tuple[int, int]) -> np.ndarray:
    """GRID x GRID counts of where the pattern's centre was."""
    g = np.zeros((GRID, GRID), int)
    for c in corner_sets:
        cx, cy = c.reshape(-1, 2).mean(axis=0)
        g[min(GRID - 1, int(cy / size[1] * GRID)), min(GRID - 1, int(cx / size[0] * GRID))] += 1
    return g


def emptiest_cell(g: np.ndarray) -> tuple[int, int]:
    r, c = np.unravel_index(np.argmin(g), g.shape)
    return int(r), int(c)


CELL_NAMES = [["top left", "top middle", "top right"],
              ["middle left", "center", "middle right"],
              ["bottom left", "bottom middle", "bottom right"]]


# --- the calibration -------------------------------------------------------

@dataclass
class CalibResult:
    camera_matrix: list
    dist: list
    rms: float
    n_used: int
    n_dropped: int
    per_image_px: list
    bend_before_px: float
    bend_after_px: float
    crop: list
    verdict: str                  # "good" | "ok" | "poor"
    notes: list = field(default_factory=list)


def _line_rms(pts: np.ndarray) -> float:
    """RMS distance of points from their best-fit straight line (px)."""
    c = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(c, full_matrices=False)
    d = c @ vt[1]
    return float(np.sqrt(np.mean(d ** 2)))


def bend(corners: np.ndarray) -> float:
    """How curved the checkerboard's rows and columns look, in px (0 = straight)."""
    p = corners.reshape(CHECKER[1], CHECKER[0], 2)
    vals = [_line_rms(p[r]) for r in range(CHECKER[1])] + \
           [_line_rms(p[:, c]) for c in range(CHECKER[0])]
    return float(np.sqrt(np.mean(np.square(vals))))


def _objp() -> np.ndarray:
    o = np.zeros((CHECKER[0] * CHECKER[1], 3), np.float32)
    o[:, :2] = np.mgrid[0:CHECKER[0], 0:CHECKER[1]].T.reshape(-1, 2)
    return o


def _calibrate(sets: list[np.ndarray], size):
    objp = _objp()
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera([objp] * len(sets), sets, size, None, None)
    errs = []
    for o, c, rv, tv in zip([objp] * len(sets), sets, rvecs, tvecs):
        proj, _ = cv2.projectPoints(o, rv, tv, K, dist)
        errs.append(float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - c.reshape(-1, 2)) ** 2,
                                                  axis=1)))))
    return rms, K, dist, errs


def valid_crop(K: np.ndarray, dist: np.ndarray, size, trim: int = 0) -> list:
    """The part of the straightened picture with no empty edges, as (x, y, w, h).
    (The scanner builds its straightened picture with alpha=1, so this is the
    region guaranteed to be filled.)"""
    _, roi = cv2.getOptimalNewCameraMatrix(K, dist, size, 1)
    x, y, w, h = (int(v) for v in roi)
    if w < size[0] // 4 or h < size[1] // 4:
        x, y, w, h = 0, 0, size[0], size[1]
    x, y, w, h = x + trim, y + trim, w - 2 * trim, h - 2 * trim
    return [x, y, max(w, 16), max(h, 16)]


def compute(corner_sets: list[np.ndarray], size: tuple[int, int], trim: int = 0) -> CalibResult:
    """Run cv2.calibrateCamera (default settings, as camera_calibration.py) on the
    photos. Drops clear outlier photos once, and measures how straight the
    checkerboard lines are before and after correction."""
    if len(corner_sets) < MIN_IMAGES:
        raise ValueError(f"Need at least {MIN_IMAGES} usable photos; have {len(corner_sets)}.")
    sets = [np.asarray(c, np.float32).reshape(-1, 1, 2) for c in corner_sets]
    notes: list[str] = []
    dropped = 0
    rms, K, dist, errs = _calibrate(sets, size)
    med = float(np.median(errs))
    bad = [i for i, e in enumerate(errs) if e > max(1.0, 2.5 * med)]
    if bad and len(sets) - len(bad) >= MIN_IMAGES:
        keep = [i for i in range(len(sets)) if i not in bad]
        dropped = len(bad)
        sets = [sets[i] for i in keep]
        rms, K, dist, errs = _calibrate(sets, size)
        notes.append(f"Left out {len(bad)} photo(s) that didn't agree with the rest "
                     "(blurry or checkerboard not flat).")
    newK, _ = cv2.getOptimalNewCameraMatrix(K, dist, size, 1)
    before = float(np.mean([bend(c) for c in sets]))
    after = float(np.mean([bend(cv2.undistortPoints(c, K, dist, P=newK)) for c in sets]))
    verdict = "good" if rms < RMS_GOOD else "ok" if rms < RMS_OK else "poor"
    g = coverage(sets, size)
    if g.min() < 1:
        r, c = emptiest_cell(g)
        notes.append(f"No photos had the checkerboard in the {CELL_NAMES[r][c]} of the frame. "
                     "Edges and corners are where distortion is largest, so add some there.")
        if verdict == "good":
            verdict = "ok"
    return CalibResult(K.tolist(), dist.flatten().tolist(), float(rms), len(sets), dropped, errs, before, after,
                       valid_crop(K, dist, size, trim), verdict, notes)


def profile_from(result: CalibResult, name: str, resolution, lens_position: float,
                 distance_mm: float | None, board_mm: list | None) -> Profile:
    return Profile(id=make_id(name), name=name.strip() or "New profile",
                   camera_matrix=result.camera_matrix, dist=result.dist,
                   resolution=[int(resolution[0]), int(resolution[1])],
                   lens_position=float(lens_position), crop=result.crop,
                   distance_mm=distance_mm, board_mm=board_mm,
                   created=time.strftime("%Y-%m-%d %H:%M"), rms=round(result.rms, 4),
                   n_images=result.n_used, bend_before_px=round(result.bend_before_px, 3),
                   bend_after_px=round(result.bend_after_px, 3))


# --- ready check -----------------------------------------------------------

@dataclass
class Check:
    ok: bool
    text: str


def ready_checks(p: Profile, prepared_bgr: np.ndarray | None,
                 practice: bool = False) -> list[Check]:
    """Is this profile ready to scan with? `prepared_bgr` is a camera frame already
    straightened and cropped with the profile (scan_service.prepare)."""
    import markers as markers_mod
    out: list[Check] = []
    if p.rms is None:
        out.append(Check(True, "Original calibration (made earlier)."))
    else:
        good = p.rms < RMS_OK
        out.append(Check(good, f"Calibration error {p.rms:.2f} px "
                               f"({'good' if p.rms < RMS_GOOD else 'acceptable' if good else 'too high, redo it'})."))
    if p.bend_before_px is not None and p.bend_after_px is not None:
        out.append(Check(p.bend_after_px < max(0.5, 0.5 * p.bend_before_px),
                         f"Straight lines: bend {p.bend_before_px:.1f} px before, "
                         f"{p.bend_after_px:.1f} px after correction."))
    if prepared_bgr is None:
        out.append(Check(False, "No camera picture yet."))
        return out
    ms = {m.id: m.size_mm for m in markers_mod.load()}
    found = {i: c for i, c in markers_mod.detect_all(prepared_bgr).items() if i in ms}
    if not found:
        out.append(Check(False, "No registered marker visible. Lay a marker flat on the board "
                                "(Marker setup lists the IDs)."))
        return out
    per = {}
    for i, pts in found.items():
        side = float(np.mean([np.linalg.norm(pts[(k + 1) % 4] - pts[k]) for k in range(4)]))
        per[i] = side / ms[i]
    out.append(Check(True, "Marker" + ("s " if len(found) > 1 else " ")
                     + ", ".join(f"ID {i}" for i in sorted(found)) + " found."))
    mean = float(np.mean(list(per.values())))
    if practice:
        out.append(Check(True, f"Scale {mean:.2f} px/mm (practice camera: not checked)."))
    elif p.distance_mm:
        exp = p.focal_px / p.distance_mm
        close = abs(mean - exp) / exp < 0.2
        out.append(Check(close, f"Scale {mean:.2f} px/mm "
                                f"(expected about {exp:.2f} from the {p.distance_mm / 1000:.2f} m distance)"
                                + ("." if close else ". Check the distance or the marker's actual size.")))
    else:
        out.append(Check(True, f"Scale {mean:.2f} px/mm."))
    if len(per) > 1:
        spread = (max(per.values()) - min(per.values())) / mean
        out.append(Check(spread <= markers_mod.MAX_SPREAD,
                         f"Markers agree within {spread * 100:.2f}% "
                         f"({'good' if spread <= markers_mod.MAX_SPREAD else 'check each size in Marker setup'})."))
    return out


def mark_verified(profile_id: str) -> None:
    for p in load_all():
        if p.id == profile_id and not p.builtin:
            p.verified = time.strftime("%Y-%m-%d")
            save_profile(p)
