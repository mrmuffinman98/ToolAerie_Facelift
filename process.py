# process.py
# Step 2 — Image processing pipeline.
# Sub-steps: distortion correction → crop → scale detection → silhouette extraction.
#
# Requires:
#   opencv-contrib-python-headless  (NOT opencv-python — ArUco is in contrib)
#   numpy
#
# Install on Raspberry Pi:
#   pip install opencv-contrib-python-headless numpy
#
# ArUco scale detection replaces the manual ruler approach. A printed ArUco
# marker of known physical size is detected automatically in every frame,
# giving a reliable pixels-per-mm ratio without requiring manual measurement.
# Generate the marker: python generate_aruco_marker.py

import cv2
import numpy as np

import config


# ---------------------------------------------------------------------------
# ArUco dictionary lookup
# ---------------------------------------------------------------------------

_ARUCO_DICT_MAP = {
    "DICT_4X4_50":         cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100":        cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50":         cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100":        cv2.aruco.DICT_5X5_100,
    "DICT_6X6_50":         cv2.aruco.DICT_6X6_50,
    "DICT_ARUCO_ORIGINAL": cv2.aruco.DICT_ARUCO_ORIGINAL,
}


# ---------------------------------------------------------------------------
# 2a: Distortion correction
# ---------------------------------------------------------------------------

def undistort(image: np.ndarray) -> np.ndarray:
    """Apply lens distortion correction using calibration values from config.

    Args:
        image: Raw captured image as a NumPy array (BGR or RGB, HxWxC).

    Returns:
        Undistorted image array (same shape).
    """
    h, w = image.shape[:2]
    new_matrix, _ = cv2.getOptimalNewCameraMatrix(
        config.CAMERA_MATRIX,
        config.DISTORTION_COEFFICIENTS,
        (w, h),
        alpha=1,
    )
    return cv2.undistort(
        image,
        config.CAMERA_MATRIX,
        config.DISTORTION_COEFFICIENTS,
        None,
        new_matrix,
    )


# ---------------------------------------------------------------------------
# 2b: Crop
# ---------------------------------------------------------------------------

def crop(image: np.ndarray) -> np.ndarray:
    """Remove edge artifacts introduced by the undistort step.

    Uses CROP_BOUNDS from config: (x, y, width, height).
    """
    x, y, w, h = config.CROP_BOUNDS
    return image[y : y + h, x : x + w]


# ---------------------------------------------------------------------------
# 2c: Scale detection via ArUco marker
# ---------------------------------------------------------------------------

def detect_scale(image: np.ndarray) -> float:
    """Detect a printed ArUco marker in the image and return pixels-per-mm.

    The marker must be flat on the light board surface within the camera frame.
    Generate and print the marker using: python generate_aruco_marker.py

    Supports both OpenCV 4.7+ (ArucoDetector class) and older 4.x builds.
    The average of all four marker side lengths is used to reduce
    corner-detection jitter and give a stable scale factor.

    Args:
        image: Cropped, undistorted image array (BGR).

    Returns:
        Tuple of (pixels_per_mm, marker_corners) where marker_corners is a
        (4, 2) array of the detected marker corner coordinates in pixel space.

    Raises:
        RuntimeError: If no ArUco marker is detected.
        KeyError: If ARUCO_DICT_NAME in config is not a recognised dictionary.
    """
    if config.ARUCO_DICT_NAME not in _ARUCO_DICT_MAP:
        raise KeyError(
            f"Unknown ARUCO_DICT_NAME '{config.ARUCO_DICT_NAME}'. "
            f"Choose from: {list(_ARUCO_DICT_MAP.keys())}"
        )

    dict_id = _ARUCO_DICT_MAP[config.ARUCO_DICT_NAME]
    aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image

    # OpenCV 4.7+ introduced the ArucoDetector class (new API).
    # Fall back to the legacy detectMarkers function for older builds.
    if hasattr(cv2.aruco, "ArucoDetector"):
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
        corners, ids, _ = detector.detectMarkers(gray)
    else:
        parameters = cv2.aruco.DetectorParameters_create()
        corners, ids, _ = cv2.aruco.detectMarkers(
            gray, aruco_dict, parameters=parameters
        )

    if ids is None or len(ids) == 0:
        raise RuntimeError(
            "No ArUco marker detected in the image. "
            "Check that the marker is in frame, flat, and well-lit. "
            "Run: python generate_aruco_marker.py to create the reference marker."
        )

    # Use the first detected marker.
    # corners[0] has shape (1, 4, 2); reshape to (4, 2): TL, TR, BR, BL.
    pts = corners[0].reshape((4, 2))

    # Average all four side lengths to reduce corner sub-pixel detection jitter.
    side_lengths_px = [
        float(np.linalg.norm(pts[(i + 1) % 4] - pts[i])) for i in range(4)
    ]
    avg_side_px = float(np.mean(side_lengths_px))
    pixels_per_mm = avg_side_px / config.ARUCO_MARKER_SIZE_MM

    print(
        f"[process] ArUco marker ID {ids[0][0]} detected. "
        f"Avg side: {avg_side_px:.1f}px / {config.ARUCO_MARKER_SIZE_MM}mm → "
        f"{pixels_per_mm:.4f} px/mm"
    )
    return pixels_per_mm, pts


# ---------------------------------------------------------------------------
# 2d: Silhouette extraction
# ---------------------------------------------------------------------------

def extract_silhouette(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Threshold and extract the dominant tool contour as a clean binary mask.

    Steps:
      1. Detect scale from the ArUco marker.
      2. Convert to grayscale and apply binary threshold (invert so tool = white).
      3. Morphological closing fills small holes in the tool outline.
      4. Select the largest contour above CONTOUR_MIN_AREA.
      5. Draw filled contour onto a clean binary mask.

    Args:
        image: Cropped, undistorted image array (BGR).

    Returns:
        Tuple of:
          - binary_mask: uint8 array, tool pixels = 255, background = 0
          - largest_contour: the raw OpenCV contour (Nx1x2 array)
          - pixels_per_mm: scale factor for use in export
    """
    pixels_per_mm, aruco_corners = detect_scale(image)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Threshold: tool is darker than the bright light board background.
    # Adaptive mode computes a local threshold per region — handles light-
    # coloured tools and uneven lighting much better than a fixed cutoff.
    if config.USE_ADAPTIVE_THRESHOLD:
        thresh = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            config.ADAPTIVE_BLOCK_SIZE,
            config.ADAPTIVE_C,
        )
        print(f"[process] Adaptive threshold (block={config.ADAPTIVE_BLOCK_SIZE}, C={config.ADAPTIVE_C})")
    else:
        _, thresh = cv2.threshold(
            gray, config.THRESHOLD_VALUE, 255, cv2.THRESH_BINARY_INV
        )
        print(f"[process] Fixed threshold ({config.THRESHOLD_VALUE})")

    # Zero out border pixels to prevent undistortion edge artifacts from
    # being detected as contours (dark border pixels become white after invert).
    border = 60
    thresh[:border, :] = 0
    thresh[-border:, :] = 0
    thresh[:, :border] = 0
    thresh[:, -border:] = 0

    # Blank out the ArUco marker region so it is never picked up as a contour.
    # Expand the mask by 60px on each side to cover the marker border/tape.
    pts = aruco_corners.astype(np.int32)
    x, y, w, h = cv2.boundingRect(pts)
    padding = 60
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(thresh.shape[1], x + w + padding)
    y2 = min(thresh.shape[0], y + h + padding)
    thresh[y1:y2, x1:x2] = 0

    # Morphological closing fills small holes inside the tool outline.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        raise RuntimeError("No contours found. Check THRESHOLD_VALUE and lighting.")

    valid = [c for c in contours if cv2.contourArea(c) >= config.CONTOUR_MIN_AREA]
    if not valid:
        raise RuntimeError(
            f"No contour above CONTOUR_MIN_AREA={config.CONTOUR_MIN_AREA}. "
            "Lower the threshold or adjust CONTOUR_MIN_AREA in config.py."
        )

    binary_mask = np.zeros_like(gray, dtype=np.uint8)
    cv2.drawContours(binary_mask, valid, -1, 255, thickness=cv2.FILLED)

    # Expand the mask outward by OUTLINE_OFFSET_MM to add clearance for tool insertion.
    if config.OUTLINE_OFFSET_MM > 0:
        offset_px = int(round(config.OUTLINE_OFFSET_MM * pixels_per_mm))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * offset_px + 1, 2 * offset_px + 1))
        binary_mask = cv2.dilate(binary_mask, kernel)
        print(f"[process] Outline offset: {config.OUTLINE_OFFSET_MM}mm = {offset_px}px")

    print(f"[process] Found {len(valid)} tool contour(s), total area: {sum(cv2.contourArea(c) for c in valid):.0f} px²")
    return binary_mask, valid, pixels_per_mm


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_image(image_path: str) -> tuple[np.ndarray, float]:
    """Run the full Step 2 pipeline on an image file.

    Args:
        image_path: Path to the input JPEG.

    Returns:
        Tuple of (binary_mask, pixels_per_mm).
    """
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")

    image = undistort(image)
    image = crop(image)
    binary_mask, _, pixels_per_mm = extract_silhouette(image)

    return binary_mask, pixels_per_mm


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python process.py <image_path>")
        sys.exit(1)

    mask, scale = process_image(sys.argv[1])
    cv2.imwrite("debug_mask.png", mask)
    print(f"Mask saved to debug_mask.png  |  scale={scale:.4f} px/mm")
