#!/bin/bash
# install_launcher.sh
# One-time setup: puts a "Tool Aerie Scanner" icon on the desktop and in the
# applications menu. Double-click the icon to start the app.
#
#   bash install_launcher.sh
#
# Run it from inside the project folder after the venv has been created.
# Run it again any time you move or rename the folder.

set -e
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$APP_DIR/venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Could not find $PY"
  echo "Create the virtual environment first (see README: Setup), then run this again."
  exit 1
fi

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -d "$DESKTOP_DIR" ] || DESKTOP_DIR="$HOME/Desktop"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR"

ENTRY="[Desktop Entry]
Type=Application
Version=1.0
Name=Tool Aerie Scanner
Comment=Scan tools into a laser-cut shadow board file
Exec=\"$PY\" \"$APP_DIR/launch.py\"
Path=$APP_DIR
Icon=$APP_DIR/assets/toolaerie.png
Terminal=false
Categories=Graphics;Utility;
StartupNotify=true"

for dir in "$DESKTOP_DIR" "$APPS_DIR"; do
  f="$dir/ToolAerie.desktop"
  printf '%s\n' "$ENTRY" > "$f"
  chmod +x "$f"
  # mark as trusted so the file manager doesn't treat it as an untrusted text file
  command -v gio >/dev/null 2>&1 && gio set "$f" metadata::trusted true 2>/dev/null || true
done

# Raspberry Pi OS's file manager asks "Execute File?" every time you double-click
# a launcher. Turn that question off (a setting in the file manager's own config).
CONF="$HOME/.config/libfm/libfm.conf"
mkdir -p "$(dirname "$CONF")"
if [ -f "$CONF" ] && grep -q '^quick_exec' "$CONF"; then
  sed -i 's/^quick_exec.*/quick_exec=1/' "$CONF"
elif [ -f "$CONF" ] && grep -q '^\[config\]' "$CONF"; then
  sed -i '/^\[config\]/a quick_exec=1' "$CONF"
else
  printf '[config]\nquick_exec=1\n' >> "$CONF"
fi

echo
echo "Done. A 'Tool Aerie Scanner' icon is now on your desktop:"
echo "  $DESKTOP_DIR/ToolAerie.desktop"
echo "If it doesn't launch on the first double-click, close and reopen the"
echo "file manager (or log out and back in), then try again."
