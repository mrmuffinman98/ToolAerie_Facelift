# storage.py
# Where things live on disk, and the helpers for saving them safely:
#
#   work folder   ~/.cache/toolaerie/work/<id>/   scratch space while scanning
#   save folder   chosen by the user in the app   the final, permanent copy
#   settings      ~/.config/toolaerie/settings.json  remembers the last toolbox
#
# No Tk in here, so it can be unit tested.

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import config

APP_DIR_NAME = "toolaerie"


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_BAD_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def folder_name(text: str, fallback: str = "Drawer") -> str:
    """Make user text safe to use as a folder name, but keep it readable.

    Spaces and apostrophes stay ("Tomas's car toolbox"); characters that file
    systems reject are dropped.
    """
    s = _BAD_CHARS.sub("", text or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s or fallback


def file_stem(text: str) -> str:
    """Like folder_name but for file names: no spaces (friendlier in CAD/laser software)."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", folder_name(text)).strip("_")
    return s or "drawer"


def suggest_drawer_name(toolbox: Path, base: str = "Drawer") -> str:
    """'Drawer 1', or the next number not already used in the toolbox folder."""
    n = 1
    try:
        existing = {p.name.lower() for p in Path(toolbox).iterdir() if p.is_dir()}
    except OSError:
        existing = set()
    while f"{base} {n}".lower() in existing:
        n += 1
    return f"{base} {n}"


def unique_child(parent: Path, name: str) -> Path:
    """parent/name, or 'name (2)', 'name (3)'... if it already exists."""
    p, n = Path(parent) / name, 2
    while p.exists():
        p = Path(parent) / f"{name} ({n})"
        n += 1
    return p


# ---------------------------------------------------------------------------
# Remembered settings
# ---------------------------------------------------------------------------

def _settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**updates) -> None:
    data = load_settings()
    data.update(updates)
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
    except OSError:
        pass  # remembering the folder is a convenience, never a reason to fail


def default_toolbox() -> Path:
    """The folder the Start screen suggests: last used, else the default."""
    saved = load_settings().get("toolbox_dir")
    if saved:
        return Path(saved)
    return Path(config.DEFAULT_SAVE_ROOT).expanduser()


# ---------------------------------------------------------------------------
# Scratch space
# ---------------------------------------------------------------------------

def new_work_root() -> Path:
    """A fresh empty scratch folder for one scanning session."""
    root = Path(config.WORK_DIR).expanduser() / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True)
    return root


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

# The only things the app ever creates inside a saved drawer folder. When a
# drawer is saved over an older copy, only these are replaced, so any other
# files the user put in that folder are left alone.
_APP_ITEMS = ("tools", "outlines", "photos", "masks", "steps", "drawer.json", "session.json")  # session.json: older versions


def is_nonempty_dir(path: Path) -> bool:
    p = Path(path)
    return p.is_dir() and any(p.iterdir())


def copy_drawer(src: Path, dest: Path, replace: bool = False) -> Path:
    """Copy a finished drawer folder to dest.

    replace=True clears the previous app-made files in dest first (stale
    tool SVGs, an old combined file) but nothing else.
    """
    src, dest = Path(src), Path(dest)
    if replace and dest.is_dir():
        for item in _APP_ITEMS:
            t = dest / item
            if t.is_dir():
                shutil.rmtree(t)
            elif t.exists():
                t.unlink()
        for pattern in ("*_combined.svg", "*.toolaerie"):
            for f in dest.glob(pattern):
                f.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def make_package(folder: Path, out: Path) -> Path:
    """Zip a drawer folder into one .toolaerie file (drawer.json sits at the top).

    Earlier .toolaerie files inside the folder are left out of the new one.
    """
    folder, out = Path(folder), Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            rel = f.relative_to(folder)
            if rel.parts and rel.parts[0] == "steps":      # troubleshooting pictures stay out
                continue
            if f.is_file() and f.suffix != ".toolaerie":
                z.write(f, rel.as_posix())
    return out


def find_usb_drives() -> list[Path]:
    """Mounted USB sticks / external drives (Raspberry Pi OS mounts under /media/<user>)."""
    user = os.environ.get("USER") or Path.home().name
    found: list[Path] = []
    for base in (Path("/media") / user, Path("/run/media") / user, Path("/media")):
        try:
            for p in sorted(base.iterdir()):
                if p.is_dir() and os.path.ismount(p) and p not in found:
                    found.append(p)
        except OSError:
            continue
    return found


def shorten(path: Path, max_len: int = 46) -> str:
    """~/Desktop/ToolAerie/...  with the middle trimmed if long."""
    s = str(path)
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    if len(s) > max_len:
        s = s[:12] + "..." + s[-(max_len - 15):]
    return s


# ---------------------------------------------------------------------------
# Error log (there is no terminal when the app is started from its icon)
# ---------------------------------------------------------------------------

def log_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP_DIR_NAME / "app.log"


def log_error(text: str) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 500_000:     # keep it small
            p.write_text("")
        with p.open("a") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{text.rstrip()}\n")
    except OSError:
        pass
