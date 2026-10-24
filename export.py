# export.py
# Step 4 — Patch vtracer SVG output with real-world mm dimensions and save.
#
# vtracer outputs SVG with pixel-based width/height. This module:
#   1. Parses the SVG
#   2. Sets width/height to mm using the pixels_per_mm scale factor
#   3. Converts filled paths to hairline strokes for laser cutting
#   4. Saves the final file
#
# Uses Python stdlib xml.etree.ElementTree — no extra dependencies.

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import config

_SVG_NS = "http://www.w3.org/2000/svg"


def svg_to_file(
    svg_content: str,
    pixels_per_mm: float,
    output_filename: str | None = None,
) -> str:
    """Patch SVG units to mm and write to the output directory.

    Args:
        svg_content: Raw SVG string from vectorize.bitmap_to_svg_string().
                     Width/height are in pixels; viewBox is in pixel space.
        pixels_per_mm: Scale factor from process.detect_scale(). Used to
                       convert pixel dimensions to real-world mm dimensions.
        output_filename: Desired output filename. Defaults to a timestamped
                         file in SVG_OUTPUT_PATH from config.

    Returns:
        Absolute path to the saved SVG file.
    """
    if output_filename is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_filename = f"tool_{timestamp}.svg"

    output_dir = Path(config.SVG_OUTPUT_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (output_dir / Path(output_filename).name).resolve()

    # Register the SVG namespace so ET preserves it cleanly on write.
    ET.register_namespace("", _SVG_NS)

    root = ET.fromstring(svg_content)

    # Read pixel dimensions from the viewBox (most reliable source).
    viewbox = root.get("viewBox", "")
    parts = viewbox.split()
    if len(parts) == 4:
        width_px  = float(parts[2])
        height_px = float(parts[3])
    else:
        # Fall back to width/height attributes if viewBox is absent.
        width_px  = float(root.get("width",  "100").replace("px", ""))
        height_px = float(root.get("height", "100").replace("px", ""))

    width_mm  = width_px  / pixels_per_mm
    height_mm = height_px / pixels_per_mm

    # Set real-world mm dimensions and explicitly set the viewBox.
    # Without viewBox, Illustrator treats path coords as raw pixels
    # (1px = 0.35mm), making the tool appear ~3x too large.
    root.set("width",   f"{width_mm:.4f}mm")
    root.set("height",  f"{height_mm:.4f}mm")
    root.set("viewBox", f"0 0 {width_px:.4f} {height_px:.4f}")

    # Convert vtracer's filled paths to hairline cut lines for the laser.
    # Also strip the outer border rectangle subpath that vtracer adds —
    # any subpath starting at or near (0,0) is the background rectangle,
    # not the tool outline.
    stroke_width_px = 0.1 * pixels_per_mm
    for elem in root.iter(f"{{{_SVG_NS}}}path"):
        d = elem.get("d", "")
        # Split compound path into subpaths and drop the border rectangle.
        subpaths = re.split(r"Z\s*", d.strip())
        tool_parts = []
        for sp in subpaths:
            sp = sp.strip()
            if not sp:
                continue
            m = re.match(r"M\s*([\d.eE+-]+)\s+([\d.eE+-]+)", sp)
            if m and float(m.group(1)) < 5 and float(m.group(2)) < 5:
                continue  # skip border rectangle
            tool_parts.append(sp + " Z")
        elem.set("d",            " ".join(tool_parts))
        elem.set("fill",         "none")
        elem.set("stroke",       "black")
        elem.set("stroke-width", f"{stroke_width_px:.4f}")

    tree = ET.ElementTree(root)
    tree.write(str(output_path), xml_declaration=True, encoding="unicode")

    print(
        f"[export] SVG saved: {output_path}  "
        f"({width_mm:.1f}mm × {height_mm:.1f}mm)"
    )
    return str(output_path)


if __name__ == "__main__":
    print("export.py has no standalone demo (requires SVG content from vectorize.py).")
    print("Run: python main.py --image sample_images/your_tool.jpg")
