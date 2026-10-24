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
