# markers.py
# The list of ArUco scale markers: which IDs exist, how big each was meant to be
# printed, and how big it really is (measured with a ruler after printing).
#
# Scanning stays on the original pipeline. With ONE marker visible the scale is
# exactly what process.detect_scale gives. When two or more registered markers
# are visible, their individual scales are averaged (see combined_scale).
#
# No Tk in here, so it can be unit tested. Stored in settings.json under "markers".

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

import config
import process
import storage

MAX_MARKERS = 6
MIN_MM, MAX_MM = 8.0, 150.0          # printable and detectable range
MAX_SPREAD = 0.005                   # markers disagreeing by more than 0.5% -> warn
PAD_PX = 60                          # same blanking padding the original uses


@dataclass
class Marker:
    id: int
    nominal_mm: float       # size it was meant to be printed at
    size_mm: float          # size it really is: what scanning uses


def _clean(raw) -> list[Marker]:
    out: list[Marker] = []
    seen = set()
    for m in raw or []:
        try:
            mid, nom, size = int(m["id"]), float(m["nominal_mm"]), float(m["size_mm"])
        except (KeyError, TypeError, ValueError):
            continue
        if mid in seen or not (0 <= mid < 50) or not (MIN_MM <= size <= MAX_MM) \
                or not (MIN_MM <= nom <= MAX_MM):
            continue
        seen.add(mid)
        out.append(Marker(mid, nom, size))
    return out


def load() -> list[Marker]:
    """The saved markers. Always contains the original marker (config.ARUCO_MARKER_ID)."""
    ms = _clean(storage.load_settings().get("markers"))
    if not any(m.id == config.ARUCO_MARKER_ID for m in ms):
        ms.insert(0, Marker(config.ARUCO_MARKER_ID, config.ARUCO_MARKER_SIZE_MM,
                            config.ARUCO_MARKER_SIZE_MM))
    return ms


def save(ms: list[Marker]) -> None:
    storage.save_settings(markers=[m.__dict__ for m in _clean(
        [m.__dict__ for m in ms])])
    apply_to_config()


def apply_to_config() -> None:
    """The original code reads config.ARUCO_MARKER_SIZE_MM: point it at the main marker."""
    for m in load():
        if m.id == config.ARUCO_MARKER_ID:
            config.ARUCO_MARKER_SIZE_MM = m.size_mm


def next_id(ms: list[Marker]) -> int | None:
    used = {m.id for m in ms}
    return next((i for i in range(50) if i not in used), None)


def parse_mm(text: str) -> float | None:
    try:
        v = float(text.strip().replace(",", ".").lower().replace("mm", ""))
    except ValueError:
        return None
    return v if MIN_MM <= v <= MAX_MM else None


# --- scanning -------------------------------------------------------------

@dataclass
class Scale:
    ppm: float
    used: list[tuple[int, float]]       # (marker id, its own px/mm)
    spread: float                       # (max-min)/mean
    corners: dict                       # id -> (4,2) corner array
    extra_ids: list[int]                # registered markers other than the main one


def detect_all(image_bgr: np.ndarray) -> dict[int, np.ndarray]:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    d = cv2.aruco.getPredefinedDictionary(process._ARUCO_DICT_MAP[config.ARUCO_DICT_NAME])
    corners, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for i, c in zip(np.asarray(ids).flatten(), corners)}


def combined_scale(image_bgr: np.ndarray, ms: list[Marker] | None = None) -> Scale | None:
    """Scale from every registered marker in view, or None when fewer than two are.

    None means: keep the original single-marker scale untouched.
    """
    ms = ms if ms is not None else load()
    sizes = {m.id: m.size_mm for m in ms}
    found = {i: c for i, c in detect_all(image_bgr).items() if i in sizes}
    if len(found) < 2:
        return None
    per = []
    for i, pts in sorted(found.items()):
        side = float(np.mean([np.linalg.norm(pts[(k + 1) % 4] - pts[k]) for k in range(4)]))
        per.append((i, side / sizes[i]))
    vals = [p for _, p in per]
    mean = float(np.mean(vals))
    return Scale(mean, per, (max(vals) - min(vals)) / mean, found,
                 [i for i in found if i != config.ARUCO_MARKER_ID])


def blank_markers(mask: np.ndarray, corners_by_id: dict, ids) -> np.ndarray:
    """Clear the (padded) area around the given markers so they are never read as tools."""
    out = mask.copy()
    for i in ids:
        x, y, w, h = cv2.boundingRect(corners_by_id[i].astype(np.int32))
        out[max(0, y - PAD_PX):min(out.shape[0], y + h + PAD_PX),
            max(0, x - PAD_PX):min(out.shape[1], x + w + PAD_PX)] = 0
    return out


# --- printing -------------------------------------------------------------

def markers_dir() -> Path:
    return storage.default_toolbox() / "Markers"


def print_pdf(marker: Marker, out_dir: Path | None = None) -> Path:
    """Write a one-page PDF with the marker at exactly marker.nominal_mm (Letter, vector).

    Print at 100% / "Actual size". The page also has a 50 mm check line so the print
    can be verified with a ruler. If it prints differently, DON'T reprint: measure the
    marker's black square and enter that as its actual size in Marker setup.
    No extra libraries: the PDF is written directly.
    """
    MM = 72.0 / 25.4
    pw, ph = 612.0, 792.0                        # US Letter in points
    size = marker.nominal_mm
    if size * MM + 40 * MM > pw:
        raise ValueError("That marker is too big for one page.")
    out_dir = Path(out_dir) if out_dir else markers_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"Marker_ID{marker.id}_{size:g}mm.pdf"

    d = cv2.aruco.getPredefinedDictionary(process._ARUCO_DICT_MAP[config.ARUCO_DICT_NAME])
    cells = _bits(d, marker.id)
    n = cells.shape[0]
    cell = size * MM / n
    x0 = (pw - size * MM) / 2
    y0 = ph - 40 * MM - size * MM

    ops: list[str] = ["0 g"]
    for r in range(n):
        for k in range(n):
            if cells[r, k] == 0:
                ops.append(f"{x0 + k * cell:.4f} {y0 + (n - 1 - r) * cell:.4f} "
                           f"{cell:.4f} {cell:.4f} re f")

    def text(y, s, font="F1", pt=10):
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        w = len(s) * pt * 0.5                    # rough centring, good enough for a label
        ops.append(f"BT /{font} {pt} Tf {(pw - w) / 2:.2f} {y:.2f} Td ({s}) Tj ET")

    text(ph - 20 * MM, f"Tool Aerie marker  ID {marker.id}  -  {size:g} mm square", "F2", 14)
    y = y0 - 14 * MM
    for line in ("Print at 100% / Actual size (turn OFF 'fit to page').",
                 "Cut it out leaving a white edge around the black square; lay it flat on the board.",
                 "Measure the BLACK square with a ruler. If it is not exactly the size above,",
                 "do not reprint: enter the measured size in Marker setup."):
        text(y, line)
        y -= 5 * MM
    y -= 8 * MM
    xa, xb = (pw - 50 * MM) / 2, (pw + 50 * MM) / 2
    ops.append(f"0.5 w {xa:.3f} {y:.3f} m {xb:.3f} {y:.3f} l S "
               f"{xa:.3f} {y - 2 * MM:.3f} m {xa:.3f} {y + 2 * MM:.3f} l S "
               f"{xb:.3f} {y - 2 * MM:.3f} m {xb:.3f} {y + 2 * MM:.3f} l S")
    text(y - 7 * MM, "This line should measure 50 mm")

    stream = "\n".join(ops).encode("latin-1")
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
    buf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    out.write_bytes(bytes(buf))
    return out


def _bits(aruco_dict, marker_id: int) -> np.ndarray:
    """The marker as a grid of cells (0 = black), including its 1-cell black border."""
    n = aruco_dict.markerSize + 2
    return cv2.aruco.generateImageMarker(aruco_dict, marker_id, n * 10)[5::10, 5::10]
