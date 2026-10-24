#!/bin/bash
# install.sh  -  one-step setup (and update) for the Tool Aerie Scanner.
#
#   bash install.sh
#
# Run it from the unzipped download folder. It:
#   1. installs anything missing from the system (asks for your password if needed)
#   2. copies the app to ~/ToolAerieApp  (so the downloaded folder can be deleted)
#   3. sets up its Python environment and packages
#   4. puts a "Tool Aerie Scanner" icon and a "ToolAerie" folder on the desktop
#
# Run it again with a newer download to UPDATE: the app code is replaced, your
# saved drawers and settings are not touched.

set -e
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${TOOLAERIE_HOME:-$HOME/ToolAerieApp}"
step() { echo; echo "==> $*"; }
fail() { echo; echo "PROBLEM: $*"; echo "Take a photo of this screen and send it for help."; exit 1; }

step "Installing Tool Aerie Scanner into $APP_HOME"

# 1. system packages, only the ones that are missing
need=()
python3 -c "import tkinter" 2>/dev/null          || need+=(python3-tk)
python3 -c "import picamera2" 2>/dev/null        || need+=(python3-picamera2)
python3 -c "import venv, ensurepip" 2>/dev/null  || need+=(python3-venv)
if [ ${#need[@]} -gt 0 ] && [ -z "$TOOLAERIE_SKIP_APT" ]; then
  step "Installing system packages: ${need[*]}  (you may be asked for your password)"
  sudo apt-get install -y "${need[@]}" \
    || echo "WARNING: could not install ${need[*]}. Continuing; the app may say what is missing."
fi

# 2. copy the app (skip when run from the installed copy itself)
if [ "$SRC" != "$APP_HOME" ]; then
  step "Copying the app"
  mkdir -p "$APP_HOME"
  tar -C "$SRC" --exclude=./venv --exclude=./.git --exclude=__pycache__ \
      --exclude=.DS_Store -cf - . | tar -C "$APP_HOME" -xf -
fi

# 3. python environment (system-site-packages lets it see the Pi camera library)
if [ ! -x "$APP_HOME/venv/bin/python" ]; then
  step "Creating the Python environment"
  python3 -m venv --system-site-packages "$APP_HOME/venv" || fail "could not create the Python environment."
fi
if [ -z "$TOOLAERIE_SKIP_PIP" ]; then
  step "Installing packages (this can take a few minutes the first time)"
  "$APP_HOME/venv/bin/python" -m pip install -q -r "$APP_HOME/requirements.txt" \
    || fail "could not download the Python packages. Check the Pi is connected to the internet."
fi

# 4. quick self-check
step "Checking the install"
"$APP_HOME/venv/bin/python" - <<'PY' || fail "something did not install correctly (see the message above)."
import tkinter, cv2, numpy, vtracer
assert hasattr(cv2, "aruco"), "OpenCV is missing ArUco support (needs opencv-contrib-python-headless)"
print("Packages OK.")
try:
    import picamera2
    print("Pi camera library OK.")
except ImportError:
    print("NOTE: Pi camera library not found. The app will start in practice mode (no camera).")
PY

# 5. desktop icon + the folder drawers are saved to by default
bash "$APP_HOME/install_launcher.sh"
mkdir -p "$HOME/Desktop/ToolAerie"

echo
echo "All done."
echo "  - Double-click 'Tool Aerie Scanner' on the desktop to start."
echo "  - Scanned drawers are saved in the 'ToolAerie' folder on the desktop."
echo "  - You can delete the downloaded folder now."
