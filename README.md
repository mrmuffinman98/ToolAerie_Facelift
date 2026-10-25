# Aviation Tool Accountability System

Scans aviation tools on a backlit light board using a Raspberry Pi 5 camera and outputs precision SVG files for laser cutting foam shadow board inserts.

## Pipeline

```
Capture → Undistort/Crop/Scale/Threshold → Vectorize → SVG Export
```

Each stage is a discrete, independently testable module.

## Hardware

- Raspberry Pi 5 (ARM64, Raspberry Pi OS Bookworm 64-bit)
- Raspberry Pi Camera Module (CSI)
- Backlit light board
- Laser cutter (SVG input via USB/network)

## Install on the Raspberry Pi (and update)

Download the ZIP of this branch, unzip it, open a Terminal in the unzipped folder and run:

```bash
bash install.sh
```

That installs anything missing, copies the app to `~/ToolAerie_App` ... see below, sets up
its Python environment, and puts a **Tool Aerie Scanner** icon and a **ToolAerie** folder on
the desktop. After that nobody needs a terminal: double-click the icon.

- **Update:** download the newer ZIP, unzip it, run `bash install.sh` again. The app code is
  replaced; saved drawers and settings are not touched.
- The app lives in `~/ToolAerieApp`; you can delete the downloaded folder afterwards.
- If the camera can't be used, the app opens in an orange **PRACTICE MODE** (fake camera)
  and says why, so a practice scan can't be mistaken for a real one.

## Manual setup (original v1 workflow)

### 1. System packages (on the Pi)

```bash
sudo apt update
sudo apt install python3-picamera2 potrace libpotrace-dev
```

### 2. Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Camera calibration (one time only)

```bash
mkdir calibration_images
# Photograph a checkerboard pattern ~15–20 times, save to calibration_images/
python camera_calibration.py
# Paste the printed CAMERA_MATRIX and DISTORTION_COEFFICIENTS into config.py
```

### 4. Tune config.py

Set `CROP_BOUNDS` and `RULER_REGION` for your physical station layout.

## Usage

**With Pi camera:**
```bash
python main.py
```

**Development (supply a pre-taken image):**
```bash
python main.py --image sample_images/wrench.jpg
```

SVG output is written to `output/`.

## Scanner app (new interface)

A point-and-confirm app for scanning a whole drawer of tools in one sitting.

**The processing is the original, unchanged pipeline.** The app only wraps it: lens
correction, ArUco scaling, thresholding, vtracer tracing and SVG export are the same
code as `main.py`. `tests/test_v1_equivalence.py` checks that for the same photo the app
and the original pipeline give identical masks, scale and SVG files.

**Start it**

- Double-click the **Tool Aerie Scanner** icon on the desktop (set up once with
  `bash install_launcher.sh` from inside this folder, after the venv exists).
- Or from a terminal: `python app.py` (add `--demo` for a fake camera,
  `--touch` for the fullscreen touchscreen layout).

**Flow:** Start (name the drawer, pick the toolbox folder) → *Scan* → review the outline
over the photo → *Confirm* or *Retake* → next tool … → *Finish* → *Save*.

- The review screen shows the tool's real size and lets you adjust clearance, edge
  threshold and speck filter; the outline updates live.
- **Auto-scan** (button on the scan screen, remembered between runs): place a tool, hold
  it still for a second and the app scans by itself. It waits for you to lift the tool
  before arming again, so nothing is scanned twice. Tune it in `config.py`
  (`AUTOSCAN_*`). With `--demo` it simulates tools being placed so you can try it.
- Desktop shortcuts: `Space` scans / confirms, `R` or `Backspace` retakes.
- Scanning happens in a temporary scratch folder. **Nothing is written to your toolbox
  folder until you press Save**, and the app then says "Everything is saved" with the
  full path.
- Closing the app (or starting a new drawer) with unsaved tools asks you to
  **Save**, **Delete project**, or **Cancel**.
- *Open folder* shows the saved files in the file manager; *Copy to USB* appears when a
  USB stick is plugged in (eject it from the taskbar before unplugging).
- The toolbox folder you pick is remembered for next time. The Start screen's
  *Change / new folder* button can create new folders, e.g. `Tomas's car toolbox`.

**What gets saved** (default location `~/Desktop/ToolAerie`):

```
Tomas's car toolbox/
└── Drawer 1/
    ├── Drawer 1.toolaerie      <- everything in ONE file (zip): upload this to the board builder
    ├── Drawer_1_combined.svg   <- all tools, real size, ready for Illustrator / LightBurn
    ├── drawer.json             <- measurements and true outlines for the board builder
    ├── tools/tool_01.svg ...   <- one real-size SVG per tool
    └── photos/tool_01.jpg ...  <- the photo each outline came from
```

`drawer.json` also records each tool's long-axis angle, length and width, and whether
head vs handle can be told apart, plus the outline with no clearance added. The exact
format is in [FORMAT.md](FORMAT.md).

The combined SVG stacks the tools in one column (or wraps them into rows) with an
adjustable gap, and uses 1 SVG unit = 1 mm.

If the app won't start from the icon it shows the error in a message box; details are
also written to `~/.cache/toolaerie/app.log`.

Needs `python3-tk` (preinstalled with Raspberry Pi OS desktop).

Code: `app.py` (screens), `autoscan.py` (hands-free scanning), `shape_info.py`
(outline measurements), `ui.py` (buttons, dialogs, folder picker), `scan_service.py`
(scan + review overlay), `session.py` (drawer + combined layout), `storage.py` (save
locations, safe copy, USB), `camera.py` (Pi camera, demo camera), `launch.py` (icon
entry point).

## Tests

```bash
python -m pytest tests/ -v
```

Tests run without the Pi camera using synthetic images.

## File Structure

```
aviation-tool-accountability/
├── main.py                # Entry point
├── capture.py             # Step 1: Picamera2 image capture
├── process.py             # Step 2: OpenCV image processing
├── vectorize.py           # Step 3: pypotrace bitmap-to-vector
├── export.py              # Step 4: svgwrite SVG generation
├── config.py              # Tunable parameters
├── camera_calibration.py  # One-time checkerboard calibration helper
├── requirements.txt
├── tests/
│   ├── test_process.py
│   └── test_vectorize.py
├── sample_images/         # Test images (no camera needed)
└── output/                # Generated SVGs (git-ignored)
```


## Tool count, solid outlines (v0.3)

- **Tools on the board:** type how many tools you placed (Scan and Review screens). The scan keeps
  that many outlines (the largest ones), numbered left to right, and never more. If it finds fewer
  (two tools touching), it tells you to move them apart. Extra pieces are shown dotted orange and
  ignored.
- **Solid outlines:** the original edge detection is unchanged. If a large tool's edge has a gap and
  the outline would come out hollow, the tool's solid inside is filled in. Tools that were already
  solid are untouched (identical to the original result).
- **Roundness numbers** for each tool are saved in `drawer.json` (for the board builder's
  "make round" option). Outlines themselves are never rounded by the scanner.

## Saved masks and step pictures (while developing)

Every saved drawer has `masks/tool_NN.png`: the pure black-and-white mask each tool was traced from
(white = tool, black = everything else, nothing in between). With `SAVE_STEPS = True` in `config.py`
(the default for now) it also has `steps/scan_NN/` with a picture of every stage:

1. `1_raw_camera.png` straight from the camera
2. `2_corrected_cropped.png` after lens correction and crop (what the code actually sees)
3. `3_edge_threshold.png` the original edge-detection picture
4. `4_original_mask.png` the original code's filled mask
5. `5_solid_mask.png` after the hollow-fill step
6. `6_final_mask_<tool>.png` and `7_overlay_<tool>.jpg` per tool (with clearance)
7. `info.json` the settings and flags for that scan

The `steps` folder is not included in the one-file `.toolaerie` package. Set `SAVE_STEPS = False`
when the scans are trusted.
