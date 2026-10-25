# Handoff: Tool Aerie Shadow Board Builder (app #2)

Paste or point a new chat at this file. It is the starting brief for building the second app.

## 1. What exists already (do not rebuild)

**Tool Aerie Scanner** (app #1) is finished enough for now and running on Tomas's Raspberry Pi 5.
- Repo: github.com/mrmuffinman98/ToolAerie_Facelift, branch `new-interface` (the original working v1 is the older code in the same repo; the CLI `python main.py` still works).
- It scans tools on a backlit board with the Pi camera, in a tkinter desktop app launched by a desktop icon. Flow: Start (drawer name + toolbox folder) -> Scan -> Review (photo + outline, confirm/retake) -> repeat -> Done (layout preview, SAVE).
- Processing is the original proven pipeline, unchanged in behavior: lens undistort, ArUco scale (DICT_4X4_50, id 0, 20 mm marker), adaptive threshold, vtracer, SVG export. Tomas has laser-cut shapes from it that fit tools exactly. **Do not touch or "improve" this math.**
- Save location is chosen by the user: `<toolbox folder>/<Drawer name>/`.

## 2. What the builder app reads (the contract)

A saved drawer is a folder plus one zip with the same contents (`<Drawer>.toolaerie`). Full spec: `FORMAT.md` in the repo (schema_version 1). Read it first.

```
Drawer 1/
  Drawer 1.toolaerie        whole drawer as one zip (the file to upload)
  Drawer_1_combined.svg     all tools stacked, real size, 1 user unit = 1 mm
  drawer.json               manifest
  tools/tool_01.svg ...     per-tool SVG (clearance already included)
  photos/tool_01.jpg ...    undistorted photo each outline came from
```

Key facts for the builder:
- Units are mm, x right, y down.
- `drawer.json` -> `tools[]` each with: stable `id`, `name`, `files`, `scale_px_per_mm`, `clearance_mm_in_svg`, `svg_size_mm`, `warnings`, and `geometry`.
- `geometry` is the true tool shape with NO clearance: `polygon_mm` (closed rings, 0.05 mm simplified, origin = bounding-box top-left), `area_mm2`, `bbox_mm`, `centroid_mm`, `principal_axis_deg`, `length_mm`, `width_mm`, `elongation`, `axis_reliable` (false for round tools like sockets), `centroid_offset_along_axis_mm`, `direction_ambiguous` (true = can't tell head from handle by shape, ask the user).
- Why two outlines: tool SVGs already carry the clearance chosen at scan time (go straight to laser). `polygon_mm` is clearance-free so the builder can choose clearance per tool, add finger cutouts and compare shapes.
- Readers must check `schema_version` and ignore unknown keys.

## 3. What Tomas wants the builder to do

Goal: take ONE upload (the `.toolaerie` file, or the folder) from the Pi, on a normal computer, and let him build a full-scale shadow board for a toolbox drawer naturally and quickly.

Must-haves (from his words):
1. **Import** a `.toolaerie` file (or folder) with a single drop/upload.
2. **Move tools around** on a drawer-sized board, easily and confidently (drag, rotate, snap, zoom). Real-world drawer dimensions entered by the user.
3. **Finger cutouts:** add relief cutouts so tools can be gripped and lifted out of the foam.
4. **Align similar tools:** e.g. a set of wrenches or screwdrivers lined up, evenly spaced, same orientation (the geometry fields above exist to help: axis, length, head/handle hint).
5. **Per-tool clearance** adjustable from the clearance-free polygon.
6. **Export** to formats his software accepts. He uses **LightBurn** at home, a **different laser program at work**, and owns **Illustrator**. SVG at true mm scale is the baseline; **DXF** should also be offered. Ask which work software he uses before choosing details (units, line colors/layers for cut vs engrave, hairline stroke).
7. Even if the builder has problems, he can always take the combined SVG from the scanner into Illustrator. The builder is a convenience, not a gate.

Standing preferences:
- Friendly for someone who has never used it. No terminal. Quick, reliable, simple math over clever.
- Desktop (normal computer) first. A web app that runs in the browser (static, no install) is a likely good fit, but confirm with him before deciding.
- Capture-time alignment was deliberately NOT done in the scanner; it belongs here.

## 4. How to start the new chat

Do NOT start building immediately. Tomas previously (rightly) stopped a premature build with "we haven't even discussed what I want?". First:
1. Read `FORMAT.md` and a real `drawer.json` (ask him to upload a saved `.toolaerie` from the Pi, so you design against real data).
2. Ask a few short questions (use the question tool): web app vs desktop app; work laser software name and its import preferences; drawer sizes he cares about; which features first.
3. Propose a small first milestone, then build and test it with sample data. A good first milestone: import `.toolaerie`, show tools on a to-scale drawer board, drag/rotate, export SVG (mm).
4. Then iterate: finger cutouts -> alignment/spacing helpers -> DXF -> clearance per tool.

Testing tip from the scanner work: generate sample drawers with the scanner's demo camera (practice mode), or use the test fixtures in `tests/test_handoff.py`, so you don't need the Pi.

## 5. Known caveats from the scanner

- Real Pi camera path, USB copy and auto-scan tuning on the real light board are not fully verified. Tomas reports it works on his Pi.
- Possible scale error for thick tools (parallax). Worth a known-gauge check; the builder should let the user see/compare dimensions.
- Original tag `v1.0-working` isn't on GitHub (push blocked from the cloud environment). Create a GitHub Release or `git push origin v1.0-working` from his machine.
- A leftover `_v1.bundle` file may sit in `captures` (safe to delete).
- Pre-existing failing tests `test_process.py` / `test_vectorize.py` use an old pypotrace API; unrelated.

## 6. Possible later scanner work (not now)

Crash recovery for unsaved drawers, adding tools to an already-saved drawer, naming tools quickly on the Pi, review zoom, multiple tools per photo.

## 7. Repo map (scanner)

`app.py` UI screens | `session.py` drawer model + combined SVG + manifest | `shape_info.py` geometry for the hand-off | `storage.py` folders/settings/zip | `scan_service.py` + `process.py` + `vectorize.py` + `export.py` pipeline | `camera.py` Pi/demo cameras | `autoscan.py` | `install.sh` one-step Pi install | `FORMAT.md` the contract.

## 8. Scanner v0.3 changes (2026-10-24) the builder can rely on

- Outlines are now always SOLID: `polygon_mm` normally has one ring per tool (large tools used to
  come out hollow, with a second ring for slots). If the builder sees more than one ring in a
  tool, treat extras as separate pieces, not as holes.
- The scanner asks for the number of tools on the board and saves exactly that many (never more).
  Several tools from one photo are separate entries in `tools[]`.
- New optional geometry fields (additive, schema_version still 1): `equiv_diameter_mm`,
  `min_circle_diameter_mm`, `circularity`, `max_radial_dev_mm` — for a per-tool "make round"
  option (replace the outline with a circle of equivalent diameter; show `max_radial_dev_mm` so the
  user can judge). The scanner never rounds anything itself.
- Re-scan any drawer made before v0.3 that contains large tools (hollow outlines).
