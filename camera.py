# camera.py
# Camera sources for the scanning app.
#
#   PiCamera      real Raspberry Pi camera, kept open for fast live preview
#   FolderCamera  steps through photos in a folder (testing without a camera)
#   DemoCamera    draws a fake light board with a marker and a random "tool"
#
# open_camera("auto") picks the Pi camera when Picamera2 is available and
# falls back to the demo camera otherwise.
#
# All cameras return full-size BGR frames (OpenCV order) from frame().

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

import config
import scan_service


class CameraError(Exception):
    """Camera could not be opened or read. str(e) is user-facing."""


class BaseCamera:
    name = "camera"

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def frame(self) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Real camera
# ---------------------------------------------------------------------------

class PiCamera(BaseCamera):
    """Picamera2 kept running so Scan is instant (no 2 s start-up each time).

    NOTE: written from the Picamera2 docs but not exercised off the Pi. The
    configuration, focus and white balance mirror capture.py, which is known
    to work.
    """
    name = "Pi camera"

    def __init__(self):
        try:
            from picamera2 import Picamera2
            from libcamera import controls
        except ImportError as e:
            raise CameraError("Picamera2 is not installed "
                              "(sudo apt install python3-picamera2).") from e
        self._Picamera2 = Picamera2
        self._controls = controls
        self._cam = None

    def start(self) -> None:
        try:
            self._cam = self._Picamera2()
            # "RGB888" in Picamera2 is laid out B,G,R in memory, which is the
            # channel order OpenCV expects. No colour conversion needed.
            #
            # The camera is run in the same STILL configuration capture.py has
            # always used (same size, format and sensor mode), only left running
            # so scans are instant. That keeps the field of view identical to the
            # photos the lens calibration in config.py was made from. Set
            # PREVIEW_USES_STILL_CONFIG = False for a faster live view, but only
            # if you re-check sizes afterwards.
            make_cfg = (self._cam.create_still_configuration
                        if config.PREVIEW_USES_STILL_CONFIG
                        else self._cam.create_preview_configuration)
            cfg = make_cfg(main={"size": config.IMAGE_RESOLUTION, "format": "RGB888"},
                           buffer_count=2)
            self._cam.configure(cfg)
            self._cam.start()
            time.sleep(2)  # let auto exposure / white balance settle
            self._cam.set_controls({
                "AwbEnable": True,
                "AwbMode": self._controls.AwbModeEnum.Indoor,
            })
            try:
                self._cam.set_controls({
                    "AfMode": self._controls.AfModeEnum.Manual,
                    "LensPosition": config.LENS_POSITION,
                })
            except RuntimeError:
                pass  # Camera Module 2 has fixed focus
        except Exception as e:  # camera busy, not connected, ...
            raise CameraError(f"Could not start the camera: {e}") from e

    def stop(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None

    def frame(self) -> np.ndarray:
        if self._cam is None:
            raise CameraError("Camera is not running.")
        return self._cam.capture_array("main")


# ---------------------------------------------------------------------------
# Test cameras
# ---------------------------------------------------------------------------

class FolderCamera(BaseCamera):
    """Cycles through the images in a folder, one per frame() call."""

    def __init__(self, folder: str):
        self._files = sorted(p for p in Path(folder).expanduser().iterdir()
                             if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if not self._files:
            raise CameraError(f"No images found in {folder}")
        self.name = f"Folder: {folder}"
        self._i = 0

    def frame(self) -> np.ndarray:
        img = cv2.imread(str(self._files[self._i % len(self._files)]))
        if img is None:
            raise CameraError("Could not read an image from the folder.")
        return img

    def advance(self) -> None:
        self._i += 1


class DemoCamera(BaseCamera):
    """A fake light board: bright background, an ArUco marker and one tool.

    frame() always shows the current tool, so the live preview is steady;
    call next_tool() (the app does this after Confirm / Retake) to swap it.
    """
    name = "Demo camera (no hardware)"

    PIXELS_PER_MM = 6.5

    def __init__(self, seed: int = 1):
        self._rng = np.random.default_rng(seed)
        self._kind = 0
        self._cache: np.ndarray | None = None
        self._noise: list | None = None
        self._noise_i = 0
        self.tool_visible = True
        self.auto_cycle = False          # simulate lifting a tool and placing the next
        self._appear_at: float | None = None

    def next_tool(self) -> None:
        self._kind += 1
        self._cache = None
        if self.auto_cycle:              # tool is picked up; next one arrives in a bit
            self.set_tool_visible(False, appear_after=3.0)

    def set_auto_cycle(self, on: bool) -> None:
        """Demo of auto-scan: start from an empty board, then a tool is 'placed'."""
        self.auto_cycle = on
        if on:
            self.set_tool_visible(False, appear_after=2.5)
        else:
            self.set_tool_visible(True)

    def set_tool_visible(self, visible: bool, appear_after: float | None = None) -> None:
        self.tool_visible = visible
        self._appear_at = (time.time() + appear_after) if appear_after is not None else None
        self._cache = None

    def frame(self) -> np.ndarray:
        if (not self.tool_visible and self._appear_at is not None
                and time.time() >= self._appear_at):
            self.tool_visible, self._appear_at, self._cache = True, None, None
        if self._cache is None:
            self._cache = self._render(self._kind)
        # Sensor noise like a real camera: cycle through a few pre-made noise
        # frames (making fresh random numbers for 3 million pixels is slow).
        if self._noise is None:
            self._noise = [self._rng.integers(-4, 5, size=self._cache.shape, dtype=np.int8)
                           for _ in range(3)]
        self._noise_i = (self._noise_i + 1) % len(self._noise)
        return np.clip(self._cache.astype(np.int16) + self._noise[self._noise_i],
                       0, 255).astype(np.uint8)

    # --- scene drawing ---------------------------------------------------

    def _render(self, kind: int) -> np.ndarray:
        w, h = config.IMAGE_RESOLUTION
        ppm = self.PIXELS_PER_MM
        yy, xx = np.mgrid[0:h, 0:w]
        # soft vignette on a bright light board
        r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
        board = (238 - 22 * r ** 2).astype(np.float32)
        img = cv2.cvtColor(board.astype(np.uint8), cv2.COLOR_GRAY2BGR)

        # ArUco marker with a white quiet zone, fixed in the lower-left area
        side = int(round(config.ARUCO_MARKER_SIZE_MM * ppm))
        d = cv2.aruco.getPredefinedDictionary(
            cv2.aruco.__dict__[config.ARUCO_DICT_NAME])
        marker = cv2.aruco.generateImageMarker(d, config.ARUCO_MARKER_ID, side)
        pad = side // 4
        mx, my = 430, 700
        img[my - pad:my + side + pad, mx - pad:mx + side + pad] = 255
        img[my:my + side, mx:mx + side] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)

        # the tool, centred-ish in the free area
        tool = np.zeros((h, w), np.uint8)
        cx, cy = int(w * 0.55), int(h * 0.46)
        shapes = (self._wrench, self._screwdriver, self._pliers, self._socket)
        if self.tool_visible:
            shapes[kind % len(shapes)](tool, cx, cy, ppm)
        img = np.where(tool[..., None] > 0, (46, 44, 42), img).astype(np.uint8)
        return cv2.GaussianBlur(img, (3, 3), 0)

    @staticmethod
    def _wrench(t, cx, cy, ppm):
        L = int(150 * ppm)
        cv2.rectangle(t, (cx - L // 2, cy - int(5 * ppm)),
                      (cx + L // 2, cy + int(5 * ppm)), 255, -1)
        for sx, r in ((-1, 12), (1, 10)):
            c = (cx + sx * L // 2, cy)
            cv2.circle(t, c, int(r * ppm), 255, -1)
            cv2.circle(t, (c[0] + sx * int(r * ppm * 0.75), c[1]),
                       int(r * ppm * 0.55), 0, -1)  # open jaw

    @staticmethod
    def _screwdriver(t, cx, cy, ppm):
        L = int(170 * ppm)
        cv2.rectangle(t, (cx - L // 2, cy - int(3 * ppm)),
                      (cx + L // 2 - int(60 * ppm), cy + int(3 * ppm)), 255, -1)
        cv2.rectangle(t, (cx + L // 2 - int(60 * ppm), cy - int(9 * ppm)),
                      (cx + L // 2, cy + int(9 * ppm)), 255, -1)
        cv2.circle(t, (cx + L // 2, cy), int(9 * ppm), 255, -1)

    @staticmethod
    def _pliers(t, cx, cy, ppm):
        a = int(55 * ppm)
        for sgn in (-1, 1):
            pts = np.array([[cx - a, cy + sgn * int(2 * ppm)],
                            [cx + a // 3, cy + sgn * int(14 * ppm)],
                            [cx + a, cy + sgn * int(9 * ppm)],
                            [cx + a, cy + sgn * int(3 * ppm)],
                            [cx + a // 3, cy + sgn * int(2 * ppm)]], np.int32)
            cv2.fillPoly(t, [pts], 255)
        cv2.circle(t, (cx + a // 3, cy), int(5 * ppm), 255, -1)
        for sgn in (-1, 1):  # handles
            cv2.line(t, (cx - a, cy + sgn * int(6 * ppm)),
                     (cx - int(1.6 * a), cy + sgn * int(24 * ppm)), 255, int(9 * ppm))

    @staticmethod
    def _socket(t, cx, cy, ppm):
        cv2.circle(t, (cx, cy), int(13 * ppm), 255, -1)
        cv2.circle(t, (cx, cy), int(7 * ppm), 120, -1)
        cv2.circle(t, (cx, cy), int(7 * ppm), 255, -1)


# ---------------------------------------------------------------------------
# Live feed: grabs frames on a background thread so the UI never waits
# ---------------------------------------------------------------------------

class LiveFeed:
    """Continuously reads a camera and keeps the newest frame available."""

    def __init__(self, camera: BaseCamera, fps: float = 12.0):
        self.camera = camera
        self._period = 1.0 / fps
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._marker_ok = False
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.camera.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.camera.stop()

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    @property
    def marker_ok(self) -> bool:
        return self._marker_ok

    @property
    def error(self) -> str | None:
        return self._error

    def _run(self) -> None:
        last_check = 0.0
        while not self._stop.is_set():
            t0 = time.time()
            try:
                f = self.camera.frame()
            except Exception as e:
                self._error = str(e)
                time.sleep(0.5)
                continue
            self._error = None
            with self._lock:
                self._frame = f
            if t0 - last_check > 0.5:  # marker check twice a second is plenty
                last_check = t0
                try:
                    self._marker_ok = scan_service.marker_visible(f)
                except Exception:
                    self._marker_ok = False
            time.sleep(max(0.0, self._period - (time.time() - t0)))


def open_camera(kind: str = "auto", folder: str | None = None) -> BaseCamera:
    """kind: "auto" | "pi" | "demo" | "folder"."""
    if kind == "demo":
        return DemoCamera()
    if kind == "folder":
        if not folder:
            raise CameraError("Give a folder of photos with --folder.")
        return FolderCamera(folder)
    if kind == "pi":
        return PiCamera()
    try:  # auto
        return PiCamera()
    except CameraError:
        return DemoCamera()
