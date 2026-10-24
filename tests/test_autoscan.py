"""Auto-scan detector, driven with demo frames and simulated time (no camera)."""
import numpy as np
import pytest

import autoscan as au
import camera


class Clock:
    def __init__(self):
        self.t = 100.0

    def run(self, det, cam, seconds, marker_ok=True, step=0.08, shake=0):
        """Feed frames for `seconds`; return the time of the first FIRE or None."""
        fired = None
        for _ in range(int(seconds / step)):
            self.t += step
            f = cam.frame()
            if shake:   # tool sliding around: roll the picture a few pixels
                f = np.roll(f, int(shake * np.sin(self.t * 9)), axis=1)
            if det.update(f, marker_ok, self.t) and fired is None:
                fired = self.t
        return fired


@pytest.fixture
def rig():
    cam = camera.DemoCamera()
    cam.set_tool_visible(False)          # empty board
    return au.AutoScan(), cam, Clock()


def _learned(rig):
    det, cam, clk = rig
    assert clk.run(det, cam, 2.0) is None
    assert det.state == au.READY
    return det, cam, clk


def test_learns_empty_board_without_firing(rig):
    det, cam, clk = rig
    assert det.state == au.LEARN
    _learned(rig)


def test_fires_once_tool_is_placed_and_still(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    fired = clk.run(det, cam, 3.0)
    assert fired is not None
    assert det.state == au.WAIT_CLEAR


def test_does_not_fire_while_tool_is_moving(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    assert clk.run(det, cam, 3.0, shake=25) is None


def test_does_not_scan_the_same_tool_twice(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    assert clk.run(det, cam, 3.0) is not None
    assert clk.run(det, cam, 5.0) is None            # tool still sitting there


def test_rearms_after_tool_removed(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    clk.run(det, cam, 3.0)
    cam.set_tool_visible(False)
    assert clk.run(det, cam, 2.5) is None            # lifting it never triggers
    assert det.state == au.READY
    cam.next_tool()
    cam.set_tool_visible(True)
    assert clk.run(det, cam, 3.0) is not None        # next tool scans


def test_waits_for_marker(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    assert clk.run(det, cam, 3.0, marker_ok=False) is None
    assert "Marker not found" in det.status_text(False)


def test_passing_hand_is_ignored(rig):
    det, cam, clk = _learned(rig)
    cam.set_tool_visible(True)
    clk.run(det, cam, 0.5)                           # something appears briefly ...
    cam.set_tool_visible(False)                      # ... and leaves
    assert clk.run(det, cam, 3.0) is None
    assert det.state == au.READY
