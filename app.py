# app.py
# Tool Aerie scanner: fast tool-by-tool scanning into one shadow-board file.
#
#   python app.py              desktop window
#   python app.py --touch      fullscreen, big buttons, for the Pi touchscreen
#   python app.py --demo       fake camera (no hardware needed)
#   python app.py --folder DIR use photos from a folder instead of a camera
#
# Flow:  Start -> [ Scan -> Review -> Confirm / Retake ] x N -> Finish
#
# Finish writes <session>/<name>_combined.svg with every confirmed tool laid
# out at real size with a gap between them, plus one SVG per tool.
#
# Only needs tkinter (python3-tk, preinstalled on Raspberry Pi OS desktop) --
# no extra GUI packages.

from __future__ import annotations

import argparse
import dataclasses
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

import cv2
import numpy as np

import autoscan
import camera as cam_mod
import config
import markers as markers_mod
import scan_service
import storage
from scan_service import ScanError, Settings
from session import Session
from ui import (BG, PANEL, PANEL2, FG, MUTED, GREEN, AMBER, RED, BLUE, DARK_GREEN_TEXT,
                Btn, Stepper, FolderPicker, ask, png_photo)

APP_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, touch=False, camera_kind="auto", folder=None):
        super().__init__()
        self.touch = touch
        # UI scale factor. On the touchscreen it follows the screen height:
        # 1.0 at 800x480, ~1.3 at 1280x720, up to 1.6 on bigger panels.
        self.k = min(1.6, max(1.0, self.winfo_screenheight() / 540)) if touch else 1.0
        # Short screens (e.g. 800x480) get a tighter layout so nothing is clipped.
        self.compact = self.winfo_screenheight() < 640
        self.title("Tool Aerie Scanner")
        self.configure(bg=BG)
        if touch:
            self.attributes("-fullscreen", True)
            self.config(cursor="none")
        else:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{min(1180, sw)}x{min(720, sh - 60)}+0+0")
            self.minsize(min(900, sw), min(560, sh - 60))
            try:
                self.attributes("-zoomed", True)     # fill the screen (Linux)
            except tk.TclError:
                pass
        try:
            self.iconphoto(True, tk.PhotoImage(file=str(APP_DIR / "assets" / "toolaerie.png")))
        except tk.TclError:
            pass
        self.report_callback_exception = self._on_ui_error
        self._closing = False

        self.settings = Settings()
        self.session: Session | None = None
        self.toolbox: Path = storage.default_toolbox()   # where drawers get saved
        self.auto_scan: bool = bool(storage.load_settings().get("auto_scan", False))
        self._fonts: dict = {}
        self.startup_error: str | None = None

        try:
            # "auto" asks for the real camera and, if it can't be used, says why
            # (open_camera("auto") would fall back silently).
            camera = cam_mod.open_camera("pi" if camera_kind == "auto" else camera_kind, folder)
        except cam_mod.CameraError as e:
            self.startup_error = str(e)
            camera = cam_mod.DemoCamera()
        self.feed = cam_mod.LiveFeed(camera)
        try:
            self.feed.start()
        except cam_mod.CameraError as e:
            self.startup_error = str(e)
            self.feed = cam_mod.LiveFeed(cam_mod.DemoCamera())
            self.feed.start()

        # header
        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="TOOL AERIE", bg=PANEL, fg=GREEN, font=self.f(13, True)
                 ).pack(side="left", padx=14, pady=8)
        self.head_info = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=self.f(11))
        self.head_info.pack(side="left", padx=8)
        if isinstance(self.feed.camera, cam_mod.DemoCamera):
            tk.Label(head, text=" PRACTICE MODE: NOT THE REAL CAMERA ", bg=AMBER, fg="#2b1b00",
                     font=self.f(11, True)).pack(side="right", padx=14, pady=6)
        else:
            tk.Label(head, text=f"Camera: {self.feed.camera.name}", bg=PANEL, fg=MUTED,
                     font=self.f(10)).pack(side="right", padx=14)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.screens = {}
        for cls in (HomeScreen, MarkerScreen, StartScreen, ScanScreen, ReviewScreen, DoneScreen):
            s = cls(body, self)
            s.grid(row=0, column=0, sticky="nsew")
            self.screens[cls.__name__] = s
        self.current = None
        markers_mod.apply_to_config()      # main marker size from Marker setup
        self.show("HomeScreen")

        self.bind("<F11>", lambda e: self.attributes(
            "-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Key>", self._key)
        self.protocol("WM_DELETE_WINDOW", self.request_exit)

    # --- helpers ---------------------------------------------------------

    def f(self, size, bold=False) -> tkfont.Font:
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(
                family="Helvetica", size=int(round(size * self.k)),
                weight="bold" if bold else "normal")
        return self._fonts[key]

    def show(self, name: str):
        self.current = name
        scr = self.screens[name]
        scr.tkraise()
        scr.on_show()
        self.refresh_header()

    def refresh_header(self):
        s = self.session
        if not s:
            self.head_info.config(text="", fg=MUTED)
            return
        n = len(s.tools)
        state = "saved" if (s.saved_to and not s.dirty) else "not saved yet"
        self.head_info.config(
            text=f"{s.title}   -   {n} tool{'s' if n != 1 else ''} scanned   -   {state}",
            fg=MUTED if state == "saved" else AMBER)

    def run_bg(self, fn, ok, err=None):
        """Run fn() on a worker thread; call ok(result) / err(exc) on the UI thread."""
        q: queue.Queue = queue.Queue()

        def work():
            try:
                q.put((True, fn()))
            except Exception as e:  # reported to the user, not swallowed
                q.put((False, e))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                good, val = q.get_nowait()
            except queue.Empty:
                self.after(30, poll)
                return
            (ok if good else (err or self.fatal))(val)

        poll()

    def fatal(self, exc):
        storage.log_error("".join(traceback.format_exception(exc)) if isinstance(
            exc, BaseException) else str(exc))
        self.screens[self.current].flash(f"Error: {exc}", RED, 8000)

    def _on_ui_error(self, exc, val, tb):
        storage.log_error("".join(traceback.format_exception(exc, val, tb)))
        try:
            self.screens[self.current].flash(f"Error: {val}  (details in {storage.log_path()})",
                                             RED, 9000)
        except Exception:
            pass

    def set_auto_scan(self, on: bool):
        self.auto_scan = on
        storage.save_settings(auto_scan=on)
        if isinstance(self.feed.camera, cam_mod.DemoCamera):
            self.feed.camera.set_auto_cycle(on)    # demo: simulate placing tools

    def next_demo_tool(self):
        if isinstance(self.feed.camera, cam_mod.DemoCamera):
            self.feed.camera.next_tool()
        elif isinstance(self.feed.camera, cam_mod.FolderCamera):
            self.feed.camera.advance()

    def _key(self, e):
        self.screens[self.current].on_key(e)

    def open_folder(self, path):
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            elif platform.system() == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.fatal(e)

    # --- saving, deleting and leaving ---------------------------------------

    def destination(self) -> Path:
        """Folder this drawer will be (or was) saved to."""
        s = self.session
        if s.saved_to and s.saved_to.parent == self.toolbox:
            return s.saved_to                      # saving again over our own copy
        return self.toolbox / storage.folder_name(s.title)

    def has_unsaved(self) -> bool:
        return bool(self.session and self.session.tools and self.session.dirty)

    def choose_toolbox(self) -> bool:
        """Let the user pick / create the folder drawers are saved in."""
        picked = FolderPicker(self, self.toolbox, "Choose or create the toolbox folder").show()
        if picked is None:
            return False
        self.toolbox = picked
        storage.save_settings(toolbox_dir=str(picked))
        if self.session and self.session.tools:
            self.session.dirty = True              # the saved copy is no longer the target
        self.refresh_header()
        return True

    def save_session(self, on_saved=None):
        """Copy the drawer to its destination. Calls on_saved() only on success."""
        s = self.session
        dest, replace = self.destination(), False
        if dest == s.saved_to:
            replace = True
        elif storage.is_nonempty_dir(dest):
            choice = ask(self, "That folder already exists",
                         f'"{dest.name}" is already in {storage.shorten(dest.parent)}.',
                         [("Replace it", "replace", AMBER, "#2b1b00"),
                          ("Keep both (save a new copy)", "copy", BLUE, "#ffffff"),
                          ("Cancel", None, PANEL2, FG)])
            if choice is None:
                return
            if choice == "copy":
                dest = storage.unique_child(dest.parent, dest.name)
            else:
                replace = True
        scr = self.screens[self.current]
        scr.flash("Saving...", MUTED, 20000)

        def ok(saved_file):
            storage.save_settings(toolbox_dir=str(self.toolbox))
            self.refresh_header()
            done = self.screens["DoneScreen"]
            done.show_saved()
            self.screens[self.current].flash("Saved", GREEN, 1500)
            if on_saved:
                on_saved()

        def err(e):
            self.screens[self.current].flash(
                f"Could not save to {storage.shorten(dest, 40)}: {e}", RED, 10000)
            storage.log_error(f"save failed: {e!r}")

        self.run_bg(lambda: s.save_to(dest, replace=replace), ok, err)

    def guard_unsaved(self, proceed):
        """Run proceed() once any unsaved drawer has been saved or deleted.

        Cancel leaves everything as it is and does not call proceed().
        """
        if not self.has_unsaved():
            proceed()
            return
        s = self.session
        n = len(s.tools)
        choice = ask(
            self, "This drawer isn't saved yet",
            f'"{s.title}" has {n} scanned tool{"s" if n != 1 else ""} that '
            f"haven't been saved.\n\nSave to:  {storage.shorten(self.destination(), 90)}",
            [("Save", "save", GREEN, DARK_GREEN_TEXT),
             ("Delete project", "delete", RED, "#ffffff"),
             ("Cancel", None, PANEL2, FG)])
        if choice == "save":
            self.save_session(on_saved=proceed)
        elif choice == "delete":
            sure = ask(self, "Delete this drawer?",
                       f"The {n} scanned tool{'s' if n != 1 else ''} will be gone for good. "
                       "This can't be undone.",
                       [("Yes, delete it", "yes", RED, "#ffffff"),
                        ("No, keep it", None, PANEL2, FG)])
            if sure == "yes":
                self.discard_session()
                proceed()

    def discard_session(self):
        if self.session:
            self.session.cleanup()
        self.session = None
        self.refresh_header()

    def new_drawer(self):
        def go():
            self.discard_session()
            self.screens["StartScreen"].reset()
            self.show("StartScreen")
        self.guard_unsaved(go)

    def request_exit(self):
        self.guard_unsaved(self._quit_now)

    def _quit_now(self):
        self._closing = True
        for scr in self.screens.values():
            scr.cancel_timers()
        try:
            self.feed.stop()
        finally:
            if self.session:
                self.session.cleanup()
            self.destroy()


class Screen(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self._flash_id = None
        self.toast = tk.Label(self, text="", bg=BG, fg=FG, font=app.f(12, True))

    def on_show(self): ...
    def on_key(self, e): ...
    def cancel_timers(self):
        if self._flash_id:
            self.after_cancel(self._flash_id)
            self._flash_id = None

    def flash(self, text, color=GREEN, ms=3500):
        self.toast.config(text=text, fg=color)
        self.toast.place(relx=0.5, rely=0.02, anchor="n")
        self.toast.lift()
        if self._flash_id:
            self.after_cancel(self._flash_id)
        self._flash_id = self.after(ms, self.toast.place_forget)

    def photo_canvas(self, parent) -> tk.Canvas:
        c = tk.Canvas(parent, bg="#0b0e12", highlightthickness=0)
        c.photo = None
        return c

    @staticmethod
    def fit(img: np.ndarray, canvas: tk.Canvas) -> np.ndarray:
        cw, ch = max(canvas.winfo_width(), 50), max(canvas.winfo_height(), 50)
        h, w = img.shape[:2]
        s = min(cw / w, ch / h)
        return cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                          interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)

    def draw(self, canvas: tk.Canvas, img: np.ndarray, is_bgr=False):
        canvas.photo = png_photo(self.fit(img, canvas), is_bgr)
        canvas.delete("all")
        canvas.create_image(canvas.winfo_width() // 2, canvas.winfo_height() // 2,
                            image=canvas.photo)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

class HomeScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        box = tk.Frame(self, bg=BG)
        box.place(relx=0.5, rely=0.47, anchor="center")
        tk.Label(box, text="What would you like to do?", bg=BG, fg=FG,
                 font=app.f(22 if c else 26, True)).pack(pady=(0, 14 if c else 24))
        Btn(box, "SCAN TOOLS", lambda: app.show("StartScreen"), GREEN, DARK_GREEN_TEXT,
            app.f(20, True), padx=70, pady=14 if c else 20).pack(fill="x", pady=5)
        Btn(box, "MARKER SETUP", lambda: app.show("MarkerScreen"), BLUE, "#ffffff",
            app.f(20, True), padx=70, pady=14 if c else 20).pack(fill="x", pady=5)
        tk.Label(box, text="Marker setup: print scale markers and set their real sizes.",
                 bg=BG, fg=MUTED, font=app.f(11)).pack(pady=(8, 10))
        Btn(box, "Quit", app.request_exit, PANEL, MUTED, app.f(11), padx=30, pady=5).pack()


# ---------------------------------------------------------------------------
# Marker setup
# ---------------------------------------------------------------------------

class MarkerScreen(Screen):
    """One row per scale marker: its printed (intended) size, its actual size, Print."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        self.markers: list = []
        tk.Label(self, text="Marker setup", bg=BG, fg=FG, font=app.f(20 if c else 24, True)
                 ).pack(pady=(10 if c else 18, 2))
        tk.Label(self, text="Each marker on the board has its own size. If a marker prints at a "
                            "different size, don't reprint: measure the black square with a "
                            "ruler and type it in 'Actual size'.",
                 bg=BG, fg=MUTED, font=app.f(10 if c else 11), wraplength=int(700 * app.k)
                 ).pack(pady=(0, 8))
        self.rows = tk.Frame(self, bg=BG)
        self.rows.pack(padx=20)
        self.note = tk.Label(self, text="", bg=BG, fg=GREEN, font=app.f(11),
                             wraplength=int(700 * app.k))
        self.note.pack(pady=6)
        bar = tk.Frame(self, bg=BG)
        bar.pack(pady=4)
        self.add_btn = Btn(bar, "+ Add marker", self.add, BLUE, "#ffffff", app.f(13, True),
                           padx=20, pady=6)
        self.add_btn.pack(side="left", padx=6)
        Btn(bar, "Done", lambda: app.show("HomeScreen"), GREEN, DARK_GREEN_TEXT,
            app.f(13, True), padx=30, pady=6).pack(side="left", padx=6)

    def on_show(self):
        self.markers = markers_mod.load()
        self.note.config(text="")
        self.build()

    def build(self):
        app = self.app
        for w in self.rows.winfo_children():
            w.destroy()
        hdr = dict(bg=BG, fg=MUTED, font=app.f(10))
        tk.Label(self.rows, text="Marker", **hdr).grid(row=0, column=0, padx=6)
        tk.Label(self.rows, text="Printed size (mm)", **hdr).grid(row=0, column=1, padx=6)
        tk.Label(self.rows, text="Actual size (mm)", **hdr).grid(row=0, column=2, padx=6)
        self.vars = []
        for r, m in enumerate(self.markers, 1):
            tk.Label(self.rows, text=f"ID {m.id}" + ("  (main)" if m.id == config.ARUCO_MARKER_ID
                                                      else ""),
                     bg=BG, fg=FG, font=app.f(13, True)).grid(row=r, column=0, padx=8, pady=3,
                                                              sticky="w")
            nom, act = tk.StringVar(value=f"{m.nominal_mm:g}"), tk.StringVar(value=f"{m.size_mm:g}")
            self.vars.append((nom, act))
            for col, v in ((1, nom), (2, act)):
                e = tk.Entry(self.rows, textvariable=v, width=7, font=app.f(14), justify="center",
                             bg=PANEL2, fg=FG, insertbackground=FG, relief="flat")
                e.grid(row=r, column=col, padx=8, pady=3, ipady=4)
                e.bind("<FocusOut>", lambda _e: self.commit())
                e.bind("<Return>", lambda _e: self.commit())
            Btn(self.rows, "Print PDF", lambda i=r - 1: self.print(i), GREEN, DARK_GREEN_TEXT,
                app.f(11, True), padx=14, pady=4).grid(row=r, column=3, padx=6)
            if m.id != config.ARUCO_MARKER_ID:
                Btn(self.rows, "Remove", lambda i=r - 1: self.remove(i), PANEL, MUTED,
                    app.f(10), padx=10, pady=4).grid(row=r, column=4, padx=4)
        self.add_btn.set_enabled(len(self.markers) < markers_mod.MAX_MARKERS)

    def commit(self) -> bool:
        """Read the boxes; save when every value is valid. Returns True when saved."""
        new = []
        for m, (nom, act) in zip(self.markers, self.vars):
            n, a = markers_mod.parse_mm(nom.get()), markers_mod.parse_mm(act.get())
            if n is None or a is None:
                self.note.config(
                    text=f"Marker ID {m.id}: sizes must be numbers between "
                         f"{markers_mod.MIN_MM:g} and {markers_mod.MAX_MM:g} mm.", fg=AMBER)
                return False
            new.append(markers_mod.Marker(m.id, n, a))
        if [x.__dict__ for x in new] != [x.__dict__ for x in self.markers]:
            self.markers = new
            markers_mod.save(new)
            self.note.config(text="Saved. New scans use these sizes.", fg=GREEN)
        return True

    def add(self):
        if not self.commit() or len(self.markers) >= markers_mod.MAX_MARKERS:
            return
        mid = markers_mod.next_id(self.markers)
        size = self.markers[0].nominal_mm
        self.markers.append(markers_mod.Marker(mid, size, size))
        markers_mod.save(self.markers)
        self.note.config(text=f"Added marker ID {mid}. Print it, place it flat on the board.",
                         fg=GREEN)
        self.build()

    def remove(self, i):
        if not self.commit():
            return
        gone = self.markers.pop(i)
        markers_mod.save(self.markers)
        self.note.config(text=f"Removed marker ID {gone.id}.", fg=GREEN)
        self.build()

    def print(self, i):
        if not self.commit():
            return
        m = self.markers[i]
        try:
            path = markers_mod.print_pdf(m)
        except (OSError, ValueError) as e:
            self.note.config(text=f"Could not make the PDF: {e}", fg=AMBER)
            return
        self.note.config(
            text=f"Saved {storage.shorten(path, 60)}. Print it at 100% (Actual size), "
                 f"then measure the black square. Its printed size should be {m.nominal_mm:g} mm.",
            fg=GREEN)
        if ask(self.app, "Marker PDF saved",
               f"{path.name}\nis in\n{storage.shorten(path.parent, 50)}\n\n"
               "Print at 100% / Actual size.",
               [("Open folder", True, BLUE, "#ffffff"), ("OK", False, GREEN, DARK_GREEN_TEXT)],
               cancel_value=False):
            self.app.open_folder(path.parent)


class StartScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        box = tk.Frame(self, bg=BG)
        box.place(relx=0.5, rely=0.47, anchor="center")
        tk.Label(box, text="New drawer", bg=BG, fg=FG, font=app.f(22 if c else 26, True)
                 ).pack(pady=(0, 4))
        tk.Label(box, text="Scan every tool for one drawer, then save them all together.",
                 bg=BG, fg=MUTED, font=app.f(11 if c else 12)).pack(pady=(0, 12 if c else 20))

        tk.Label(box, text="Drawer name", bg=BG, fg=FG, font=app.f(11)).pack(anchor="w")
        self.name = tk.StringVar()
        tk.Entry(box, textvariable=self.name, font=app.f(15), justify="left",
                 bg=PANEL2, fg=FG, insertbackground=FG, relief="flat", width=30
                 ).pack(ipady=6, pady=(2, 10 if c else 14), fill="x")

        tk.Label(box, text="Save drawers in this toolbox folder", bg=BG, fg=FG,
                 font=app.f(11)).pack(anchor="w")
        row = tk.Frame(box, bg=PANEL2)
        row.pack(fill="x", pady=(2, 4))
        self.path_lbl = tk.Label(row, text="", bg=PANEL2, fg=GREEN, font=app.f(12),
                                 anchor="w", padx=10)
        self.path_lbl.pack(side="left", fill="x", expand=True, pady=6)
        Btn(row, "Change / new folder", self.change_folder, BLUE, "#ffffff", app.f(11, True),
            padx=12, pady=4).pack(side="right", padx=6, pady=4)
        self.final_lbl = tk.Label(box, text="", bg=BG, fg=MUTED, font=app.f(10), anchor="w")
        self.final_lbl.pack(fill="x")

        Btn(box, "START SCANNING", self.start, GREEN, DARK_GREEN_TEXT, app.f(18, True),
            padx=50, pady=10 if c else 15).pack(pady=(12 if c else 20, 4), fill="x")
        row2 = tk.Frame(box, bg=BG)
        row2.pack(pady=(8, 0))
        Btn(row2, "How it works", self.how_it_works, PANEL2, FG, app.f(11), padx=24, pady=5
            ).pack(side="left", padx=4)
        Btn(row2, "Menu", lambda: app.show("HomeScreen"), PANEL2, FG, app.f(11), padx=24, pady=5
            ).pack(side="left", padx=4)
        Btn(row2, "Quit", app.request_exit, PANEL, MUTED, app.f(11), padx=30, pady=5
            ).pack(side="left", padx=4)
        self.note = tk.Label(box, text="", bg=BG, fg=AMBER, font=app.f(10),
                             wraplength=int(520 * app.k))
        self.note.pack(pady=(8, 0))
        self._suggested = ""
        self.name.trace_add("write", lambda *_: self.update_final())
        self.reset()

    def reset(self):
        self._suggested = storage.suggest_drawer_name(self.app.toolbox)
        self.name.set(self._suggested)
        self.note.config(text="")
        self.update_final()

    def update_final(self):
        tb = self.app.toolbox
        self.path_lbl.config(text=storage.shorten(tb, 44))
        self.final_lbl.config(
            text=f"This drawer will be saved as:  {storage.shorten(tb / storage.folder_name(self.name.get()), 56)}")

    def how_it_works(self):
        ask(self.app, "How it works",
            "1.  Lay the printed marker flat on the light board, away from where tools go.\n"
            "2.  Put your tool(s) on the board, set 'Tools on the board' to how many you placed, "
            "and press SCAN (or turn on Auto-scan). Several tools can be scanned at once if they "
            "don't touch.\n"
            "3.  Check the green outline on the photo. Press CONFIRM, or Retake if it looks wrong.\n"
            "4.  Repeat for every tool in the drawer, then press Finish.\n"
            "5.  Press SAVE. The files are put in the folder you chose, ready to copy to a USB stick.",
            [("Got it", None, GREEN, DARK_GREEN_TEXT)], left=True)

    def on_show(self):
        if self.app.startup_error:
            self.note.config(
                text=f"No camera found: {self.app.startup_error}\n"
                     "The app is in PRACTICE MODE (fake camera). Check the camera cable, "
                     "close anything else using the camera, then restart the app.")
        self.update_final()

    def on_key(self, e):
        if e.keysym == "Return":
            self.start()

    def change_folder(self):
        old = self._suggested
        if self.app.choose_toolbox():
            if self.name.get() == old:             # user hasn't typed their own name
                self._suggested = storage.suggest_drawer_name(self.app.toolbox)
                self.name.set(self._suggested)
            self.update_final()

    def start(self):
        tb = self.app.toolbox
        probe = tb
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            self.note.config(text=f"Can't save into {storage.shorten(tb)}. "
                                  "Choose a different folder.")
            return
        try:
            self.app.session = Session(self.name.get())
        except OSError as e:
            self.note.config(text=f"Could not start: {e}")
            return
        storage.save_settings(toolbox_dir=str(tb))
        self.app.show("ScanScreen")


# ---------------------------------------------------------------------------
# Scan (live preview)
# ---------------------------------------------------------------------------

class ScanScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = self.photo_canvas(self)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)

        side = tk.Frame(self, bg=PANEL, width=int(280 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=12)
        side.grid_propagate(False)
        side.pack_propagate(False)

        c = app.compact
        # Bottom buttons are packed first so they always keep their space.
        Btn(side, "Finish", self.finish, BLUE, "#ffffff", app.f(14, True), pady=8 if c else 12
            ).pack(fill="x", padx=16, pady=(4, 10), side="bottom")
        Btn(side, "Undo last tool", self.undo, PANEL2, FG, app.f(12), pady=5 if c else 8
            ).pack(fill="x", padx=16, pady=4, side="bottom")
        self.auto_btn = Btn(side, "", self.toggle_auto, PANEL2, FG, app.f(12, True),
                            pady=5 if c else 8)
        self.auto_btn.pack(fill="x", padx=16, pady=4, side="bottom")
        self.autoscan = autoscan.AutoScan()
        tk.Label(side, text="Place the next tool", bg=PANEL, fg=FG,
                 font=app.f(14, True), wraplength=int(250 * app.k)).pack(pady=(10 if c else 16, 2))
        if not c:
            tk.Label(side, text="Keep the marker in view and clear of the tool.", bg=PANEL,
                     fg=MUTED, font=app.f(10), wraplength=int(250 * app.k)).pack()
        self.marker = tk.Label(side, text="", bg=PANEL, font=app.f(13, True))
        self.marker.pack(pady=6 if c else 14)
        self.count = tk.Label(side, text="0", bg=PANEL, fg=FG, font=app.f(28 if c else 40, True))
        self.count.pack()
        tk.Label(side, text="tools scanned", bg=PANEL, fg=MUTED, font=app.f(11)).pack()
        self.expected_step = Stepper(
            app, side, "Tool count" if c else "Tools on the board right now",
            app.settings.expected, 1, 1, scan_service.MAX_TOOLS, "{:.0f}",
            lambda v: setattr(app, "settings", dataclasses.replace(app.settings, expected=int(v))),
            hint="Count them and type it in. Scan will never find more than this.")
        self.expected_step.pack(fill="x", padx=16, pady=(6 if c else 12, 0))
        self.scan_btn = Btn(side, "SCAN", self.scan, GREEN, "#06210f", app.f(26, True),
                            pady=14 if c else 24)
        self.scan_btn.pack(fill="x", padx=16, pady=(8 if c else 14, 6))
        self.auto_lbl = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=app.f(10),
                                 wraplength=int(250 * app.k), justify="center")
        self.auto_lbl.pack(padx=12, pady=(2, 0))
        self.busy = False

    def update_auto_button(self):
        on = self.app.auto_scan
        self.auto_btn.config(text="Auto-scan: ON" if on else "Auto-scan: OFF",
                             bg=GREEN if on else PANEL2, fg=DARK_GREEN_TEXT if on else FG)
        self.auto_btn._color = GREEN if on else PANEL2
        if not on:
            self.auto_lbl.config(text="")

    def toggle_auto(self):
        self.app.set_auto_scan(not self.app.auto_scan)
        self.autoscan.reset()                    # re-learn the empty board
        self.update_auto_button()

    def on_show(self):
        self.busy = False
        self.update_auto_button()
        self.scan_btn.config(text="SCAN")
        self.scan_btn.set_enabled(True)
        self.count.config(text=str(len(self.app.session.tools)))
        self.expected_step.set(self.app.settings.expected)
        self.tick()

    def on_key(self, e):
        if e.keysym == "space" or e.keysym == "Return":
            self.scan()

    def cancel_timers(self):
        super().cancel_timers()
        if getattr(self, "_tick_id", None):
            self.after_cancel(self._tick_id)
            self._tick_id = None

    def tick(self):
        if self.app._closing or self.app.current != "ScanScreen":
            return
        f = self.app.feed.latest()
        if f is not None:
            self.draw(self.canvas, f, is_bgr=True)
        ok = self.app.feed.marker_ok
        self.marker.config(text="Marker found" if ok else "Marker NOT found",
                           fg=GREEN if ok else AMBER)
        if self.app.auto_scan and f is not None:
            fire = self.autoscan.update(f, ok, time.time()) and not self.busy
            self.auto_lbl.config(text=self.autoscan.status_text(ok),
                                 fg=GREEN if self.autoscan.state == autoscan.READY else AMBER)
            if fire:
                self.scan()
        self._tick_id = self.after(80, self.tick)

    def scan(self):
        if self.busy:
            return
        frame = self.app.feed.latest()
        if frame is None:
            self.flash("Camera not ready yet.", AMBER)
            return
        self.busy = True
        self.autoscan.after_scan()             # don't re-scan this tool automatically
        self.scan_btn.config(text="Scanning...")
        self.scan_btn.set_enabled(False)
        settings = self.app.settings

        def work():
            prepared = scan_service.prepare(frame)
            res = scan_service.analyze(prepared, settings)
            res.raw_frame = frame                      # kept for the troubleshooting steps
            return prepared, res

        def ok(res):
            prepared, result = res
            self.busy = False
            self.app.screens["ReviewScreen"].load(prepared, result)
            self.app.show("ReviewScreen")

        def err(e):
            self.busy = False
            self.scan_btn.config(text="SCAN")
            self.scan_btn.set_enabled(True)
            if isinstance(e, ScanError):
                self.flash(str(e), AMBER, 6000)
            else:
                self.flash(f"Error: {e}", RED, 8000)

        self.app.run_bg(work, ok, err)

    def undo(self):
        recs = self.app.session.remove_last()
        if recs:
            self.flash("Removed " + (recs[0].name if len(recs) == 1
                                     else f"{len(recs)} tools from the last scan"), AMBER)
            self.count.config(text=str(len(self.app.session.tools)))
            self.app.show("ScanScreen")
        else:
            self.flash("Nothing to undo.", AMBER)

    def finish(self):
        if not self.app.session.tools:
            self.flash("Scan and confirm at least one tool first.", AMBER)
            return
        self.app.show("DoneScreen")


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

class ReviewScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = self.photo_canvas(self)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        self.canvas.bind("<Configure>", lambda e: self.redraw())

        side = tk.Frame(self, bg=PANEL, width=int(300 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=12)
        side.grid_propagate(False)
        side.pack_propagate(False)

        c = app.compact
        # Confirm / Retake are packed first so small screens never clip them.
        self.confirm_btn = Btn(side, "CONFIRM", self.confirm, GREEN, "#06210f",
                               app.f(20, True), pady=10 if c else 16)
        self.confirm_btn.pack(fill="x", padx=16, pady=(6, 10), side="bottom")
        Btn(side, "Retake", self.retake, AMBER, "#2b1b00", app.f(14, True),
            pady=5 if c else 10).pack(fill="x", padx=16, pady=0, side="bottom")

        tk.Label(side, text="Does the outline look right?", bg=PANEL, fg=FG,
                 font=app.f(13 if c else 14, True), wraplength=int(270 * app.k)
                 ).pack(pady=(8 if c else 14, 2))
        self.size_lbl = tk.Label(side, text="", bg=PANEL, fg=GREEN,
                                 font=app.f(16 if c else 18, True))
        self.size_lbl.pack()
        self.info_lbl = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=app.f(10))
        self.info_lbl.pack()
        self.warn = tk.Label(side, text="", bg=PANEL, fg=AMBER, font=app.f(9 if c else 10),
                             wraplength=int(270 * app.k), justify="left")
        self.warn.pack(pady=(2, 2), padx=12, anchor="w")

        self.view_mode = "overlay"
        self.view_btn = Btn(side, "View: photo + outline", self.toggle_view, PANEL2, FG,
                            app.f(11), pady=3 if c else 6)
        self.view_btn.pack(fill="x", padx=16, pady=(2 if c else 6, 4 if c else 8))

        s = app.settings
        self.steps = {
            "expected": Stepper(app, side, "Tool count" if c else "Tools on the board", s.expected, 1, 1,
                                scan_service.MAX_TOOLS, "{:.0f}",
                                lambda v: self.change(expected=int(v))),
            "offset_mm": Stepper(app, side, "Clearance" if c else "Clearance around tool", s.offset_mm, 0.5, 0, 15,
                                 "{:.1f} mm", lambda v: self.change(offset_mm=v)),
            "adaptive_c": Stepper(app, side, "Threshold" if c else "Edge threshold", s.adaptive_c, 1, 1, 40,
                                  "{:.0f}", lambda v: self.change(adaptive_c=v),
                                  hint="lower = picks up fainter edges"),
            "min_area_px": Stepper(app, side, "Ignore specks" if c else "Ignore specks smaller than", s.min_area_px,
                                   500, 100, 60000, "{:.0f} px²",
                                   lambda v: self.change(min_area_px=int(v))),
        }
        for st in self.steps.values():
            st.pack(fill="x", padx=16, pady=1 if c else 3)

        self.name_var = tk.StringVar()
        if not app.touch:
            tk.Label(side, text="Name (optional)", bg=PANEL, fg=FG, font=app.f(11),
                     anchor="w").pack(fill="x", padx=16, pady=(8, 0))
            self.name_entry = tk.Entry(side, textvariable=self.name_var, font=app.f(12),
                                       bg=PANEL2, fg=FG, insertbackground=FG, relief="flat")
            self.name_entry.pack(fill="x", padx=16, ipady=4)

        self.prepared = None
        self.result: scan_service.ScanResult | None = None
        self._token = 0
        self._debounce = None
        self._saving = False

    # --- data ------------------------------------------------------------

    def load(self, prepared, result):
        self.prepared, self.result = prepared, result
        self._saving = False
        self.name_var.set("")
        self._show_result()

    def on_show(self):
        s = self.app.settings
        self.steps["expected"].set(s.expected)
        self.steps["offset_mm"].set(s.offset_mm)
        self.steps["adaptive_c"].set(s.adaptive_c)
        self.steps["min_area_px"].set(s.min_area_px)
        self.confirm_btn.set_enabled(self.result is not None)
        self.after(50, self.redraw)

    def on_key(self, e):
        if e.keysym in ("Return", "space") and not isinstance(self.focus_get(), tk.Entry):
            self.confirm()
        elif e.keysym in ("BackSpace", "Escape") and not isinstance(self.focus_get(), tk.Entry):
            self.retake()
        elif e.char in ("r", "R") and not isinstance(self.focus_get(), tk.Entry):
            self.retake()

    def _show_result(self):
        r = self.result
        if len(r.parts) > 1:
            self.size_lbl.config(text=f"{len(r.parts)} tools found")
            self.info_lbl.config(text="  ".join(
                f"{i}: {p.width_mm:.0f}×{p.height_mm:.0f}" for i, p in enumerate(r.parts, 1)) + " mm")
        else:
            self.size_lbl.config(text=f"{r.width_mm:.1f} × {r.height_mm:.1f} mm")
            self.info_lbl.config(text=f"1 tool  ·  {r.pixels_per_mm:.2f} px/mm")
        self.warn.config(text="\n".join("! " + w for w in r.warnings))
        self.confirm_btn.set_enabled(True)
        self.redraw()

    def redraw(self):
        if self.result is None or self.app.current != "ReviewScreen":
            return
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        rgb = scan_service.render_overlay(self.result, self.view_mode, (cw, ch))
        self.canvas.photo = png_photo(rgb)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self.canvas.photo)

    def toggle_view(self):
        self.view_mode = "outline" if self.view_mode == "overlay" else "overlay"
        self.view_btn.config(text="View: outline only" if self.view_mode == "outline"
                             else "View: photo + outline")
        self.redraw()

    # --- tuning ------------------------------------------------------------

    def change(self, **kw):
        self.app.settings = dataclasses.replace(self.app.settings, **kw)
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(180, self.reprocess)

    def reprocess(self):
        self._token += 1
        token, prepared, settings = self._token, self.prepared, self.app.settings
        self.confirm_btn.set_enabled(False)

        def ok(result):
            if token != self._token:
                return  # a newer adjustment is already running
            result.raw_frame = self.result.raw_frame if self.result else None
            self.result = result
            self._show_result()

        def err(e):
            if token != self._token:
                return
            self.warn.config(text=f"! {e}" if isinstance(e, ScanError) else f"Error: {e}")

        self.app.run_bg(lambda: scan_service.analyze(prepared, settings), ok, err)

    # --- decisions -----------------------------------------------------------

    def confirm(self):
        if self._saving or self.result is None or not self.confirm_btn._enabled:
            return
        self._saving = True
        self.confirm_btn.config(text="Saving…")
        self.confirm_btn.set_enabled(False)
        result, name, session = self.result, self.name_var.get(), self.app.session

        def ok(recs):
            self.confirm_btn.config(text="CONFIRM")
            self.result = None
            self.app.next_demo_tool()
            self.app.show("ScanScreen")
            msg = (f"Added {recs[0].name}  ({recs[0].width_mm:.0f} × {recs[0].height_mm:.0f} mm)"
                   if len(recs) == 1 else f"Added {len(recs)} tools")
            self.app.screens["ScanScreen"].flash(msg)

        def err(e):
            self._saving = False
            self.confirm_btn.config(text="CONFIRM")
            self.confirm_btn.set_enabled(True)
            self.flash(f"Could not save: {e}", RED, 8000)

        self.app.run_bg(lambda: session.add_scan(result, name), ok, err)

    def retake(self):
        self._token += 1          # drop any adjustment still running
        self.result = None
        self.app.next_demo_tool()
        self.app.show("ScanScreen")


# ---------------------------------------------------------------------------
# Done: layout preview + combined export
# ---------------------------------------------------------------------------

class DoneScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg="#0b0e12", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        self.canvas.bind("<Configure>", lambda e: self.draw_layout())

        side = tk.Frame(self, bg=PANEL, width=int(310 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=12)
        side.grid_propagate(False)
        side.pack_propagate(False)
        wrap = int(280 * app.k)

        # Bottom buttons live in one holder that is packed first, so small
        # screens never clip them. Only one of the two button sets shows.
        holder = tk.Frame(side, bg=PANEL)
        holder.pack(side="bottom", fill="x", padx=16, pady=(4, 10))
        self.unsaved_box = tk.Frame(holder, bg=PANEL)
        self.saved_box = tk.Frame(holder, bg=PANEL)
        py = 8 if c else 12
        Btn(self.unsaved_box, "SAVE", self.save, GREEN, DARK_GREEN_TEXT, app.f(20, True),
            pady=py + 2).pack(fill="x", pady=3)
        Btn(self.unsaved_box, "Scan more tools", self.back, BLUE, "#ffffff", app.f(12, True),
            pady=5 if c else 9).pack(fill="x", pady=3)
        Btn(self.unsaved_box, "Exit", app.request_exit, PANEL2, FG, app.f(11),
            pady=4 if c else 7).pack(fill="x", pady=3)

        self.saved_msg = tk.Label(self.saved_box, text="Everything is saved.\nIt's safe to exit.",
                                  bg=PANEL, fg=GREEN, font=app.f(12 if c else 13, True),
                                  wraplength=wrap, justify="left")
        self.saved_msg.pack(anchor="w", pady=(0, 2))
        self.saved_path = tk.Label(self.saved_box, text="", bg=PANEL, fg=GREEN,
                                   font=app.f(9 if c else 10), wraplength=wrap, justify="left")
        self.saved_path.pack(anchor="w", pady=(0, 4))
        Btn(self.saved_box, "Open folder", self.open_saved, GREEN, DARK_GREEN_TEXT,
            app.f(14, True), pady=py).pack(fill="x", pady=3)
        self.usb_box = tk.Frame(self.saved_box, bg=PANEL)
        self.usb_box.pack(fill="x")
        row = tk.Frame(self.saved_box, bg=PANEL)
        row.pack(fill="x", pady=3)
        Btn(row, "New drawer", app.new_drawer, BLUE, "#ffffff", app.f(11, True),
            pady=5 if c else 8).pack(side="left", fill="x", expand=True, padx=(0, 3))
        Btn(row, "Exit app", app.request_exit, PANEL2, FG, app.f(11, True),
            pady=5 if c else 8).pack(side="left", fill="x", expand=True, padx=(3, 0))

        tk.Label(side, text="Save your drawer", bg=PANEL, fg=FG,
                 font=app.f(14 if c else 15, True)).pack(pady=(10 if c else 14, 0))
        self.summary = tk.Label(side, text="", bg=PANEL, fg=GREEN, font=app.f(11, True))
        self.summary.pack()
        self.size_lbl = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=app.f(10))
        self.size_lbl.pack(pady=(0, 4))

        self.gap = Stepper(app, side, "Gap" if c else "Space between tools",
                           config.DEFAULT_TOOL_GAP_MM, 2.5, 0, 60, "{:.1f} mm",
                           lambda v: self.layout_changed())
        self.gap.pack(fill="x", padx=16, pady=2)
        self.rows_on = False
        self.mode_btn = Btn(side, "Layout: one column", self.toggle_mode, PANEL2, FG,
                            app.f(10), pady=3 if c else 6)
        self.mode_btn.pack(fill="x", padx=16, pady=(4, 2))
        self.width_step = Stepper(app, side, "Max width" if c else "Max width (rows mode)",
                                  600, 50, 100, 2000, "{:.0f} mm",
                                  lambda v: self.layout_changed())

        tk.Label(side, text="Save to:", bg=PANEL, fg=MUTED, font=app.f(10), anchor="w"
                 ).pack(fill="x", padx=16, pady=(8, 0))
        self.dest_lbl = tk.Label(side, text="", bg=PANEL, fg=FG, font=app.f(10, True),
                                 wraplength=wrap, justify="left", anchor="w")
        self.dest_lbl.pack(fill="x", padx=16)
        Btn(side, "Change folder", self.change_folder, PANEL2, FG, app.f(10),
            pady=3 if c else 5).pack(fill="x", padx=16, pady=(4, 0))
        self._saved_path: Path | None = None

    # --- state -----------------------------------------------------------

    def on_show(self):
        s = self.app.session
        n = len(s.tools)
        self.summary.config(text=f"{n} tool{'s' if n != 1 else ''}")
        self.gap.set(s.gap_mm)
        if s.max_width_mm:
            self.rows_on = True
            self.width_step.set(s.max_width_mm)
        self.sync_mode_widgets()
        self.update_dest()
        self.draw_layout()
        if s.saved_to and not s.dirty:
            self.show_saved()
        else:
            self.show_unsaved()

    def update_dest(self):
        self.dest_lbl.config(text=storage.shorten(self.app.destination(), 140))

    def show_unsaved(self):
        self.saved_box.pack_forget()
        self.unsaved_box.pack(fill="x")
        self.update_dest()

    def show_saved(self):
        s = self.app.session
        self._saved_path = s.saved_to
        self.saved_path.config(
            text=f"{storage.shorten(s.saved_to, 140)}\nOne-file copy: {s.title}.toolaerie")
        for w in self.usb_box.winfo_children():
            w.destroy()
        for drive in storage.find_usb_drives()[:2]:
            Btn(self.usb_box, f"Copy to USB: {drive.name[:16]}",
                (lambda d=drive: self.copy_usb(d)), BLUE, "#ffffff",
                self.app.f(11, True), pady=5 if self.app.compact else 8
                ).pack(fill="x", pady=3)
        self.unsaved_box.pack_forget()
        self.saved_box.pack(fill="x")
        self.update_dest()
        self.app.refresh_header()

    # --- layout controls ---------------------------------------------------

    def sync_mode_widgets(self):
        self.mode_btn.config(text="Layout: wrap into rows" if self.rows_on
                             else "Layout: one column")
        if self.rows_on:
            self.width_step.pack(fill="x", padx=16, pady=2, after=self.mode_btn)
        else:
            self.width_step.pack_forget()

    def toggle_mode(self):
        self.rows_on = not self.rows_on
        self.sync_mode_widgets()
        self.layout_changed()

    def layout_changed(self):
        s = self.app.session
        s.gap_mm = self.gap.value
        s.max_width_mm = self.width_step.value if self.rows_on else None
        s.dirty = bool(s.tools)            # a saved copy is now out of date
        self.draw_layout()
        self.show_unsaved()
        self.app.refresh_header()

    def draw_layout(self):
        s = self.app.session
        if not s or not s.tools:
            return
        placements, W, H = s.layout(s.gap_mm, s.max_width_mm)
        self.size_lbl.config(text=f"Overall size {W:.0f} x {H:.0f} mm")
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        pad = 20
        k = min((cw - 2 * pad) / W, (ch - 2 * pad) / H)
        ox, oy = (cw - W * k) / 2, (ch - H * k) / 2
        c.create_rectangle(ox, oy, ox + W * k, oy + H * k, fill="#171d24", outline="#33404e")
        for p in placements:
            for outline in p.tool.preview_outlines_mm:
                pts = []
                for x, y in outline[::max(1, len(outline) // 300)]:
                    pts += [ox + (p.x_mm + x) * k, oy + (p.y_mm + y) * k]
                if len(pts) >= 6:
                    c.create_polygon(pts, fill="#1e5a3a", outline=GREEN, width=1)
            c.create_text(ox + (p.x_mm + 2) * k, oy + (p.y_mm + 2) * k,
                          text=p.tool.name, anchor="nw", fill=FG, font=self.app.f(9))

    # --- actions -----------------------------------------------------------

    def save(self):
        self.app.save_session()

    def change_folder(self):
        if self.app.choose_toolbox():
            self.show_unsaved()
            self.update_dest()

    def back(self):
        self.app.show("ScanScreen")

    def open_saved(self):
        if self._saved_path:
            self.app.open_folder(self._saved_path)

    def copy_usb(self, drive: Path):
        src = self._saved_path
        if not src:
            return
        self.flash(f"Copying to {drive.name}...", MUTED, 30000)

        def work():
            storage.copy_drawer(src, drive / src.name, replace=True)
            os.sync()
            return drive / src.name

        self.app.run_bg(
            work,
            lambda d: self.flash(f"Copied to USB '{drive.name}'. Eject it from the taskbar "
                                 "before unplugging.", GREEN, 9000),
            lambda e: self.flash(f"USB copy failed: {e}", RED, 9000))


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Tool Aerie shadow-board scanner")
    ap.add_argument("--touch", action="store_true", help="fullscreen touchscreen layout")
    ap.add_argument("--demo", action="store_true", help="fake camera, no hardware needed")
    ap.add_argument("--folder", metavar="DIR", help="use photos from a folder as the camera")
    args = ap.parse_args()
    kind = "demo" if args.demo else "folder" if args.folder else "auto"
    sys.excepthook = lambda t, v, tb: storage.log_error("".join(traceback.format_exception(t, v, tb)))
    App(touch=args.touch, camera_kind=kind, folder=args.folder).mainloop()


if __name__ == "__main__":
    main()
