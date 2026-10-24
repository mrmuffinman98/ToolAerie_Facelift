# tests/test_process.py
# Unit tests for process.py — runs without the Pi camera.
# Synthetic images with embedded ArUco markers are used to test scale detection.
#
# Run from the project root:
#   python -m pytest tests/test_process.py -v

import sys
import os
import numpy as np
import pytest

# Allow imports from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2
import config
import process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_aruco_image(
    width: int = 800,
    height: int = 600,
    marker_size_px: int = 100,
    tool_rect: tuple = (200, 150, 300, 200),
) -> np.ndarray:
    """Create a synthetic light-board image with an ArUco marker and a tool shape.

    White background simulates the lit board. A dark rectangle simulates the
    tool. A real ArUco marker (DICT_4X4_50, ID 0) is embedded at a known pixel
    size so detect_scale() can compute a predictable pixels-per-mm ratio.

    Args:
        width, height: Image dimensions in pixels.
        marker_size_px: Side length of the generated ArUco marker in pixels.
            With ARUCO_MARKER_SIZE_MM = 50.0, pixels_per_mm ≈ marker_size_px/50.
        tool_rect: (x, y, w, h) of the dark tool rectangle.

    Returns:
        BGR image array.
    """
    img = np.full((height, width, 3), 255, dtype=np.uint8)

    # Embed a real ArUco marker so detect_scale() can find it.
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker_gray = cv2.aruco.generateImageMarker(aruco_dict, 0, marker_size_px)
    else:
        marker_gray = np.zeros((marker_size_px, marker_size_px), dtype=np.uint8)
        cv2.aruco.drawMarker(aruco_dict, 0, marker_size_px, marker_gray, 1)

    marker_bgr = cv2.cvtColor(marker_gray, cv2.COLOR_GRAY2BGR)
    mx, my = 10, 10  # top-left corner of marker placement
    img[my : my + marker_size_px, mx : mx + marker_size_px] = marker_bgr

    # Draw the tool silhouette (dark rectangle on white background).
    x, y, w, h = tool_rect
    img[y : y + h, x : x + w] = 30  # near-black, not pure 0 for realism

    return img


def _patch_config_aruco(monkeypatch, width=800, height=600):
    """Set config values suitable for the synthetic ArUco test image."""
    monkeypatch.setattr(config, "CROP_BOUNDS", (0, 0, width, height))
    monkeypatch.setattr(config, "ARUCO_DICT_NAME", "DICT_4X4_50")
    monkeypatch.setattr(config, "ARUCO_MARKER_ID", 0)
    monkeypatch.setattr(config, "ARUCO_MARKER_SIZE_MM", 50.0)
    monkeypatch.setattr(config, "THRESHOLD_VALUE", 127)
    monkeypatch.setattr(config, "CONTOUR_MIN_AREA", 100)


# ---------------------------------------------------------------------------
# crop()
# ---------------------------------------------------------------------------

class TestCrop:
    def test_crop_reduces_size(self, monkeypatch):
        monkeypatch.setattr(config, "CROP_BOUNDS", (10, 10, 200, 150))
        img = np.zeros((400, 600, 3), dtype=np.uint8)
        result = process.crop(img)
        assert result.shape == (150, 200, 3)

    def test_crop_returns_correct_region(self, monkeypatch):
        monkeypatch.setattr(config, "CROP_BOUNDS", (0, 0, 50, 50))
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[0:50, 0:50] = 128
        result = process.crop(img)
        assert result.shape == (50, 50, 3)
        assert result[0, 0, 0] == 128


# ---------------------------------------------------------------------------
# detect_scale()
# ---------------------------------------------------------------------------

class TestDetectScale:
    def test_known_scale(self, monkeypatch):
        """ArUco marker of 100 px with ARUCO_MARKER_SIZE_MM=50 → ~2.0 px/mm."""
        _patch_config_aruco(monkeypatch)
        marker_size_px = 100
        img = _make_aruco_image(marker_size_px=marker_size_px)

        scale = process.detect_scale(img)

        expected = marker_size_px / config.ARUCO_MARKER_SIZE_MM  # 2.0
        assert abs(scale - expected) < 0.2, f"Expected ~{expected:.1f} px/mm, got {scale:.4f}"

    def test_no_marker_raises(self, monkeypatch):
        """All-white image (no marker) should raise RuntimeError."""
        _patch_config_aruco(monkeypatch)
        img = np.full((600, 800, 3), 255, dtype=np.uint8)

        with pytest.raises(RuntimeError, match="No ArUco marker detected"):
            process.detect_scale(img)

    def test_unknown_dict_raises(self, monkeypatch):
        """Unrecognised ARUCO_DICT_NAME should raise KeyError."""
        _patch_config_aruco(monkeypatch)
        monkeypatch.setattr(config, "ARUCO_DICT_NAME", "DICT_INVALID")
        img = _make_aruco_image()

        with pytest.raises(KeyError):
            process.detect_scale(img)


# ---------------------------------------------------------------------------
# extract_silhouette()
# ---------------------------------------------------------------------------

class TestExtractSilhouette:
    def test_returns_binary_mask(self, monkeypatch):
        _patch_config_aruco(monkeypatch)
        img = _make_aruco_image()

        mask, contour, ppm = process.extract_silhouette(img)

        assert mask.dtype == np.uint8
        assert set(np.unique(mask)).issubset({0, 255})

    def test_mask_covers_tool_region(self, monkeypatch):
        _patch_config_aruco(monkeypatch)
        tool = (200, 150, 300, 200)
        img = _make_aruco_image(tool_rect=tool)

        mask, _, _ = process.extract_silhouette(img)

        x, y, w, h = tool
        roi = mask[y : y + h, x : x + w]
        white_ratio = np.sum(roi == 255) / roi.size
        assert white_ratio > 0.8, f"Expected tool region mostly white, got {white_ratio:.2f}"

    def test_scale_returned(self, monkeypatch):
        """pixels_per_mm returned by extract_silhouette should be positive."""
        _patch_config_aruco(monkeypatch)
        img = _make_aruco_image()

        _, _, ppm = process.extract_silhouette(img)
        assert ppm > 0

    def test_no_tool_raises(self, monkeypatch):
        _patch_config_aruco(monkeypatch)
        monkeypatch.setattr(config, "CONTOUR_MIN_AREA", 1_000_000)  # unreachably large

        img = _make_aruco_image()
        with pytest.raises(RuntimeError):
            process.extract_silhouette(img)


# ---------------------------------------------------------------------------
# process_image() — integration
# ---------------------------------------------------------------------------

class TestProcessImage:
    def test_with_synthetic_image(self, tmp_path, monkeypatch):
        """Write a synthetic image to disk and run the full Step 2 pipeline."""
        _patch_config_aruco(monkeypatch)

        img = _make_aruco_image()

        # Patch undistort to be a no-op (no real calibration data needed).
        monkeypatch.setattr(process, "undistort", lambda x: x)

        img_path = tmp_path / "test_tool.jpg"
        cv2.imwrite(str(img_path), img)

        mask, ppm = process.process_image(str(img_path))

        assert mask is not None
        assert ppm > 0

    def test_missing_file_raises(self, monkeypatch):
        with pytest.raises(FileNotFoundError):
            process.process_image("/nonexistent/path/tool.jpg")
