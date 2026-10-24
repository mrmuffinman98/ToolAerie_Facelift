# session.py
# A scanning session = one folder holding everything for one drawer. It is
# built in a scratch folder while scanning and copied to the user's chosen
# location by save_to():
#
#   <save location>/<drawer name>/
#       photos/tool_01.jpg ...      the prepared photo of each confirmed tool
#       tools/tool_01.svg ...       one real-size SVG per tool
#       <session name>_combined.svg all tools laid out in one file, with gaps
#       <drawer name>.toolaerie     everything above in ONE file (zip) for the board builder
#       drawer.json                 manifest: geometry + measurements, see FORMAT.md
#
# The combined SVG uses millimetres as its user unit and real-world
# width/height, so it opens at 1:1 in Illustrator, Inkscape, LightBurn, etc.

from __future__ import annotations

import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2

import config
import export
import scan_service
import shape_info
import storage

_SVG_NS = "http://www.w3.org/2000/svg"
_STROKE_MM = 0.1  # hairline cut width, same as the single-tool export


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("_")
    return s or "tool"


@dataclass
class ToolRecord:
    index: int
    name: str
    svg_file: str            # relative to session folder
    photo_file: str
    width_mm: float          # size of the SVG canvas (tool + small padding)
    height_mm: float
    pixels_per_mm: float
    uid: str = ""            # stable id, survives renames and re-saves
    clearance_mm: float = 0.0        # clearance baked into the SVG file
    geometry: dict = field(default_factory=dict)   # shape_info.analyze_shape(): no clearance
    warnings: list = field(default_factory=list)
    scanned: str = ""
    # Outline in mm relative to the SVG canvas origin; used for the layout
    # preview only, not for the cut file.
    preview_outlines_mm: list = field(default_factory=list, repr=False)


@dataclass
class Placement:
    tool: ToolRecord
    x_mm: float
    y_mm: float


class Session:
    """One drawer being scanned.

    Everything is written into a scratch folder (root/<name>) while scanning.
    Nothing reaches the user's chosen folder until save_to() is called.
    """

    def __init__(self, title: str | None = None, root: str | None = None):
        root_dir = Path(root).expanduser() if root else storage.new_work_root()
        self._owned_root = None if root else root_dir   # scratch root we created
        self.title = storage.folder_name(title or time.strftime("Drawer %Y-%m-%d %H%M"))
        base = storage.file_stem(self.title)
        folder, n = root_dir / base, 2
        while folder.exists():               # never overwrite an earlier session
            folder = root_dir / f"{base}_{n}"
            n += 1
        self.name = folder.name              # file-friendly name (no spaces)
        self.folder = folder
        (folder / "photos").mkdir(parents=True)
        (folder / "tools").mkdir()
        self.tools: list[ToolRecord] = []
        self.gap_mm = float(config.DEFAULT_TOOL_GAP_MM)
        self.max_width_mm: float | None = None   # None = one column
        self.created = time.strftime("%Y-%m-%d %H:%M:%S")
        self.dirty = False                   # unsaved work?
        self.saved_to: Path | None = None
        self.toolbox_name: str | None = None
        self.saved_at: str | None = None
        self._save_manifest()

    def cleanup(self) -> None:
        """Delete the scratch files (after saving, or when the user discards)."""
        storage.remove_tree(self._owned_root or self.folder)

    # --- tools -----------------------------------------------------------

    def add_tool(self, result: scan_service.ScanResult, name: str | None = None) -> ToolRecord:
        idx = (self.tools[-1].index + 1) if self.tools else 1
        tool_name = (name or "").strip() or f"tool_{idx:02d}"
        stem = f"tool_{idx:02d}_{_slug(tool_name)}" if name and name.strip() else f"tool_{idx:02d}"

        photo = self.folder / "photos" / f"{stem}.jpg"
        cv2.imwrite(str(photo), result.prepared, [cv2.IMWRITE_JPEG_QUALITY, 92])

        raw_svg = scan_service.to_svg_string(result)
        svg_path = export.svg_to_file(raw_svg, result.pixels_per_mm,
                                      output_filename=f"{stem}.svg",
                                      output_dir=str(self.folder / "tools"))

        _, w_px, h_px = export.tool_paths(raw_svg)
        ppm = result.pixels_per_mm

        # Where the vtracer canvas sits in the photo (mirrors vectorize.py's
        # 10 px padded bounding box) so outlines can be redrawn for preview.
        allpts = [o for o in result.outlines]
        x0 = max(0, min(o[:, 0].min() for o in allpts) - 10)
        y0 = max(0, min(o[:, 1].min() for o in allpts) - 10)
        preview = [((o - (x0, y0)) / ppm).round(2).tolist() for o in allpts]

        rec = ToolRecord(idx, tool_name, str(Path(svg_path).relative_to(self.folder)),
                         str(photo.relative_to(self.folder)),
                         w_px / ppm, h_px / ppm, ppm,
                         uid=uuid.uuid4().hex[:12],
                         clearance_mm=result.settings.offset_mm,
                         geometry=shape_info.analyze_shape(result.raw_outlines, ppm),
                         warnings=list(result.warnings),
                         scanned=time.strftime("%Y-%m-%d %H:%M:%S"),
                         preview_outlines_mm=preview)
        self.tools.append(rec)
        self.dirty = True
        self._save_manifest()
        return rec

    def remove_last(self) -> ToolRecord | None:
        """Undo the most recent confirm."""
        if not self.tools:
            return None
        rec = self.tools.pop()
        for rel in (rec.svg_file, rec.photo_file):
            (self.folder / rel).unlink(missing_ok=True)
        self.dirty = bool(self.tools)
        self._save_manifest()
        return rec

    # --- combined layout -------------------------------------------------

    def layout(self, gap_mm: float | None = None,
               max_width_mm: float | None = None) -> tuple[list[Placement], float, float]:
        """Place the tools. Returns (placements, total_width_mm, total_height_mm).

        Default is one column, stacked top to bottom with gap_mm between
        tools. If max_width_mm is given, tools wrap into rows instead.
        """
        gap = self.gap_mm if gap_mm is None else gap_mm
        m = config.COMBINED_MARGIN_MM
        placements: list[Placement] = []
        x = y = m
        row_h = 0.0
        used_w = 0.0
        for t in self.tools:
            if max_width_mm and x > m and x + t.width_mm + m > max_width_mm:
                x = m
                y += row_h + gap
                row_h = 0.0
            placements.append(Placement(t, x, y))
            used_w = max(used_w, x + t.width_mm)
            row_h = max(row_h, t.height_mm)
            if max_width_mm:
                x += t.width_mm + gap
            else:
                y += t.height_mm + gap
                row_h = 0.0
        total_h = (y + row_h if max_width_mm else y - gap) + m if self.tools else 2 * m
        return placements, used_w + m, total_h

    def export_combined(self, gap_mm: float | None = None,
                        max_width_mm: float | None = None) -> Path:
        """Write <session>_combined.svg and return its path."""
        if not self.tools:
            raise ValueError("No tools to export yet.")
        if gap_mm is not None:
            self.gap_mm = gap_mm
        placements, W, H = self.layout(self.gap_mm, max_width_mm)

        ET.register_namespace("", _SVG_NS)
        root = ET.Element(f"{{{_SVG_NS}}}svg", {
            "width": f"{W:.3f}mm", "height": f"{H:.3f}mm",
            "viewBox": f"0 0 {W:.3f} {H:.3f}",   # 1 user unit = 1 mm
        })
        for p in placements:
            raw = (self.folder / p.tool.svg_file).read_text()
            ppm = p.tool.pixels_per_mm
            sroot = ET.fromstring(raw)
            g = ET.SubElement(root, f"{{{_SVG_NS}}}g", {
                "id": _slug(p.tool.name),
                # tool paths are in photo pixels: scale to mm, then move
                "transform": f"translate({p.x_mm:.3f} {p.y_mm:.3f}) scale({1 / ppm:.6f})",
            })
            ET.SubElement(g, f"{{{_SVG_NS}}}title").text = p.tool.name
            for el in sroot.iter(f"{{{_SVG_NS}}}path"):
                ET.SubElement(g, f"{{{_SVG_NS}}}path", {
                    "d": el.get("d", ""), "fill": "none", "stroke": "black",
                    "stroke-width": f"{_STROKE_MM * ppm:.4f}",
                })
        out = self.folder / f"{self.name}_combined.svg"
        ET.ElementTree(root).write(out, xml_declaration=True, encoding="unicode")
        self.max_width_mm = max_width_mm
        self._save_manifest(combined=out.name)
        return out

    def save_to(self, dest: str | Path, replace: bool = False) -> Path:
        """Write the combined SVG and the .toolaerie package, then copy the whole
        drawer to dest.

        Returns the path of the saved combined SVG inside dest. Marks the
        session as saved only after the copy has completed and been checked.
        """
        dest = Path(dest)
        self.toolbox_name = dest.parent.name
        self.saved_at = time.strftime("%Y-%m-%d %H:%M:%S")
        combined = self.export_combined(self.gap_mm, self.max_width_mm)
        package = self.write_package()
        storage.copy_drawer(self.folder, dest, replace=replace)
        for made in (combined, package):
            copy = dest / made.name
            if not copy.exists() or copy.stat().st_size != made.stat().st_size:
                raise OSError(f"{made.name} could not be verified in {dest}")
        self.saved_to = dest
        self.dirty = False
        return dest / combined.name

    def write_package(self) -> Path:
        """Zip the drawer into <Drawer name>.toolaerie (one file to move around)."""
        for old in self.folder.glob("*.toolaerie"):
            old.unlink()
        out = self.folder / f"{self.title}.toolaerie"
        storage.make_package(self.folder, out)
        return out

    # --- bookkeeping -----------------------------------------------------

    def _save_manifest(self, **extra) -> None:
        """drawer.json: the hand-off file for the board builder (see FORMAT.md)."""
        tools = []
        for t in self.tools:
            tools.append({
                "id": t.uid,
                "index": t.index,
                "name": t.name,
                "files": {"svg": t.svg_file, "photo": t.photo_file},
                "scale_px_per_mm": round(t.pixels_per_mm, 4),
                "clearance_mm_in_svg": t.clearance_mm,
                "svg_size_mm": [round(t.width_mm, 3), round(t.height_mm, 3)],
                "geometry": t.geometry,
                "warnings": t.warnings,
                "scanned": t.scanned,
            })
        data = {
            "format": "toolaerie-drawer",
            "schema_version": config.MANIFEST_SCHEMA_VERSION,
            "app_version": config.APP_VERSION,
            "units": "mm",
            "coordinates": "x right, y down; polygon_mm origin is the tool's bounding-box "
                           "top-left corner; geometry has NO clearance added",
            "drawer": {
                "name": self.title,
                "toolbox": self.toolbox_name,
                "created": self.created,
                "saved": self.saved_at,
            },
            "scan": {"marker_size_mm": config.ARUCO_MARKER_SIZE_MM,
                     "image_resolution": list(config.IMAGE_RESOLUTION)},
            "layout": {"gap_mm": self.gap_mm, "max_width_mm": self.max_width_mm,
                       **{k: v for k, v in extra.items() if k == "combined"}},
            "tools": tools,
        }
        (self.folder / "drawer.json").write_text(json.dumps(data, indent=2))
