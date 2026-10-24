# capture.py
# Step 1 — Capture a high-resolution image of the tool on the light board.
# Requires: python3-picamera2 (sudo apt install python3-picamera2)
#           libcamera (included in Raspberry Pi OS Bookworm by default)
# Only runs on a Raspberry Pi with the camera module connected via CSI.
#
# Based on official Picamera2 examples:
# https://github.com/raspberrypi/picamera2/tree/main/examples

import time
from pathlib import Path

from picamera2 import Picamera2
from libcamera import controls

import config


def capture_image(output_path: str | None = None) -> str:
    """Capture one frame from the Pi camera and save it to disk.

    Uses manual focus at a fixed LensPosition suited to the top-down rig.
    Manual focus is preferred over continuous AF for repeatability — once
    LENS_POSITION is tuned for your working distance it never needs to change
    unless the camera height is adjusted.

    Args:
        output_path: Where to save the JPEG. Defaults to a timestamped file
                     in the current directory.

    Returns:
        Absolute path to the saved image file.
    """
    if output_path is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        images_dir = Path(config.IMAGE_OUTPUT_PATH)
        images_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(images_dir / f"capture_{timestamp}.jpg")

    output_path = str(Path(output_path).resolve())

    picam2 = Picamera2()

    # Still configuration: full-resolution capture with no framerate constraint.
    # buffer_count=1 reduces memory use for single-shot captures.
    # Pattern from official example: capture_full_res_jpeg.py
    still_config = picam2.create_still_configuration(
        main={"size": config.IMAGE_RESOLUTION, "format": "RGB888"},
        buffer_count=1,
    )
    picam2.configure(still_config)
    picam2.start()

    # Allow AGC and AWB to converge before locking controls.
    # 2 seconds is the official recommendation from the Picamera2 manual.
    time.sleep(2)

    # Lock white balance to Indoor mode for consistent artificial lighting.
    # Change AwbMode to match your light board's light source:
    #   controls.AwbModeEnum.Indoor       — warm white LED / halogen
    #   controls.AwbModeEnum.Fluorescent  — fluorescent / cool white LED
    #   controls.AwbModeEnum.Daylight     — daylight-balanced LED
    picam2.set_controls({
        "AwbEnable": True,
        "AwbMode": controls.AwbModeEnum.Indoor,
    })
    time.sleep(0.5)

    # Manual focus — only supported on Pi Camera Module 3 (motorised lens).
    # Camera Module 2 has fixed focus and will raise RuntimeError here — silently
    # skipped so the same code works with both camera modules.
    try:
        picam2.set_controls({
            "AfMode": controls.AfModeEnum.Manual,
            "LensPosition": config.LENS_POSITION,
        })
        time.sleep(0.3)
        print("[capture] Manual focus set (Camera Module 3)")
    except RuntimeError:
        print("[capture] Fixed focus camera detected — skipping LensPosition (Camera Module 2)")

    picam2.capture_file(output_path)
    picam2.stop()
    picam2.close()

    print(f"[capture] Saved: {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick standalone test — run on the Pi to verify camera is working.
    path = capture_image()
    print(f"Capture complete: {path}")
