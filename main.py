# main.py
# Entry point — orchestrates the full 4-step pipeline.
#
# Usage (on Raspberry Pi with camera):
#   python main.py
#
# Usage (development, supply a pre-taken image):
#   python main.py --image sample_images/wrench.jpg

import argparse
import sys

import process
import vectorize
import export


def run_pipeline(image_path: str | None = None) -> str:
    """Execute the complete tool-scanning pipeline.

    Args:
        image_path: Path to an existing image. If None, Step 1 (camera
                    capture) is triggered. Providing a path skips capture —
                    useful for development and testing without the Pi camera.

    Returns:
        Path to the generated SVG file.
    """
    # -----------------------------------------------------------------
    # Step 1: Capture
    # -----------------------------------------------------------------
    if image_path is None:
        try:
            from capture import capture_image
        except ImportError:
            print(
                "[main] ERROR: Picamera2 not available. "
                "Supply --image to use a pre-taken photo.",
                file=sys.stderr,
            )
            sys.exit(1)
        print("[main] Step 1: Capturing image...")
        image_path = capture_image()
    else:
        print(f"[main] Step 1: Using supplied image: {image_path}")

    # -----------------------------------------------------------------
    # Step 2: Image processing
    # -----------------------------------------------------------------
    print("[main] Step 2: Processing image...")
    binary_mask, pixels_per_mm = process.process_image(image_path)

    # -----------------------------------------------------------------
    # Step 3: Vectorize
    # -----------------------------------------------------------------
    print("[main] Step 3: Vectorizing silhouette...")
    svg_content = vectorize.bitmap_to_svg_string(binary_mask)

    # -----------------------------------------------------------------
    # Step 4: Export SVG
    # -----------------------------------------------------------------
    print("[main] Step 4: Exporting SVG...")
    svg_path = export.svg_to_file(svg_content, pixels_per_mm)

    print(f"\n[main] Done. SVG ready for laser cutter: {svg_path}")
    return svg_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aviation Tool Accountability System — shadow board SVG generator"
    )
    parser.add_argument(
        "--image",
        metavar="PATH",
        default=None,
        help="Path to a pre-captured image (skips camera capture). "
             "Use this for development without the Pi camera.",
    )
    args = parser.parse_args()
    run_pipeline(image_path=args.image)


if __name__ == "__main__":
    main()
