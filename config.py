# config.py
# All tunable parameters for the Aviation Tool Accountability System.
# Edit these values once during station setup. Do not scatter magic numbers
# through the pipeline modules — everything configurable lives here.

from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Camera calibration
# Generated once using cv2.calibrateCamera() with a checkerboard pattern.
# Replace the placeholder arrays below with your actual calibration output.
# Run: python camera_calibration.py
# ---------------------------------------------------------------------------

CAMERA_MATRIX = np.array([
    [3488.3671687699966, 0.0,                1159.3592103409471],
    [0.0,                3492.9736334855047,  638.97648859996  ],
    [0.0,                0.0,                   1.0            ],
], dtype=np.float64)

DISTORTION_COEFFICIENTS = np.array(
    [0.21752241760501959, 1.7477219532005852, 0.0010838858642741851,
     0.003647211855111366, -12.102889281028451], dtype=np.float64
)

# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

# Picamera2 capture resolution. Higher = more detail, slower processing.
# Pi Camera Module 3 native max is (4608, 2592). Start at half for speed.
IMAGE_RESOLUTION = (2304, 1296)

# Manual focus position for the top-down rig (Pi Camera Module 3 only).
# LensPosition: 0.0 = infinity, higher values = closer subject.
# Typical values for top-down setups:
#   ~5.0  = ~30 cm working distance
#   ~8.0  = ~20 cm working distance
#   ~12.0 = ~15 cm working distance
# Tune by capturing a test image and checking sharpness.
LENS_POSITION = 8.0

# ---------------------------------------------------------------------------
# Crop
# Applied after undistortion to remove edge artifacts.
# Format: (x, y, width, height) in pixels at IMAGE_RESOLUTION scale.
# Measure these values after running undistortion on a test frame.
# ---------------------------------------------------------------------------

CROP_BOUNDS = (200, 0, 2004, 1096)  # Full frame — no cropping

# ---------------------------------------------------------------------------
# ArUco scale detection
# Replace the ruler with a printed ArUco marker of known physical size.
# Tape the marker flat on the light board surface in view of the camera.
#
# To generate and print a marker: python generate_aruco_marker.py
# Use a ruler to verify the printout dimensions match ARUCO_MARKER_SIZE_MM
# before using the system.
# ---------------------------------------------------------------------------

# ArUco dictionary to use. DICT_4X4_50 is fastest to detect and lowest
# false-positive rate — sufficient for a single reference marker.
# Must match what was used in generate_aruco_marker.py.
ARUCO_DICT_NAME = "DICT_4X4_50"

# Which marker ID to expect in the frame (0–49 for DICT_4X4_50).
ARUCO_MARKER_ID = 0

# Physical side length of the printed ArUco marker in millimetres.
# Measure the actual printout with a ruler and set this accurately —
# any error here propagates directly to every SVG dimension.
ARUCO_MARKER_SIZE_MM = 20.0

# ---------------------------------------------------------------------------
# Silhouette extraction
# ---------------------------------------------------------------------------

# Use adaptive thresholding instead of a fixed global threshold.
# Recommended for light-coloured tools or uneven lighting — it computes
# a local threshold per region so low-contrast tools are still detected.
# Set to False to use the fixed THRESHOLD_VALUE below instead.
USE_ADAPTIVE_THRESHOLD = True

# Adaptive threshold block size — neighbourhood area used to compute each
# local threshold. Must be an odd number. Larger = smoother but less sensitive
# to fine detail. 51 works well for most tools at 18" camera height.
ADAPTIVE_BLOCK_SIZE = 51

# Constant subtracted from the local mean. Higher = less sensitive (fewer
# false positives from light board texture); lower = more sensitive.
ADAPTIVE_C = 8

# Fixed threshold fallback (used only when USE_ADAPTIVE_THRESHOLD = False).
# 0–255. Background (lit) → white; tool → black.
THRESHOLD_VALUE = 127

# Minimum contour area (pixels²) to be considered a tool.
# Eliminates dust, reflections, and small noise contours.
# Lowered to 2000 to catch small pieces like sockets.
CONTOUR_MIN_AREA = 2000

# Uniform outward expansion applied to the tool silhouette before vectorizing.
# Adds wiggle room so the tool drops into the foam insert easily.
# Increase if tools fit too snugly; decrease if cutouts look too loose.
OUTLINE_OFFSET_MM = 0

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

IMAGE_OUTPUT_PATH = "/home/tomas/Desktop/aviation-tool-accountability-main/images"
SVG_OUTPUT_PATH   = "/home/tomas/Desktop/captures"

# Physical dimensions of the light board in millimetres.
# Used to set the SVG canvas size (8" x 11.5").
LIGHT_BOARD_WIDTH_MM  = 292.1   # 11.5 inches
LIGHT_BOARD_HEIGHT_MM = 203.2   #  8.0 inches

# ---------------------------------------------------------------------------
# Scanning app (app.py)
# ---------------------------------------------------------------------------

# Where the app suggests saving finished drawers (the user can pick any other
# folder inside the app, and it remembers the last one used).
DEFAULT_SAVE_ROOT = str(Path.home() / "Desktop" / "ToolAerie")

# Scratch space used while scanning, before the user presses Save.
WORK_DIR = str(Path.home() / ".cache" / "toolaerie" / "work")

# Space left between neighbouring tools in the combined SVG, in millimetres.
DEFAULT_TOOL_GAP_MM = 10.0

# Empty margin around the edge of the combined SVG, in millimetres.
COMBINED_MARGIN_MM = 5.0

# Version numbers written into each saved drawer (see FORMAT.md). Bump
# MANIFEST_SCHEMA_VERSION whenever the layout of drawer.json changes.
APP_VERSION = "0.3.0"
MANIFEST_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Auto-scan (autoscan.py): hands-free scanning when a tool is placed and still.
# Tune these if it triggers too eagerly or not at all with your light board.
# ---------------------------------------------------------------------------

AUTOSCAN_DROP = 35            # how much darker than the empty board a pixel must be (0-255)
AUTOSCAN_PRESENT_FRAC = 0.0015  # fraction of the picture that must be darker to count as a tool
AUTOSCAN_EMPTY_FRAC = 0.0005    # below this the board counts as empty
AUTOSCAN_STABLE_SECS = 1.0    # picture must hold still this long before scanning
AUTOSCAN_STILL_LEVEL = 1.5    # "holding still" = average change below this (0-255 scale)
AUTOSCAN_LEARN_SECS = 1.2     # time spent learning the empty board
AUTOSCAN_CLEAR_SECS = 0.8     # board must be empty this long before re-arming

# Pi camera: run the always-on live view in the same "still" configuration that
# capture.py (the original, calibrated capture) uses. Keep True so every scan has
# exactly the field of view the camera calibration was made for.
PREVIEW_USES_STILL_CONFIG = True
