# scan_service.py
# The scanning engine behind the app: frame in -> outline + review overlay out.
#
# It reuses the original pipeline (process.py, vectorize.py) but splits it so
# the interface can re-run just the cheap part (silhouette extraction) when the
# user nudges clearance / threshold on the review screen:
#
#   prepare(raw_frame)            undistort + crop   (once per photo)
#   analyze(prepared, settings)   scale + threshold  (every time a slider moves)
#
# Nothing here touches Tk or the camera, so it is easy to test.

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

import config
import process
import vectorize


# ---------------------------------------------------------------------------
# Settings the user can change from the interface
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    """Per-scan tuning knobs. Defaults come from config.py."""
    offset_mm: float = float(config.OUTLINE_OFFSET_MM)       # clearance added around the tool
    adaptive_c: float = float(config.ADAPTIVE_C)             # lower = more sensitive edges
    adaptive_block: int = int(config.ADAPTIVE_BLOCK_SIZE)    # neighbourhood size (odd)
    min_area_px: int = int(config.CONTOUR_MIN_AREA)          # ignore specks smaller than this

    def clamped(self) -> "Settings":
        block = max(3, int(self.adaptive_block)) | 1  # force odd
        return replace(
            self,
            offset_mm=min(max(self.offset_mm, 0.0), 15.0),
            adaptive_c=min(max(self.adaptive_c, 1.0), 40.0),
            adaptive_block=block,
            min_area_px=max(100, int(self.min_area_px)),
        )


class ScanError(Exception):
    """A scan failed in a way the user can act on. str(e) is shown on screen."""


# ---------------------------------------------------------------------------
# Result of one analysis
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    prepared: np.ndarray            # undistorted + cropped BGR photo
    mask: np.ndarray                # uint8, tool = 255
    outlines: list[np.ndarray]      # list of (N, 2) float arrays, pixel coords
    pixels_per_mm: float
    settings: Settings
    marker_corners: np.ndarray      # (4, 2)
    width_mm: float = 0.0           # overall bounding box of the tool
    height_mm: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def pieces(self) -> int:
        return len(self.outlines)


# ---------------------------------------------------------------------------
# Step 1: prepare (undistort + crop), with cached remap tables
# ---------------------------------------------------------------------------

_maps_lock = threading.Lock()
_maps_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def _undistort_maps(w: int, h: int):
    """Same maths as process.undistort(), but the lookup tables are built once.

    cv2.undistort() rebuilds them on every call; caching makes scans several
    times faster on the Pi.
    """
    key = (w, h)
    with _maps_lock:
        if key not in _maps_cache:
            new_matrix, _ = cv2.getOptimalNewCameraMatrix(
                config.CAMERA_MATRIX, config.DISTORTION_COEFFICIENTS, (w, h), alpha=1
            )
            _maps_cache[key] = cv2.initUndistortRectifyMap(
                config.CAMERA_MATRIX, config.DISTORTION_COEFFICIENTS,
                None, new_matrix, (w, h), cv2.CV_16SC2,
            )
        return _maps_cache[key]


def prepare(raw_bgr: np.ndarray) -> np.ndarray:
    """Undistort and crop a raw camera frame (BGR)."""
    h, w = raw_bgr.shape[:2]
    map1, map2 = _undistort_maps(w, h)
    undistorted = cv2.remap(raw_bgr, map1, map2, cv2.INTER_LINEAR)
    return process.crop(undistorted)


# ---------------------------------------------------------------------------
# Step 2: analyze (scale + silhouette) with overridable settings
# ---------------------------------------------------------------------------

_config_lock = threading.Lock()


@contextlib.contextmanager
def _config_overrides(s: Settings):
    """Temporarily point config.py's values at the user's settings.

    process.py reads config at call time, so this lets the interface tune the
    pipeline without editing config.py. The lock keeps two threads from
    overlapping.
    """
    names = {
        "USE_ADAPTIVE_THRESHOLD": True,
        "OUTLINE_OFFSET_MM": s.offset_mm,
        "ADAPTIVE_C": s.adaptive_c,
        "ADAPTIVE_BLOCK_SIZE": s.adaptive_block,
        "CONTOUR_MIN_AREA": s.min_area_px,
    }
    with _config_lock:
        saved = {k: getattr(config, k) for k in names}
        for k, v in names.items():
            setattr(config, k, v)
        try:
            yield
        finally:
            for k, v in saved.items():
                setattr(config, k, v)


def analyze(prepared: np.ndarray, settings: Settings | None = None) -> ScanResult:
    """Find the tool outline in a prepared photo.

    Raises:
        ScanError: marker missing, nothing found, etc. (message is user-facing)
    """
    s = (settings or Settings()).clamped()
    quiet = io.StringIO()  # process.py prints progress; keep the console clean
    try:
        with _config_overrides(s), contextlib.redirect_stdout(quiet):
            mask, _contours, ppm = process.extract_silhouette(prepared)
            marker = process.detect_scale(prepared)[1]
    except RuntimeError as e:
        raise ScanError(_friendly(str(e))) from e

    # Outlines come from the final mask so they include the clearance offset
    # and match exactly what gets traced and cut.
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    outlines = [c.reshape(-1, 2).astype(np.float64) for c in found
                if cv2.contourArea(c) >= s.min_area_px]
    if not outlines:
        raise ScanError("No tool found. Check the tool is on the light board "
                        "and not touching the marker.")

    allpts = np.vstack(outlines)
    w_mm = float(np.ptp(allpts[:, 0])) / ppm
    h_mm = float(np.ptp(allpts[:, 1])) / ppm

    result = ScanResult(prepared, mask, outlines, ppm, s, marker, w_mm, h_mm)
    result.warnings = _quality_warnings(result)
    return result


def scan(raw_bgr: np.ndarray, settings: Settings | None = None) -> ScanResult:
    """prepare() + analyze() in one call."""
    return analyze(prepare(raw_bgr), settings)


def _friendly(msg: str) -> str:
    if "No ArUco marker" in msg:
        return ("Marker not found. Make sure the ArUco marker is flat, in view "
                "and not covered by the tool.")
    if "CONTOUR_MIN_AREA" in msg or "No contours" in msg:
        return ("No tool found. Check the tool is on the light board and the "
                "light is on.")
    return msg


def _quality_warnings(r: ScanResult) -> list[str]:
    out: list[str] = []
    h, w = r.mask.shape[:2]
    pts = np.vstack(r.outlines)
    margin = 80  # process.py blanks 60 px at the frame edge
    if (pts[:, 0].min() < margin or pts[:, 1].min() < margin
            or pts[:, 0].max() > w - margin or pts[:, 1].max() > h - margin):
        out.append("Tool is close to the edge of the frame. It may be cut off.")
    if r.pieces > 1:
        out.append(f"Found {r.pieces} separate pieces. Expected one tool? "
                   "Raise 'Ignore specks' or move stray objects away.")
    if not (2.0 <= r.pixels_per_mm <= 60.0):
        out.append(f"Unusual scale ({r.pixels_per_mm:.1f} px/mm). "
                   "Check the marker size in config.py.")
    if r.width_mm < 5 and r.height_mm < 5:
        out.append("Tool looks very small. Is this just a speck?")
    return out


def marker_visible(frame_bgr: np.ndarray) -> bool:
    """Fast check used by the live preview: is the configured marker in view?"""
    small = cv2.resize(frame_bgr, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    d = cv2.aruco.getPredefinedDictionary(process._ARUCO_DICT_MAP[config.ARUCO_DICT_NAME])
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    _, ids, _ = det.detectMarkers(gray)
    return ids is not None and config.ARUCO_MARKER_ID in ids.flatten().tolist()


# ---------------------------------------------------------------------------
# Review overlay
# ---------------------------------------------------------------------------

OUTLINE_BGR = (0, 255, 120)     # bright green
MARKER_BGR = (255, 160, 0)      # blue-ish, marks the detected marker


def render_overlay(r: ScanResult, mode: str = "overlay",
                   max_size: tuple[int, int] = (900, 600)) -> np.ndarray:
    """Draw the review image and return it as RGB, scaled to fit max_size.

    mode "overlay": photo with the outline on top.
    mode "outline": outline on dark background (shows exactly what gets cut).
    """
    if mode == "outline":
        canvas = np.full_like(r.prepared, 24)
        cv2.fillPoly(canvas, [o.astype(np.int32) for o in r.outlines], (48, 48, 48))
    else:
        canvas = r.prepared.copy()

    thick = max(2, int(round(canvas.shape[1] / 700)))
    cv2.polylines(canvas, [o.astype(np.int32) for o in r.outlines], True,
                  OUTLINE_BGR, thick, cv2.LINE_AA)
    cv2.polylines(canvas, [r.marker_corners.astype(np.int32)], True,
                  MARKER_BGR, thick, cv2.LINE_AA)

    ih, iw = canvas.shape[:2]
    scale = min(max_size[0] / iw, max_size[1] / ih, 1.0)
    if scale < 1.0:
        canvas = cv2.resize(canvas, (int(iw * scale), int(ih * scale)),
                            interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Vector output
# ---------------------------------------------------------------------------

def to_svg_string(r: ScanResult) -> str:
    """Trace the result's mask into a raw (pixel-space) vtracer SVG string."""
    with contextlib.redirect_stdout(io.StringIO()):
        return vectorize.bitmap_to_svg_string(r.mask)
