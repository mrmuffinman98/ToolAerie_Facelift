# Tool Aerie drawer format (v1)

What the Raspberry Pi scanner saves, and what the board-building app reads.

A saved drawer is a folder **and** a single zip file with the same contents:

```
Drawer 1/
├── Drawer 1.toolaerie        <- the whole drawer in one file (a zip). Move/upload THIS.
├── Drawer_1_combined.svg     <- all tools laid out, real size; opens directly in
│                                Illustrator / LightBurn / any laser software
├── drawer.json               <- manifest: geometry and measurements (below)
├── tools/tool_01.svg ...     <- one real-size SVG per tool (clearance included)
├── outlines/tool_01.svg ...  <- the FINAL traced outline with NO clearance (what polygon_mm is read from)
├── masks/tool_01.png ...     <- pure black/white mask each tool was traced from (255 = tool)
├── photos/tool_01.jpg ...    <- the photo each outline came from (undistorted, cropped)
└── steps/scan_01/ ...        <- troubleshooting pictures of every processing step (folder only;
                                 NOT inside the .toolaerie file; can be turned off with SAVE_STEPS)
```

`.toolaerie` is a normal zip. `drawer.json` sits at its top level.

## Units and coordinates

- Everything is **millimetres**.
- x to the right, **y down** (same as SVG).
- `geometry.polygon_mm` origin is the top-left corner of the tool's bounding box.

## drawer.json

```jsonc
{
  "format": "toolaerie-drawer",
  "schema_version": 1,              // bumped when the layout below changes
  "app_version": "0.2.0",
  "units": "mm",
  "drawer": { "name": "Drawer 1", "toolbox": "Tomas's car toolbox",
              "created": "...", "saved": "..." },
  "scan":   { "marker_size_mm": 20.0, "image_resolution": [2304, 1296] },
  "layout": { "gap_mm": 10.0, "max_width_mm": null, "combined": "Drawer_1_combined.svg" },
  "tools": [
    {
      "id": "a1b2c3d4e5f6",         // stable: survives renames and re-saves
      "index": 1,
      "name": "tool_01",
      "files": { "svg": "tools/tool_01.svg", "photo": "photos/tool_01.jpg" },
      "scale_px_per_mm": 6.46,
      "clearance_mm_in_svg": 1.5,   // clearance already added to the SVG file
      "svg_size_mm": [179.0, 30.3], // SVG canvas (outline + clearance + small margin)
      "geometry": { ... },          // see below
      "warnings": [],               // anything the scanner flagged
      "scanned": "2026-10-24 14:07:25"
    }
  ]
}
```

### geometry (the true tool shape, **no clearance added**)

| field | meaning |
|---|---|
| `polygon_mm` | list of closed rings `[[x,y],...]`, read from the final traced SVG (outlines/), simplified to 0.005 mm. Usually one ring; several if the scan found separate pieces |
| `area_mm2`, `bbox_mm` | area and bounding box `[width, height]` |
| `centroid_mm` | centre of mass `[x, y]` |
| `principal_axis_deg` | direction of the tool's long axis, -90..+90, measured from +x toward +y (down) |
| `length_mm`, `width_mm` | extent along / across that axis |
| `elongation` | length / width |
| `axis_reliable` | `false` for round-ish tools (sockets): the axis angle means nothing |
| `centroid_offset_along_axis_mm` | how far the centre of mass is from the middle of the tool along the axis (+ = toward the +axis end). Handle vs head hint |
| `equiv_diameter_mm` | diameter of the circle with the same area as the outline (for a "make round" option) |
| `min_circle_diameter_mm` | smallest circle that contains the whole outline |
| `circularity` | 4·pi·area / perimeter² (1.0 = perfect circle) |
| `max_radial_dev_mm` | worst gap between the outline and the same-area circle centred on the tool's centre of mass. Small = round enough to replace with a circle |
| `direction_ambiguous` | `true` when the tool is nearly symmetric end to end, so which end is the head cannot be told from shape alone. Ask the user |

## Solid outlines, one ring per tool

Each tool is saved as a solid silhouette: `polygon_mm` normally has ONE ring, and slots or
gaps inside a tool are filled (the cut is the foam pocket for the whole tool). The scanner asks
how many tools are on the board and never saves more tools than that; several tools scanned
in one photo become separate entries in `tools[]`.

## Why two copies of the outline?

`tools/*.svg` and the combined SVG include the clearance chosen at scan time, so they
can go straight to the laser. `geometry.polygon_mm` does not, so the board builder can
choose clearance per tool, add finger cutouts and compare shapes from the real outline.

## Compatibility

Readers should check `schema_version`. New optional fields may be added without a
version bump, so ignore keys you don't know.

## Where `geometry` comes from (v0.3)

`polygon_mm` is read from the scan's FINAL output: the vtracer SVG, traced and exported by the
same steps as `tools/*.svg`, only with 0 clearance (`outlines/tool_NN.svg`, `files.outline`).
It is not taken from the mask or any intermediate step. Extra keys on `geometry`:
`source` (which file), `trace_edge_shift_mm` (how far the traced edge sits from the mask edge,
about +0.1 mm is normal for vtracer) and `specks_dropped` (stray pieces under 1 mm² left out).
