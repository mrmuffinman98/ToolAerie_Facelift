# generate_aruco_marker.py
# Generates a printable ArUco marker PNG for use as the scale reference on
# the light board. Run once, print the output, and tape it flat to the board.
#
# Requires: opencv-contrib-python-headless (NOT opencv-python)
#   pip install opencv-contrib-python-headless
#
# Usage:
#   python generate_aruco_marker.py
#
# After printing:
#   - Measure the printed marker with a ruler.
#   - Verify it matches ARUCO_MARKER_SIZE_MM in config.py.
#   - If not, scale the image in your print settings and re-measure.

import cv2
import numpy as np

import config

# Output image size in pixels (larger = higher DPI when printed).
# At 300 DPI, 600px → 50.8mm. Size the image to match your target print size.
MARKER_PX = 600

_ARUCO_DICT_MAP = {
    "DICT_4X4_50":        cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100":       cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50":        cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100":       cv2.aruco.DICT_5X5_100,
    "DICT_6X6_50":        cv2.aruco.DICT_6X6_50,
    "DICT_ARUCO_ORIGINAL": cv2.aruco.DICT_ARUCO_ORIGINAL,
}


def generate_marker(
    dict_name: str = config.ARUCO_DICT_NAME,
    marker_id: int = config.ARUCO_MARKER_ID,
    size_px: int = MARKER_PX,
    output_path: str = "aruco_scale_marker.png",
) -> None:
    if dict_name not in _ARUCO_DICT_MAP:
        raise KeyError(f"Unknown dictionary: {dict_name}")

    aruco_dict = cv2.aruco.getPredefinedDictionary(_ARUCO_DICT_MAP[dict_name])

    # OpenCV 4.7+ uses generateImageMarker; older 4.x uses drawMarker.
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker_img = cv2.aruco.generateImageMarker(aruco_dict, marker_id, size_px)
    else:
        marker_img = np.zeros((size_px, size_px), dtype=np.uint8)
        cv2.aruco.drawMarker(aruco_dict, marker_id, size_px, marker_img, 1)

    # Add a white border so the marker has contrast against any background.
    border_px = size_px // 10
    marker_with_border = cv2.copyMakeBorder(
        marker_img, border_px, border_px, border_px, border_px,
        cv2.BORDER_CONSTANT, value=255,
    )

    cv2.imwrite(output_path, marker_with_border)

    print(f"Saved: {output_path}")
    print(f"Dictionary : {dict_name}")
    print(f"Marker ID  : {marker_id}")
    print(f"Target size: {config.ARUCO_MARKER_SIZE_MM} mm (set in config.py)")
    print()
    print("IMPORTANT: After printing, measure the marker with a ruler.")
    print(f"The black square (excluding white border) must be exactly "
          f"{config.ARUCO_MARKER_SIZE_MM} mm on each side.")
    print("If it is not, adjust your printer's scaling settings.")


if __name__ == "__main__":
    generate_marker()
