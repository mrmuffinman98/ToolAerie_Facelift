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
import solidify
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
    expected: int = 1                                        # how many tools are on the board

    def clamped(self) -> "Settings":
        block = max(3, int(self.adaptive_block)) | 1  # force odd
        return replace(
            self,
            offset_mm=min(max(self.offset_mm, 0.0), 15.0),
            adaptive_c=min(max(self.adaptive_c, 1.0), 40.0),
            adaptive_block=block,
            min_area_px=max(100, int(self.min_area_px)),
            expected=min(max(int(self.expected), 1), MAX_TOOLS),
        )


MAX_TOOLS = 12


class ScanError(Exception):
    """A scan failed in a way the user can act on. str(e) is shown on screen."""


# ---------------------------------------------------------------------------
# Result of one analysis
# ---------------------------------------------------------------------------

@dataclass
class Part:
    """One tool found in the photo."""
    mask: np.ndarray                # uint8, this tool only (clearance included)
    outlines: list[np.ndarray]      # pixel coords, with clearance
    raw_outlines: list[np.ndarray]  # pixel coords, WITHOUT clearance
    width_mm: float
    height_mm: float
    base_mask: np.ndarray | None = None   # this tool only, WITHOUT clearance (filled outline)


@dataclass
class ScanResult:
    prepared: np.ndarray            # undistorted + cropped BGR photo
    mask: np.ndarray                # uint8, all kept tools = 255
    outlines: list[np.ndarray]      # list of (N, 2) float arrays, pixel coords (with clearance)
    pixels_per_mm: float
    settings: Settings
    marker_corners: np.ndarray      # (4, 2)
    raw_outlines: list[np.ndarray] = field(default_factory=list)  # same, WITHOUT clearance
    width_mm: float = 0.0           # overall bounding box of everything kept
    height_mm: float = 0.0
    warnings: list[str] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)        # one per tool, left to right
    ignored_outlines: list[np.ndarray] = field(default_factory=list)  # pieces not kept
    expected: int = 1               # the tool count the user asked for
    found: int = 0                  # how many separate pieces were seen in total
    raw_frame: np.ndarray | None = None          # camera frame before lens correction
    trace: dict = field(default_factory=dict)    # intermediate pictures for troubleshooting

    @property
    def pieces(self) -> int:
        """Number of tools kept (never more than the count the user entered)."""
        return len(self.parts) or len(self.outlines)

    def split(self) -> list["ScanResult"]:
        """One single-tool ScanResult per kept tool (for saving each separately)."""
        if len(self.parts) <= 1:
            return [self]
        out = []
        for part in self.parts:
            r = ScanResult(self.prepared, part.mask, part.outlines, self.pixels_per_mm,
                           self.settings, self.marker_corners, raw_outlines=part.raw_outlines,
                           width_mm=part.width_mm, height_mm=part.height_mm,
                           parts=[part], expected=1, found=1,
                           raw_frame=self.raw_frame, trace=self.trace)
            r.warnings = _quality_warnings(r)
            out.append(r)
        return out


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
    """Find the tool outline(s) in a prepared photo.

    The edge detection is the original process.extract_silhouette. After it:
      * a hollow outline (edge band with a gap on a large tool) is made solid;
      * the user's tool count decides how many outlines are kept: the N largest,
        never more than N;
      * the clearance is added to each kept tool.

    Raises:
        ScanError: marker missing, nothing found, etc. (message is user-facing)
    """
    s = (settings or Settings()).clamped()
    quiet = io.StringIO()  # process.py prints progress; keep the console clean
    try:
        # clearance is applied below, per tool, so ask the original step for none
        trace: dict = {}
        with _config_overrides(replace(s, offset_mm=0.0)), contextlib.redirect_stdout(quiet):
            process.trace = trace
            try:
                mask0, _contours, ppm = process.extract_silhouette(prepared)
            finally:
                process.trace = None
            marker = process.detect_scale(prepared)[1]
    except RuntimeError as e:
        raise ScanError(_friendly(str(e))) from e

    solid, was_hollow = solidify.make_solid(prepared, mask0, s.min_area_px)

    found_cnts, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    found_cnts = [c for c in found_cnts if cv2.contourArea(c) >= s.min_area_px]
    if not found_cnts:
        raise ScanError("No tool found. Check the tool is on the light board "
                        "and not touching the marker.")
    found_cnts.sort(key=cv2.contourArea, reverse=True)
    kept = found_cnts[:s.expected]
    dropped = found_cnts[s.expected:]
    kept.sort(key=lambda c: float(c[:, 0, 0].mean()))        # number tools left to right

    offset_px = int(round(s.offset_mm * ppm)) if s.offset_mm > 0 else 0
    grow = (cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * offset_px + 1, 2 * offset_px + 1))
            if offset_px > 0 else None)

    parts: list[Part] = []
    for c in kept:
        raw_mask = np.zeros_like(solid)
        cv2.drawContours(raw_mask, [c], -1, 255, thickness=cv2.FILLED)
        pm = cv2.dilate(raw_mask, grow) if grow is not None else raw_mask
        outs, _ = cv2.findContours(pm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        outs = [o.reshape(-1, 2).astype(np.float64) for o in outs
                if cv2.contourArea(o) >= s.min_area_px]
        if not outs:
            continue
        pts = np.vstack(outs)
        parts.append(Part(pm, outs, [c.reshape(-1, 2).astype(np.float64)],
                          float(np.ptp(pts[:, 0])) / ppm, float(np.ptp(pts[:, 1])) / ppm,
                          base_mask=raw_mask))
    if not parts:
        raise ScanError("No tool found. Check the tool is on the light board "
                        "and not touching the marker.")

    all_mask = np.zeros_like(solid)
    for p in parts:
        all_mask = cv2.bitwise_or(all_mask, p.mask)
    outlines = [o for p in parts for o in p.outlines]
    raw = [o for p in parts for o in p.raw_outlines]
    allpts = np.vstack(outlines)
    result = ScanResult(prepared, all_mask, outlines, ppm, s, marker, raw_outlines=raw,
                        width_mm=float(np.ptp(allpts[:, 0])) / ppm,
                        height_mm=float(np.ptp(allpts[:, 1])) / ppm,
                        parts=parts,
                        ignored_outlines=[c.reshape(-1, 2).astype(np.float64) for c in dropped],
                        expected=s.expected, found=len(found_cnts),
                        trace={"threshold": trace.get("threshold"), "original_mask": mask0,
                               "solid_mask": solid, "was_hollow": bool(was_hollow)})
    result.warnings = _count_warnings(result, [cv2.contourArea(c) / ppm ** 2 for c in dropped]) \
        + _quality_warnings(result)
    if was_hollow:
        result.warnings.append("This tool's edge had a gap, so its inside was filled in "
                               "to make a solid outline.")
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


def _count_warnings(r: ScanResult, dropped_areas_mm2: list[float]) -> list[str]:
    out: list[str] = []
    n = r.expected
    if len(r.parts) < n:
        out.append(f"You said {n} tools but only {len(r.parts)} "
                   f"{'was' if len(r.parts) == 1 else 'were'} found. If two tools are "
                   "touching, move them apart and scan again.")
    if dropped_areas_mm2:
        k = len(dropped_areas_mm2)
        sizes = ", ".join(f"{a:.0f} mm²" for a in dropped_areas_mm2[:3])
        out.append(f"Ignored {k} other piece{'s' if k != 1 else ''} ({sizes}) because you "
                   f"said {n} tool{'s' if n != 1 else ''}. Shown dotted in orange. If a tool "
                   "really has several parts, raise the tool count.")
    return out


def _quality_warnings(r: ScanResult) -> list[str]:
    out: list[str] = []
    h, w = r.mask.shape[:2]
    pts = np.vstack(r.outlines)
    margin = 80  # process.py blanks 60 px at the frame edge
    if (pts[:, 0].min() < margin or pts[:, 1].min() < margin
            or pts[:, 0].max() > w - margin or pts[:, 1].max() > h - margin):
        out.append("Tool is close to the edge of the frame. It may be cut off.")
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
IGNORED_BGR = (0, 140, 255)     # orange, pieces ignored because of the tool count


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
    # pieces that were NOT kept (more than the tool count): thin orange line
    if r.ignored_outlines:
        cv2.polylines(canvas, [o.astype(np.int32) for o in r.ignored_outlines], True,
                      IGNORED_BGR, max(1, thick - 1), cv2.LINE_AA)
    # several tools in one photo: number them so the list on the side makes sense
    if len(r.parts) > 1:
        scale_txt = max(1.2, canvas.shape[1] / 900)
        for i, part in enumerate(r.parts, 1):
            pts = np.vstack(part.outlines)
            cx, cy = pts.mean(axis=0)
            cv2.putText(canvas, str(i), (int(cx) - 12, int(cy) + 12), cv2.FONT_HERSHEY_SIMPLEX,
                        scale_txt, (0, 0, 0), thick + 4, cv2.LINE_AA)
            cv2.putText(canvas, str(i), (int(cx) - 12, int(cy) + 12), cv2.FONT_HERSHEY_SIMPLEX,
                        scale_txt, (255, 255, 255), thick + 1, cv2.LINE_AA)

    ih, iw = canvas.shape[:2]
    scale = min(max_size[0] / iw, max_size[1] / ih, 1.0)
    if scale < 1.0:
        canvas = cv2.resize(canvas, (int(iw * scale), int(ih * scale)),
                            interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Vector output
# ---------------------------------------------------------------------------

def to_svg_string(r: ScanResult, with_clearance: bool = True) -> str:
    """Trace the result's mask into a raw (pixel-space) vtracer SVG string.

    with_clearance=False traces the same tool without the clearance (same
    vtracer step, same settings), for the board builder to add its own.
    """
    mask = r.mask
    if not with_clearance and len(r.parts) == 1 and r.parts[0].base_mask is not None:
        mask = r.parts[0].base_mask
    with contextlib.redirect_stdout(io.StringIO()):
        return vectorize.bitmap_to_svg_string(mask)
