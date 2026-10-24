# autoscan.py
# Hands-free scanning: notices when a tool is placed on the light board and has
# stopped moving, so the app can scan without a button press.
#
# How it works (plain image comparison, no machine learning):
#   1. LEARN      look at the empty board for a moment: that is the reference.
#   2. READY      a tool is "present" when enough of the picture is clearly
#                 DARKER than the reference (the tool blocks the backlight).
#   3. SETTLING   wait until the picture stops changing (hands moving away).
#   4. FIRE       ask the app to scan.
#   5. WAIT_CLEAR don't scan again until the board is empty for a moment, so one
#                 tool is never scanned twice. Then back to READY.
#
# Only darker-than-reference counts, so lifting a tool off the board (the
# picture gets brighter) never triggers a scan.

from __future__ import annotations

import cv2
import numpy as np

import config

LEARN, READY, SETTLING, WAIT_CLEAR = "learn", "ready", "settling", "wait_clear"

_STATUS = {
    LEARN: "Learning the empty board. Keep it clear.",
    READY: "Ready. Place a tool on the board.",
    SETTLING: "Hold still...",
    WAIT_CLEAR: "Remove the tool to continue.",
}


def _small(frame_bgr: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(cv2.resize(frame_bgr, (320, 180), interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0).astype(np.float32)


class AutoScan:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Start over: re-learn the empty board."""
        self.state = LEARN
        self.ref: np.ndarray | None = None
        self.dark_fraction = 0.0
        self._learn_t0 = 0.0
        self._prev: np.ndarray | None = None
        self._prev_t = 0.0
        self._motion = 99.0
        self._still_since: float | None = None
        self._clear_since: float | None = None

    def after_scan(self) -> None:
        """Call whenever a scan starts (button or automatic)."""
        self.state = WAIT_CLEAR
        self._clear_since = None
        self._still_since = None

    def status_text(self, marker_ok: bool = True) -> str:
        if not marker_ok and self.state != LEARN:
            return "Marker not found. Auto-scan is paused."
        return _STATUS[self.state]

    def update(self, frame_bgr: np.ndarray, marker_ok: bool, now: float) -> bool:
        """Feed one live frame. Returns True when a scan should start now."""
        cur = _small(frame_bgr)

        # motion = how much the picture changed over the last ~0.25 s
        if self._prev is None or now - self._prev_t >= 0.25:
            if self._prev is not None:
                self._motion = float(np.mean(np.abs(cur - self._prev)))
            self._prev, self._prev_t = cur, now

        if self.state == LEARN:
            if self.ref is None:
                self.ref, self._learn_t0 = cur, now
            else:
                self.ref = 0.7 * self.ref + 0.3 * cur
            if now - self._learn_t0 >= config.AUTOSCAN_LEARN_SECS:
                self.state = READY
            return False

        self.dark_fraction = float(np.mean((self.ref - cur) > config.AUTOSCAN_DROP))
        present = self.dark_fraction > config.AUTOSCAN_PRESENT_FRAC
        empty = self.dark_fraction < config.AUTOSCAN_EMPTY_FRAC

        if self.state == READY:
            if empty:                       # follow slow lighting drift
                self.ref = 0.98 * self.ref + 0.02 * cur
            elif present:
                self.state, self._still_since = SETTLING, None

        elif self.state == SETTLING:
            if self.dark_fraction < config.AUTOSCAN_EMPTY_FRAC * 2:
                self.state = READY          # it was only a passing hand
            elif self._motion < config.AUTOSCAN_STILL_LEVEL:
                if self._still_since is None:
                    self._still_since = now
                if now - self._still_since >= config.AUTOSCAN_STABLE_SECS and marker_ok:
                    self.after_scan()
                    return True
            else:
                self._still_since = None

        elif self.state == WAIT_CLEAR:
            if empty:
                if self._clear_since is None:
                    self._clear_since = now
                if now - self._clear_since >= config.AUTOSCAN_CLEAR_SECS:
                    self.ref = 0.5 * self.ref + 0.5 * cur
                    self.state = READY
            else:
                self._clear_since = None
        return False
