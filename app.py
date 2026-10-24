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
import base64
import dataclasses
import os
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import cv2
import numpy as np

import camera as cam_mod
import config
import scan_service
from scan_service import ScanError, Settings
from session import Session

# --- look & feel -----------------------------------------------------------

BG, PANEL, PANEL2 = "#12161b", "#1c222a", "#252d37"
FG, MUTED = "#e8edf2", "#8d99a8"
GREEN, AMBER, RED, BLUE = "#2ecc71", "#f5a623", "#e5534b", "#3b82f6"


class Btn(tk.Label):
    """A flat, touch-friendly button (tk.Button ignores colours on macOS)."""

    def __init__(self, parent, text, command, color=PANEL2, fg=FG, font=None,
                 padx=18, pady=10, **kw):
        super().__init__(parent, text=text, bg=color, fg=fg, font=font,
                         padx=padx, pady=pady, cursor="hand2", **kw)
        self._color, self._command, self._enabled = color, command, True
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, _e):
        if self._enabled:
            self.config(bg=self._shade(self._color, 1.25))

    def _release(self, e):
        if not self._enabled:
            return
        self.config(bg=self._color)
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self._command()

    def set_enabled(self, on: bool):
        self._enabled = on
        self.config(bg=self._color if on else PANEL, fg=FG if on else MUTED)

    @staticmethod
    def _shade(hexcolor, k):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(min(255, int(c * k)) for c in (r, g, b))


class Stepper(tk.Frame):
    """  Label      [-]  value  [+]  """

    def __init__(self, app, parent, label, value, step, lo, hi, fmt, on_change, hint=None):
        super().__init__(parent, bg=PANEL)
        self.value, self.step, self.lo, self.hi, self.fmt = value, step, lo, hi, fmt
        self.on_change = on_change
        c = app.compact
        lab = tk.Label(self, text=label, bg=PANEL, fg=FG, font=app.f(10 if c else 11),
                       anchor="w")
        minus = Btn(self, "−", lambda: self.bump(-1), font=app.f(14 if c else 16, True),
                    padx=14, pady=0 if c else 2)
        self.lbl = tk.Label(self, text=fmt.format(value), bg=PANEL, fg=GREEN,
                            font=app.f(11 if c else 13, True), width=9)
        plus = Btn(self, "+", lambda: self.bump(+1), font=app.f(14 if c else 16, True),
                   padx=14, pady=0 if c else 2)
        if c:   # one row: label on the left, controls on the right
            self.grid_columnconfigure(0, weight=1)
            lab.grid(row=0, column=0, sticky="w")
            minus.grid(row=0, column=1, padx=(4, 0))
            self.lbl.grid(row=0, column=2)
            plus.grid(row=0, column=3)
        else:
            lab.grid(row=0, column=0, columnspan=3, sticky="w")
            minus.grid(row=1, column=0, pady=3)
            self.lbl.grid(row=1, column=1)
            plus.grid(row=1, column=2, pady=3)
            if hint:
                tk.Label(self, text=hint, bg=PANEL, fg=MUTED, font=app.f(9), anchor="w"
                         ).grid(row=2, column=0, columnspan=3, sticky="w")

    def bump(self, direction):
        v = min(max(self.value + direction * self.step, self.lo), self.hi)
        if abs(v - self.value) > 1e-9:
            self.set(v)
            self.on_change(v)

    def set(self, v):
        self.value = v
        self.lbl.config(text=self.fmt.format(v))


def png_photo(rgb_or_bgr: np.ndarray, is_bgr: bool = False) -> tk.PhotoImage:
    """Numpy image -> Tk PhotoImage via PNG (no PIL.ImageTk dependency)."""
    img = rgb_or_bgr if is_bgr else cv2.cvtColor(rgb_or_bgr, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()))


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
            self.geometry("1180x720")
            self.minsize(900, 600)

        self.settings = Settings()
        self.session: Session | None = None
        self._fonts: dict = {}
        self.startup_error: str | None = None

        try:
            camera = cam_mod.open_camera(camera_kind, folder)
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
        tk.Label(head, text=self.feed.camera.name, bg=PANEL, fg=MUTED, font=self.f(10)
                 ).pack(side="right", padx=14)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.screens = {}
        for cls in (StartScreen, ScanScreen, ReviewScreen, DoneScreen):
            s = cls(body, self)
            s.grid(row=0, column=0, sticky="nsew")
            self.screens[cls.__name__] = s
        self.current = None
        self.show("StartScreen")

        self.bind("<F11>", lambda e: self.attributes(
            "-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Key>", self._key)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

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
        n = len(self.session.tools) if self.session else 0
        self.head_info.config(
            text=f"{self.session.name}   ·   {n} tool{'s' if n != 1 else ''} saved"
            if self.session else "")

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
        self.screens[self.current].flash(f"Error: {exc}", RED, 8000)

    def next_demo_tool(self):
        if isinstance(self.feed.camera, cam_mod.DemoCamera):
            self.feed.camera.next_tool()
        elif isinstance(self.feed.camera, cam_mod.FolderCamera):
            self.feed.camera.advance()

    def _key(self, e):
        self.screens[self.current].on_key(e)

    def quit_app(self):
        try:
            self.feed.stop()
        finally:
            self.destroy()

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


class Screen(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=BG)
        self.app = app
        self._flash_id = None
        self.toast = tk.Label(self, text="", bg=BG, fg=FG, font=app.f(12, True))

    def on_show(self): ...
    def on_key(self, e): ...

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

class StartScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        box = tk.Frame(self, bg=BG)
        box.place(relx=0.5, rely=0.46, anchor="center")
        tk.Label(box, text="New scanning session", bg=BG, fg=FG, font=app.f(24, True)
                 ).pack(pady=(0, 6))
        tk.Label(box, text="One folder per drawer. Name it, then start scanning.",
                 bg=BG, fg=MUTED, font=app.f(12)).pack(pady=(0, 22))
        self.name = tk.StringVar(value=time.strftime("Drawer_%Y%m%d_%H%M"))
        tk.Entry(box, textvariable=self.name, font=app.f(16), justify="center",
                 bg=PANEL2, fg=FG, insertbackground=FG, relief="flat", width=26
                 ).pack(ipady=8, pady=(0, 18))
        Btn(box, "START", self.start, GREEN, "#06210f", app.f(20, True),
            padx=70, pady=16).pack(pady=6)
        Btn(box, "Quit", app.quit_app, PANEL, MUTED, app.f(12), padx=30, pady=8
            ).pack(pady=(16, 0))
        self.note = tk.Label(box, text="", bg=BG, fg=AMBER, font=app.f(11),
                             wraplength=int(520 * app.k))
        self.note.pack(pady=(18, 0))
        tk.Label(box, text=f"Saved to {config.SESSIONS_DIR}", bg=BG, fg=MUTED,
                 font=app.f(10)).pack(pady=(4, 0))

    def on_show(self):
        if self.app.startup_error:
            self.note.config(text=f"{self.app.startup_error}\nUsing the demo camera instead.")

    def on_key(self, e):
        if e.keysym == "Return":
            self.start()

    def start(self):
        try:
            self.app.session = Session(self.name.get())
        except OSError as e:
            self.note.config(text=f"Could not create the session folder: {e}")
            return
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
        tk.Label(side, text="Place the next tool", bg=PANEL, fg=FG,
                 font=app.f(14, True), wraplength=int(250 * app.k)).pack(pady=(10 if c else 16, 2))
        if not c:
            tk.Label(side, text="Keep the marker in view and clear of the tool.", bg=PANEL,
                     fg=MUTED, font=app.f(10), wraplength=int(250 * app.k)).pack()
        self.marker = tk.Label(side, text="", bg=PANEL, font=app.f(13, True))
        self.marker.pack(pady=6 if c else 14)
        self.count = tk.Label(side, text="0", bg=PANEL, fg=FG, font=app.f(28 if c else 40, True))
        self.count.pack()
        tk.Label(side, text="tools saved", bg=PANEL, fg=MUTED, font=app.f(11)).pack()
        self.scan_btn = Btn(side, "SCAN", self.scan, GREEN, "#06210f", app.f(26, True),
                            pady=14 if c else 24)
        self.scan_btn.pack(fill="x", padx=16, pady=(10 if c else 22, 6))
        self.busy = False

    def on_show(self):
        self.busy = False
        self.scan_btn.config(text="SCAN")
        self.scan_btn.set_enabled(True)
        self.count.config(text=str(len(self.app.session.tools)))
        self.tick()

    def on_key(self, e):
        if e.keysym == "space" or e.keysym == "Return":
            self.scan()

    def tick(self):
        if self.app.current != "ScanScreen":
            return
        f = self.app.feed.latest()
        if f is not None:
            self.draw(self.canvas, f, is_bgr=True)
        ok = self.app.feed.marker_ok
        self.marker.config(text="Marker found" if ok else "Marker NOT found",
                           fg=GREEN if ok else AMBER)
        self.after(80, self.tick)

    def scan(self):
        if self.busy:
            return
        frame = self.app.feed.latest()
        if frame is None:
            self.flash("Camera not ready yet.", AMBER)
            return
        self.busy = True
        self.scan_btn.config(text="Scanning…")
        self.scan_btn.set_enabled(False)
        settings = self.app.settings

        def work():
            prepared = scan_service.prepare(frame)
            return prepared, scan_service.analyze(prepared, settings)

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
        rec = self.app.session.remove_last()
        if rec:
            self.flash(f"Removed {rec.name}", AMBER)
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
        self.size_lbl.config(text=f"{r.width_mm:.1f} × {r.height_mm:.1f} mm")
        self.info_lbl.config(
            text=f"{r.pieces} piece{'s' if r.pieces != 1 else ''}  ·  {r.pixels_per_mm:.2f} px/mm")
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

        def ok(rec):
            self.confirm_btn.config(text="CONFIRM")
            self.result = None
            self.app.next_demo_tool()
            self.app.show("ScanScreen")
            self.app.screens["ScanScreen"].flash(
                f"Saved {rec.name}  ({rec.width_mm:.0f} × {rec.height_mm:.0f} mm)")

        def err(e):
            self._saving = False
            self.confirm_btn.config(text="CONFIRM")
            self.confirm_btn.set_enabled(True)
            self.flash(f"Could not save: {e}", RED, 8000)

        self.app.run_bg(lambda: session.add_tool(result, name), ok, err)

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
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg="#0b0e12", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        self.canvas.bind("<Configure>", lambda e: self.draw_layout())

        side = tk.Frame(self, bg=PANEL, width=int(300 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=12)
        side.grid_propagate(False)
        side.pack_propagate(False)

        # Bottom buttons are packed first so they always keep their space.
        Btn(side, "New session", self.new_session, PANEL2, FG, app.f(12),
            pady=5 if app.compact else 8).pack(fill="x", padx=16, pady=(4, 10), side="bottom")
        Btn(side, "Scan more tools", self.back, BLUE, "#ffffff", app.f(13, True),
            pady=6 if app.compact else 10).pack(fill="x", padx=16, pady=3, side="bottom")
        self.open_btn = Btn(side, "Open folder", self.open_folder, GREEN, "#06210f",
                            app.f(14, True), pady=8 if app.compact else 12)
        self.open_btn.pack(fill="x", padx=16, pady=3, side="bottom")

        tk.Label(side, text="Shadow-board file", bg=PANEL, fg=FG, font=app.f(15, True)
                 ).pack(pady=(14, 2))
        self.summary = tk.Label(side, text="", bg=PANEL, fg=GREEN, font=app.f(12, True))
        self.summary.pack()
        self.size_lbl = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=app.f(10))
        self.size_lbl.pack(pady=(0, 8))

        self.gap = Stepper(app, side, "Gap" if app.compact else "Space between tools", config.DEFAULT_TOOL_GAP_MM,
                           2.5, 0, 60, "{:.1f} mm", lambda v: self.changed())
        self.gap.pack(fill="x", padx=16, pady=4)
        self.rows_on = False
        self.mode_btn = Btn(side, "Layout: one column", self.toggle_mode, PANEL2, FG,
                            app.f(11), pady=6)
        self.mode_btn.pack(fill="x", padx=16, pady=(8, 2))
        self.width_step = Stepper(app, side, "Max width" if app.compact else "Max width (rows mode)", 600, 50, 100, 2000,
                                  "{:.0f} mm", lambda v: self.changed())
        self.width_step.pack(fill="x", padx=16, pady=4)
        self.width_step.pack_forget()

        self.status = tk.Label(side, text="", bg=PANEL, fg=MUTED, font=app.f(10),
                               wraplength=int(270 * app.k), justify="left")
        self.status.pack(padx=14, pady=10, anchor="w")

        self._debounce = None
        self._path = None

    def on_show(self):
        n = len(self.app.session.tools)
        self.summary.config(text=f"{n} tool{'s' if n != 1 else ''}")
        self.gap.set(self.app.session.gap_mm)
        self.export_now()

    def toggle_mode(self):
        self.rows_on = not self.rows_on
        self.mode_btn.config(text="Layout: wrap into rows" if self.rows_on
                             else "Layout: one column")
        if self.rows_on:
            self.width_step.pack(fill="x", padx=16, pady=4, after=self.mode_btn)
        else:
            self.width_step.pack_forget()
        self.changed()

    def _args(self):
        return self.gap.value, (self.width_step.value if self.rows_on else None)

    def changed(self):
        self.draw_layout()
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(400, self.export_now)

    def export_now(self):
        gap, max_w = self._args()
        session = self.app.session
        self.status.config(text="Saving…", fg=MUTED)

        def ok(path):
            self._path = path
            self.status.config(text=f"Saved:\n{path}", fg=GREEN)
            self.draw_layout()

        self.app.run_bg(lambda: session.export_combined(gap, max_w), ok,
                        lambda e: self.status.config(text=f"Could not save: {e}", fg=RED))

    def draw_layout(self):
        s = self.app.session
        if not s or not s.tools:
            return
        gap, max_w = self._args()
        placements, W, H = s.layout(gap, max_w)
        self.size_lbl.config(text=f"Overall size {W:.0f} × {H:.0f} mm")
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
                          text=p.tool.name, anchor="nw", fill=FG,
                          font=self.app.f(9))

    def open_folder(self):
        self.app.open_folder(self.app.session.folder)

    def back(self):
        self.app.show("ScanScreen")

    def new_session(self):
        self.app.session = None
        self.app.screens["StartScreen"].name.set(time.strftime("Drawer_%Y%m%d_%H%M"))
        self.app.show("StartScreen")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Tool Aerie shadow-board scanner")
    ap.add_argument("--touch", action="store_true", help="fullscreen touchscreen layout")
    ap.add_argument("--demo", action="store_true", help="fake camera, no hardware needed")
    ap.add_argument("--folder", metavar="DIR", help="use photos from a folder as the camera")
    args = ap.parse_args()
    kind = "demo" if args.demo else "folder" if args.folder else "auto"
    App(touch=args.touch, camera_kind=kind, folder=args.folder).mainloop()


if __name__ == "__main__":
    main()
