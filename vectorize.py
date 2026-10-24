# vectorize.py
# Step 3 — Convert binary bitmap silhouette to SVG using vtracer.
#
# Requires: vtracer (pip install vtracer)
#   vtracer is a Rust-based tracer with pre-built ARM64 wheels — no C
#   compilation needed. Drop-in replacement for pypotrace.
#   GitHub: https://github.com/visioncortex/vtracer
#
# vtracer takes an image file and returns an SVG string.
# Coordinates in the SVG are in pixel space; scale to mm is applied in export.py.

import os
import tempfile

import cv2
import numpy as np
import vtracer


def bitmap_to_svg_string(binary_mask: np.ndarray) -> str:
    """Trace a binary silhouette mask into an SVG string using vtracer.

    Saves the mask to a temporary PNG, runs vtracer, returns the SVG content.
    Coordinates in the returned SVG are in pixel space — export.py applies
    the pixels_per_mm scale to produce mm units in the final file.

    Args:
        binary_mask: uint8 array from process.extract_silhouette().
                     Tool pixels = 255, background = 0.

    Returns:
        SVG content as a string (pixel coordinates, needs scaling in export.py).
    """
    # Crop mask to the bounding box of white pixels so vtracer works on a
    # tight canvas. The SVG output will be sized to the tool, not the full image.
    white_pixels = np.where(binary_mask > 0)
    if len(white_pixels[0]) > 0:
        padding = 10
        y_min = max(0, int(white_pixels[0].min()) - padding)
        y_max = min(binary_mask.shape[0], int(white_pixels[0].max()) + padding)
        x_min = max(0, int(white_pixels[1].min()) - padding)
        x_max = min(binary_mask.shape[1], int(white_pixels[1].max()) + padding)
        binary_mask = binary_mask[y_min:y_max, x_min:x_max]

    # Write mask to a temp PNG — vtracer works on image files.
    tmp_in  = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
    tmp_in.close()
    tmp_out.close()

    try:
        cv2.imwrite(tmp_in.name, binary_mask)

        vtracer.convert_image_to_svg_py(
            tmp_in.name,
            tmp_out.name,
            colormode="binary",     # black-and-white tracing
            filter_speckle=4,       # suppress noise smaller than this many pixels
            mode="spline",          # smooth Bézier splines (vs "polygon")
            corner_threshold=60,    # angle below which corners are preserved (degrees)
            length_threshold=4.0,   # minimum path segment length
            splice_threshold=45,    # curve splicing angle threshold
            path_precision=3,       # decimal places in path data
        )

        with open(tmp_out.name, "r") as f:
            svg_content = f.read()

    finally:
        os.unlink(tmp_in.name)
        os.unlink(tmp_out.name)

    print(f"[vectorize] vtracer trace complete ({len(svg_content)} bytes)")
    return svg_content


if __name__ == "__main__":
    import sys

    mask_path = sys.argv[1] if len(sys.argv) > 1 else "debug_mask.png"
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        print(f"Could not load {mask_path}")
        sys.exit(1)

    svg = bitmap_to_svg_string(mask)
    with open("debug_vectorize.svg", "w") as f:
        f.write(svg)
    print("Saved debug_vectorize.svg")
