# camera_calibration.py
# One-time camera calibration using a printed checkerboard pattern.
#
# CAPTURE mode — guided photo session:
#   python camera_calibration.py --capture
#   Follow the prompts to place the checkerboard at each position.
#   Press Enter to capture, 'q' to quit early.
#
# CALIBRATE mode — compute calibration from saved images:
#   python camera_calibration.py
#
# After calibrating, copy the printed values into config.py.
#
# Requires: OpenCV, NumPy, picamera2 (capture mode only)

import sys
import glob
import time
from pathlib import Path
import cv2
import numpy as np


# Checkerboard inner corner dimensions — match your printed pattern.
CHECKERBOARD = (9, 6)  # (columns, rows) of inner corners

CALIBRATION_DIR = "calibration_images"

# Guided positions to ensure full-frame coverage for edge distortion correction.
POSITIONS = [
    # Corners — 3 shots each, keep checkerboard FULLY inside frame
    "TOP-LEFT corner — checkerboard fully inside frame, slight tilt",
    "TOP-LEFT corner — move slightly more toward center, different tilt",
    "TOP-LEFT corner — rotate checkerboard 45 degrees",
    "TOP-RIGHT corner — checkerboard fully inside frame, slight tilt",
    "TOP-RIGHT corner — move slightly more toward center, different tilt",
    "TOP-RIGHT corner — rotate checkerboard 45 degrees",
    "BOTTOM-LEFT corner — checkerboard fully inside frame, slight tilt",
    "BOTTOM-LEFT corner — move slightly more toward center, different tilt",
    "BOTTOM-LEFT corner — rotate checkerboard 45 degrees",
    "BOTTOM-RIGHT corner — checkerboard fully inside frame, slight tilt",
    "BOTTOM-RIGHT corner — move slightly more toward center, different tilt",
    "BOTTOM-RIGHT corner — rotate checkerboard 45 degrees",
    # Edges — 2 shots each
    "TOP EDGE center — checkerboard fully inside frame, slight tilt",
    "TOP EDGE center — different tilt",
    "BOTTOM EDGE center — checkerboard fully inside frame, slight tilt",
    "BOTTOM EDGE center — different tilt",
    "LEFT EDGE center — checkerboard fully inside frame, slight tilt",
    "LEFT EDGE center — different tilt",
    "RIGHT EDGE center — checkerboard fully inside frame, slight tilt",
    "RIGHT EDGE center — different tilt",
    # Center — variety of angles
    "CENTER — flat",
    "CENTER — tilted left",
    "CENTER — tilted right",
    "CENTER — tilted toward you",
    "CENTER — tilted away from you",
    "CENTER — rotated 45 degrees",
    "CENTER — rotated 90 degrees",
    "CENTER — tilted left and rotated",
    "CENTER — tilted right and rotated",
]


def capture_calibration_images(images_dir: str = CALIBRATION_DIR) -> None:
    try:
        from picamera2 import Picamera2
    except ImportError:
        print("ERROR: picamera2 not available. Run on the Raspberry Pi.")
        sys.exit(1)

    output_dir = Path(images_dir)
    output_dir.mkdir(exist_ok=True)

    print("=== Calibration Photo Capture ===")
    print(f"Saving to: {output_dir.resolve()}")
    print(f"Total positions to capture: {len(POSITIONS)}")
    print()
    print("IMPORTANT: Keep the ENTIRE checkerboard inside the frame in every shot.")
    print("Slightly tilt it in every shot — never perfectly flat/parallel.")
    print("For corners/edges, move close but make sure no squares are cut off.")
    print()

    picam2 = Picamera2()
    still_config = picam2.create_still_configuration(
        main={"size": (2304, 1296), "format": "RGB888"},
        buffer_count=1,
    )
    picam2.configure(still_config)
    picam2.start()
    time.sleep(2)

    preview_path = str(output_dir / "preview.jpg")
    captured = 0
    for i, position in enumerate(POSITIONS):
        print(f"[{i+1}/{len(POSITIONS)}] Place checkerboard at: {position}")
        print("  Commands: Enter=capture, 'p'=preview, 'q'=quit")

        while True:
            try:
                user_input = input("  > ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                user_input = "q"

            if user_input == "q":
                picam2.stop()
                picam2.close()
                print(f"\nCaptured {captured} images.")
                print("Now run:  python camera_calibration.py")
                return
            elif user_input == "p":
                picam2.capture_file(preview_path)
                print(f"  Preview saved — open to check framing: {preview_path}")
                print("  Then press Enter to capture or 'p' to preview again.")
            else:
                # Enter — take the real shot
                output_path = str(output_dir / f"calib_{i+1:02d}.jpg")
                picam2.capture_file(output_path)
                captured += 1
                print(f"  Saved: {output_path}\n")
                break

    picam2.stop()
    picam2.close()

    print(f"\nCaptured {captured} images.")
    print(f"Now run:  python camera_calibration.py")


def calibrate(images_dir: str = CALIBRATION_DIR) -> None:
    pattern_size = CHECKERBOARD
    objp = np.zeros((pattern_size[0] * pattern_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0 : pattern_size[0], 0 : pattern_size[1]].T.reshape(-1, 2)

    obj_points = []
    img_points = []

    image_paths = glob.glob(f"{images_dir}/*.jpg") + glob.glob(f"{images_dir}/*.png")
    if not image_paths:
        print(f"No images found in {images_dir}/")
        print("Run:  python camera_calibration.py --capture")
        sys.exit(1)

    print(f"Found {len(image_paths)} calibration image(s).")
    image_size = None

    for path in sorted(image_paths):
        img = cv2.imread(path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        if image_size is None:
            image_size = (gray.shape[1], gray.shape[0])

        ret, corners = cv2.findChessboardCorners(gray, pattern_size, None)
        if ret:
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            obj_points.append(objp)
            img_points.append(corners_refined)
            print(f"  OK: {path}")
        else:
            print(f"  SKIP (corners not found): {path}")

    if len(obj_points) < 5:
        print(f"\nNeed at least 5 usable images; only {len(obj_points)} found.")
        print("Run:  python camera_calibration.py --capture")
        sys.exit(1)

    print(f"\nCalibrating with {len(obj_points)} images...")
    rms, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, image_size, None, None
    )

    print(f"Reprojection error (RMS): {rms:.4f}  (< 0.5 is good, < 1.0 is acceptable)")
    print("\n--- Copy these values into config.py ---\n")
    print("CAMERA_MATRIX = np.array(")
    print(repr(camera_matrix.tolist()) + ", dtype=np.float64)")
    print("\nDISTORTION_COEFFICIENTS = np.array(")
    print(repr(dist_coeffs.flatten().tolist()) + ", dtype=np.float64)")


if __name__ == "__main__":
    if "--capture" in sys.argv:
        capture_calibration_images()
    else:
        images_dir = sys.argv[1] if len(sys.argv) > 1 else CALIBRATION_DIR
        calibrate(images_dir)
