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

## Setup

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
