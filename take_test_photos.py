# take_test_photos.py
# Capture a series of test photos of tools and save them to sample_images/.
#
# Run on the Raspberry Pi with the camera connected:
#   python take_test_photos.py
#
# Each press of Enter captures one photo. Press Ctrl-C or type 'q' to stop.
# Photos are saved as sample_images/tool_001.jpg, tool_002.jpg, etc.
# Existing files are never overwritten — the counter always picks up where
# it left off.
#
# After capturing, test the processing pipeline on any saved image:
#   python main.py --image sample_images/tool_001.jpg

import sys
import time
from pathlib import Path


OUTPUT_DIR = Path(__file__).parent / "sample_images"


def _next_index(output_dir: Path) -> int:
    """Return the next available tool_NNN index (1-based, no gaps)."""
    existing = sorted(output_dir.glob("tool_*.jpg"))
    if not existing:
        return 1
    last = existing[-1].stem  # e.g. "tool_007"
    try:
        return int(last.split("_")[1]) + 1
    except (IndexError, ValueError):
        return len(existing) + 1


def take_test_photos() -> None:
    try:
        from capture import capture_image
    except ImportError:
        print(
            "ERROR: Picamera2 not available.\n"
            "This script must be run on a Raspberry Pi with the camera module.\n"
            "On a development machine, place existing JPEGs in sample_images/ manually\n"
            "and test with:  python main.py --image sample_images/tool_001.jpg",
            file=sys.stderr,
        )
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("=== Test Photo Capture ===")
    print(f"Photos will be saved to: {OUTPUT_DIR.resolve()}")
    print()
    print("Tips for good photos:")
    print("  - Place the ArUco marker flat on the light board (print via: python generate_aruco_marker.py)")
    print("  - Use the light board backlight so the tool appears as a dark silhouette")
    print("  - One tool per photo, centred in the frame")
    print("  - Keep the camera height fixed (LensPosition set in config.py)")
    print()
    print("Press Enter to capture a photo. Type 'q' + Enter to quit.\n")

    while True:
        try:
            user_input = input("Ready — press Enter to capture (or 'q' to quit): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nDone.")
            break

        if user_input == "q":
            print("Done.")
            break

        idx = _next_index(OUTPUT_DIR)
        output_path = OUTPUT_DIR / f"tool_{idx:03d}.jpg"

        print(f"  Capturing → {output_path.name} ...")
        try:
            capture_image(str(output_path))
        except Exception as exc:
            print(f"  ERROR during capture: {exc}", file=sys.stderr)
            continue

        print(f"  Saved: {output_path}")
        print(f"  Test with: python main.py --image {output_path}\n")


if __name__ == "__main__":
    take_test_photos()
