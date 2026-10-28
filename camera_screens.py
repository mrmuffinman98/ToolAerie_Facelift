# camera_screens.py
# The "Camera setup" screens: camera profiles, a planner for a new setup, a guided
# lens calibration with the checkerboard, and a ready check.
#
# All the maths lives in calibration.py (no Tk there). These screens only guide
# the person through it. The scan pipeline is not touched here.

from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import tkinter as tk

import calibration as cal
import camera as cam_mod
import config
import scan_service
import storage
from ui import (BG, PANEL, PANEL2, FG, MUTED, GREEN, AMBER, RED, BLUE, DARK_GREEN_TEXT,
                Btn, Screen, Stepper, ask)


def _num(text: str, lo: float, hi: float) -> float | None:
    try:
        v = float(text.strip().replace(",", ".").lower().replace("mm", ""))
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def _entry(parent, app, var, width=8, **kw):
    return tk.Entry(parent, textvariable=var, width=width, font=app.f(13), justify="center",
                    bg=PANEL2, fg=FG, insertbackground=FG, relief="flat", **kw)


def _title(screen, app, text, sub=None):
    c = app.compact
    tk.Label(screen, text=text, bg=BG, fg=FG, font=app.f(18 if c else 22, True)
             ).pack(pady=(8 if c else 16, 0))
    if sub:
        tk.Label(screen, text=sub, bg=BG, fg=MUTED, font=app.f(10 if c else 11),
                 wraplength=int(720 * app.k)).pack(pady=(0, 6))


# ---------------------------------------------------------------------------
# Profile list
# ---------------------------------------------------------------------------

class CameraScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        _title(self, app, "Camera setup",
               "A camera profile holds the lens correction, focus and crop for one camera position. "
               "Make a new one whenever the camera's height or the board changes.")
        self.rows = tk.Frame(self, bg=BG)
        self.rows.pack(fill="x", padx=24)
        self.note = tk.Label(self, text="", bg=BG, fg=GREEN, font=app.f(11),
                             wraplength=int(720 * app.k))
        self.note.pack(pady=4)
        bar = tk.Frame(self, bg=BG)
        bar.pack(pady=4)
        Btn(bar, "+ New profile (calibrate)", self.new, BLUE, "#ffffff", app.f(13, True),
            padx=16, pady=6).pack(side="left", padx=5)
        Btn(bar, "Ready check", lambda: app.show("CalibReadyScreen"), PANEL2, FG, app.f(12),
            padx=16, pady=6).pack(side="left", padx=5)
        Btn(bar, "Done", lambda: app.show("HomeScreen"), GREEN, DARK_GREEN_TEXT,
            app.f(13, True), padx=26, pady=6).pack(side="left", padx=5)

    def on_show(self):
        self.refresh()

    def refresh(self):
        for w in self.rows.winfo_children():
            w.destroy()
        act = cal.active()
        for p in cal.load_all():
            on = p.id == act.id
            row = tk.Frame(self.rows, bg=PANEL, highlightthickness=2 if on else 0,
                           highlightbackground=GREEN)
            row.pack(fill="x", pady=3)
            info = tk.Frame(row, bg=PANEL)
            info.pack(side="left", fill="x", expand=True, padx=10, pady=4)
            tk.Label(info, text=p.name + ("   IN USE" if on else ""), bg=PANEL,
                     fg=GREEN if on else FG, font=self.app.f(13, True), anchor="w").pack(fill="x")
            tk.Label(info, text=p.summary() + (f"  -  checked {p.verified}" if p.verified else ""),
                     bg=PANEL, fg=MUTED, font=self.app.f(10), anchor="w").pack(fill="x")
            if not on:
                Btn(row, "Use", lambda q=p: self.use(q), GREEN, DARK_GREEN_TEXT,
                    self.app.f(11, True), padx=14, pady=3).pack(side="right", padx=6)
            if not p.builtin:
                Btn(row, "Delete", lambda q=p: self.delete(q), PANEL2, MUTED,
                    self.app.f(10), padx=10, pady=3).pack(side="right", padx=2)

    def use(self, p):
        cal.set_active(p.id)
        cal.apply(p)
        self.note.config(text=f"Switching to '{p.name}' (restarting the camera)...", fg=AMBER)
        self.app.restart_camera(done=lambda: (self.note.config(
            text=f"'{p.name}' is in use. Run the Ready check to confirm.", fg=GREEN),
            self.refresh()))

    def delete(self, p):
        if ask(self.app, "Delete profile?",
               f"Delete '{p.name}' and its calibration photos?\nScans already saved are not affected.",
               [("Delete", True, RED, "#ffffff"), ("Keep it", False, PANEL2, FG)],
               cancel_value=False):
            was_active = cal.active().id == p.id
            cal.delete_profile(p.id)
            if was_active:
                cal.apply_active()
                self.app.restart_camera(done=self.refresh)
            self.refresh()

    def new(self):
        self.app.calib = {}
        self.app.show("CalibPlanScreen")


# ---------------------------------------------------------------------------
# Step 1: plan the new setup
# ---------------------------------------------------------------------------

class CalibPlanScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        _title(self, app, "New camera profile: 1 of 4",
               "Tell me about the new board. I'll work out how high the camera goes and its focus.")
        body = tk.Frame(self, bg=BG)
        body.pack(padx=24)
        self.name = tk.StringVar(value="Larger board")
        self.bw, self.bd = tk.StringVar(value="600"), tk.StringVar(value="400")
        self.dist = tk.StringVar()
        self.res_name = list(cal.RESOLUTIONS)[0]
        self._manual = False
        lab = dict(bg=BG, fg=FG, font=app.f(11), anchor="w")
        g = tk.Frame(body, bg=BG)
        g.grid(row=0, column=0, sticky="n", padx=(0, 24))
        tk.Label(g, text="Profile name", **lab).grid(row=0, column=0, sticky="w", pady=3)
        _entry(g, app, self.name, 22).grid(row=0, column=1, columnspan=2, pady=3, ipady=3)
        tk.Label(g, text="Board width x depth (mm)", **lab).grid(row=1, column=0, sticky="w", pady=3)
        _entry(g, app, self.bw, 7).grid(row=1, column=1, pady=3, ipady=3, padx=(0, 4))
        _entry(g, app, self.bd, 7).grid(row=1, column=2, pady=3, ipady=3)
        tk.Label(g, text="Picture size", **lab).grid(row=2, column=0, sticky="w", pady=3)
        self.res_btns = {}
        rb = tk.Frame(g, bg=BG)
        rb.grid(row=2, column=1, columnspan=2, sticky="w")
        for n in cal.RESOLUTIONS:
            b = Btn(rb, n.split(" ")[0], lambda n=n: self.pick_res(n), PANEL2, FG, app.f(11),
                    padx=10, pady=3)
            b.pack(side="left", padx=(0, 4))
            self.res_btns[n] = b
        tk.Label(g, text="Camera height (mm)", **lab).grid(row=3, column=0, sticky="w", pady=3)
        de = _entry(g, app, self.dist, 7)
        de.grid(row=3, column=1, pady=3, ipady=3)
        de.bind("<KeyRelease>", lambda e: self._set_manual())
        tk.Label(g, text="(filled in for you)", bg=BG, fg=MUTED, font=app.f(9)
                 ).grid(row=3, column=2, sticky="w")
        self.out = tk.Label(body, text="", bg=PANEL, fg=FG, font=app.f(11), justify="left",
                            anchor="nw", padx=12, pady=10, wraplength=int(320 * app.k))
        self.out.grid(row=0, column=1, sticky="n")
        self.note = tk.Label(self, text="", bg=BG, fg=AMBER, font=app.f(10),
                             wraplength=int(720 * app.k))
        self.note.pack(pady=4)
        bar = tk.Frame(self, bg=BG)
        bar.pack(pady=4)
        Btn(bar, "Next: print the checkerboard", self.next, GREEN, DARK_GREEN_TEXT,
            app.f(13, True), padx=22, pady=6).pack(side="left", padx=5)
        Btn(bar, "Cancel", lambda: app.show("CameraScreen"), PANEL2, MUTED, app.f(12),
            padx=18, pady=6).pack(side="left", padx=5)
        for v in (self.bw, self.bd):
            v.trace_add("write", lambda *_: self.recalc(from_board=True))
        self.dist.trace_add("write", lambda *_: self.recalc())

    def _set_manual(self):
        self._manual = True

    def pick_res(self, n):
        self.res_name = n
        self.recalc(from_board=True)

    def on_show(self):
        self._manual = False
        self.recalc(from_board=True)

    def recalc(self, from_board=False):
        res = cal.RESOLUTIONS[self.res_name]
        for n, b in self.res_btns.items():
            b._color = GREEN if n == self.res_name else PANEL2
            b._fg = DARK_GREEN_TEXT if n == self.res_name else FG
            b.config(bg=b._color, fg=b._fg)
        w, d = _num(self.bw.get(), 100, 5000), _num(self.bd.get(), 100, 5000)
        if from_board and not self._manual and w and d:
            self.dist.set(f"{cal.plan(w, d, res).distance_mm:.0f}")
        dist = _num(self.dist.get(), 150, 5000)
        if not dist:
            self.out.config(text="Enter the board size and camera height.")
            return
        pl = cal.plan_at(dist, res)
        txt = (f"Camera about {pl.distance_mm / 1000:.2f} m above the board.\n"
               f"It will see {pl.view_w_mm:.0f} x {pl.view_h_mm:.0f} mm.\n"
               f"Focus setting: {pl.lens_position:g}  (set automatically)\n"
               f"Detail: {pl.px_per_mm:.2f} pixels per mm  (1 pixel = {1 / pl.px_per_mm:.2f} mm)")
        if w and d and (pl.view_w_mm < w * 1.02 or pl.view_h_mm < d * 1.02):
            txt += "\n\nAt this height the camera can't see the whole board."
        if pl.px_per_mm < 2.5:
            txt += "\n\nBelow about 2.5 px/mm shapes get coarse. Consider High picture size."
        self.out.config(text=txt)

    def next(self):
        res = cal.RESOLUTIONS[self.res_name]
        w, d = _num(self.bw.get(), 100, 5000), _num(self.bd.get(), 100, 5000)
        dist = _num(self.dist.get(), 150, 5000)
        if not self.name.get().strip():
            self.note.config(text="Give the profile a name.")
            return
        if not (w and d and dist):
            self.note.config(text="Board size must be 100-5000 mm and height 150-5000 mm.")
            return
        pl = cal.plan_at(dist, res)
        self.app.calib = dict(name=self.name.get().strip(), res=res, distance=dist,
                              board=[w, d], lens=pl.lens_position, view_w=pl.view_w_mm,
                              res_name=self.res_name)
        self.note.config(text="")
        self.app.show("CalibPrintScreen")


# ---------------------------------------------------------------------------
# Step 2: print the checkerboard
# ---------------------------------------------------------------------------

class CalibPrintScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        _title(self, app, "New camera profile: 2 of 4",
               "Print the checkerboard. The camera uses it to measure its own lens distortion.")
        self.paper = "Letter"
        self.info = tk.Label(self, text="", bg=PANEL, fg=FG, font=app.f(12), justify="left",
                             padx=14, pady=10, wraplength=int(660 * app.k))
        self.info.pack(pady=6)
        pr = tk.Frame(self, bg=BG)
        pr.pack()
        tk.Label(pr, text="Paper:", bg=BG, fg=FG, font=app.f(11)).pack(side="left", padx=4)
        self.pbtn = {}
        for n in cal.PAPERS:
            b = Btn(pr, n, lambda n=n: self.pick(n), PANEL2, FG, app.f(11), padx=12, pady=3)
            b.pack(side="left", padx=3)
            self.pbtn[n] = b
        self.note = tk.Label(self, text="", bg=BG, fg=GREEN, font=app.f(11),
                             wraplength=int(700 * app.k))
        self.note.pack(pady=6)
        bar = tk.Frame(self, bg=BG)
        bar.pack(pady=4)
        Btn(bar, "Print PDF", self.print, BLUE, "#ffffff", app.f(13, True), padx=20, pady=6
            ).pack(side="left", padx=5)
        Btn(bar, "I have it: take photos", self.go, GREEN, DARK_GREEN_TEXT, app.f(13, True),
            padx=20, pady=6).pack(side="left", padx=5)
        Btn(bar, "Back", lambda: app.show("CalibPlanScreen"), PANEL2, MUTED, app.f(12),
            padx=16, pady=6).pack(side="left", padx=5)

    def on_show(self):
        self.note.config(text="")
        self.refresh()

    def pick(self, n):
        self.paper = n
        self.refresh()

    def square(self) -> float:
        return cal.square_size_mm(self.app.calib["view_w"], self.paper)

    def refresh(self):
        for n, b in self.pbtn.items():
            b._color = GREEN if n == self.paper else PANEL2
            b._fg = DARK_GREEN_TEXT if n == self.paper else FG
            b.config(bg=b._color, fg=b._fg)
        s = self.square()
        w, h = s * cal.SQUARES[0], s * cal.SQUARES[1]
        want = 0.40 * self.app.calib["view_w"] / cal.SQUARES[0]
        txt = (f"Squares {s:g} mm  ->  the pattern is {w:.0f} x {h:.0f} mm "
               f"(10 x 7 squares).\n\n"
               "1. Print at 100% / Actual size on stiff, flat paper or glue it to a flat board.\n"
               "2. It must be perfectly flat. Ripples ruin the calibration.\n"
               "3. Keep the white border.")
        if s < want * 0.7:
            txt += ("\n\nFrom this height the pattern will look small. It works, but bigger "
                    "paper (Tabloid) gives a better calibration.")
        self.info.config(text=txt)

    def print(self):
        try:
            path = cal.checkerboard_pdf(self.square(), self.paper)
        except (OSError, ValueError) as e:
            self.note.config(text=f"Could not make the PDF: {e}", fg=AMBER)
            return
        self.note.config(text=f"Saved {storage.shorten(path, 60)}", fg=GREEN)
        if ask(self.app, "Checkerboard PDF saved",
               f"{path.name}\nis in\n{storage.shorten(path.parent, 50)}\n\nPrint at 100% / Actual size.",
               [("Open folder", True, BLUE, "#ffffff"), ("OK", False, GREEN, DARK_GREEN_TEXT)],
               cancel_value=False):
            self.app.open_folder(path.parent)

    def go(self):
        self.app.show("CalibCaptureScreen")


# ---------------------------------------------------------------------------
# Step 3: take the calibration photos
# ---------------------------------------------------------------------------

class CalibCaptureScreen(Screen):
    STABLE_S = 1.0
    COOLDOWN_S = 1.5

    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = self.photo_canvas(self)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        side = tk.Frame(self, bg=PANEL, width=int(250 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(4, 8), pady=8)
        side.grid_propagate(False)
        tk.Label(side, text="New profile: 3 of 4", bg=PANEL, fg=MUTED, font=app.f(10)
                 ).pack(pady=(8, 0))
        self.count = tk.Label(side, text="0 photos", bg=PANEL, fg=GREEN, font=app.f(18 if c else 22, True))
        self.count.pack()
        self.cov = tk.Canvas(side, width=int(120 * app.k), height=int(80 * app.k), bg=PANEL,
                             highlightthickness=0)
        self.cov.pack(pady=4)
        self.hint = tk.Label(side, text="", bg=PANEL, fg=FG, font=app.f(11), wraplength=int(230 * app.k),
                             justify="left")
        self.hint.pack(padx=8, pady=4, fill="x")
        self.auto_btn = Btn(side, "", self.toggle_auto, PANEL2, FG, app.f(10), padx=8, pady=3)
        self.auto_btn.pack(pady=2)
        Btn(side, "Take photo now", self.manual, BLUE, "#ffffff", app.f(11, True), padx=10, pady=4
            ).pack(pady=2, fill="x", padx=10)
        self.finish_btn = Btn(side, "Calibrate", self.finish, GREEN, DARK_GREEN_TEXT, app.f(12, True),
                              padx=10, pady=5)
        self.finish_btn.pack(pady=2, fill="x", padx=10)
        row = tk.Frame(side, bg=PANEL)
        row.pack(pady=2)
        Btn(row, "Undo", self.undo, PANEL2, FG, app.f(10), padx=10, pady=3).pack(side="left", padx=3)
        Btn(row, "Cancel", self.cancel, PANEL2, MUTED, app.f(10), padx=10, pady=3).pack(side="left", padx=3)
        self.auto = True
        self._reset_state()

    def _reset_state(self):
        self.sets: list[np.ndarray] = []
        self.sigs: list[tuple] = []
        self.files: list = []
        self.size = (0, 0)
        self._det = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tick_id = None
        self._busy = False
        self._last_cap = 0.0
        self._stable_since = None
        self._prev_c = None
        self._live = False
        self._msg_until = 0.0
        self._not_new = False

    # --- lifecycle -----------------------------------------------------

    def on_show(self):
        self._stop.set()
        d = self.app.calib
        if d.pop("resume", False) and d.get("sets"):
            keep = (d["sets"], d["files"], d["size"])      # adding to what we have
            self._reset_state()
            self.sets, self.files, self.size = list(keep[0]), list(keep[1]), keep[2]
            self.sigs = [cal.pose_signature(c) for c in self.sets]
            self.update_labels()
            self._camera_ready()
            return
        self._reset_state()
        d["id"] = d.get("id") or cal.make_id(d["name"])
        self.hint.config(text="Starting the camera at the new settings...", fg=AMBER)
        self.update_labels()
        config.IMAGE_RESOLUTION = tuple(d["res"])
        config.LENS_POSITION = d["lens"]
        self.app.restart_camera(done=self._camera_ready, simulate_board=True)

    def _camera_ready(self):
        if self.app.current != "CalibCaptureScreen":
            return
        self._live = True
        self._stop = threading.Event()
        threading.Thread(target=self._detect_loop, args=(self._stop,), daemon=True).start()
        self.tick()

    def on_hide(self):
        self._stop.set()
        self._live = False
        if self._tick_id:
            self.after_cancel(self._tick_id)
            self._tick_id = None

    def _detect_loop(self, stop):
        while not stop.is_set():
            f = self.app.feed.latest()
            if f is None:
                time.sleep(0.1)
                continue
            try:
                c = cal.detect_live(f)
            except cv2.error:
                c = None
            with self._lock:
                self._det = (f, c)
            time.sleep(0.05)

    # --- guidance ---------------------------------------------------------

    def tick(self):
        if not self._live:
            return
        self._tick_id = self.after(120, self.tick)
        with self._lock:
            det = self._det
        if det is None:
            return
        f, c = det
        self.size = (f.shape[1], f.shape[0])
        now = time.time()
        self._update_stability(c, now)
        self._maybe_auto(f, c, now)
        self._draw(f, c)
        self._set_hint(c, now)

    def _update_stability(self, c, now):
        if c is None:
            self._stable_since, self._prev_c = None, None
            return
        m = c.reshape(-1, 2).mean(axis=0)
        if self._prev_c is not None and np.hypot(*(m - self._prev_c)) < 0.004 * self.size[0]:
            if self._stable_since is None:
                self._stable_since = now
        else:
            self._stable_since = None
        self._prev_c = m

    def _maybe_auto(self, f, c, now):
        self._not_new = False
        if c is None or self._busy or len(self.sets) >= cal.MAX_IMAGES:
            return
        if self._stable_since is None or now - self._stable_since < self.STABLE_S:
            return
        if now - self._last_cap < self.COOLDOWN_S:
            return
        if not cal.is_new_pose(cal.pose_signature(c), self.sigs, self.size[0]):
            self._not_new = True
            return
        if self.auto:
            self._capture(f)

    def _set_hint(self, c, now):
        if now < self._msg_until:
            return
        n = len(self.sets)
        if self._busy:
            txt, col = "Reading the photo...", AMBER
        elif c is None:
            txt, col = ("Hold the checkerboard in view, all of it inside the picture, "
                        "tilted a little."), FG
        elif self._not_new:
            txt, col = "Move or tilt it to a new position.", AMBER
        elif self._stable_since is None:
            txt, col = "Hold still...", AMBER
        else:
            txt, col = ("Got it." if self.auto else "Press Take photo now."), GREEN
        if n >= 3 and c is None:
            r, cc = cal.emptiest_cell(cal.coverage(self.sets, self.size))
            txt += f"\nNext: put it in the {cal.CELL_NAMES[r][cc]}."
        self.hint.config(text=txt, fg=col)

    def _draw(self, f, c):
        W = 800
        k = min(1.0, W / f.shape[1])
        img = cv2.resize(f, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else f.copy()
        h, w = img.shape[:2]
        g = cal.coverage(self.sets, self.size) if self.sets else np.zeros((cal.GRID,) * 2, int)
        for i in range(1, cal.GRID):
            cv2.line(img, (w * i // cal.GRID, 0), (w * i // cal.GRID, h), (90, 90, 90), 1)
            cv2.line(img, (0, h * i // cal.GRID), (w, h * i // cal.GRID), (90, 90, 90), 1)
        for r in range(cal.GRID):
            for cc in range(cal.GRID):
                col = (80, 200, 80) if g[r, cc] >= cal.MIN_PER_CELL else (60, 160, 245)
                cv2.putText(img, str(g[r, cc]), (w * cc // cal.GRID + 8, h * (r + 1) // cal.GRID - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2, cv2.LINE_AA)
        if c is not None:
            stable = self._stable_since is not None
            col = (80, 220, 100) if stable else (60, 160, 245)
            for p in (c.reshape(-1, 2) * k):
                cv2.circle(img, (int(p[0]), int(p[1])), 4, col, -1, cv2.LINE_AA)
        self.draw(self.canvas, img, is_bgr=True)
        self._draw_cov(g)

    def _draw_cov(self, g):
        cv_ = self.cov
        cv_.delete("all")
        w, h = int(cv_["width"]), int(cv_["height"])
        for r in range(cal.GRID):
            for c in range(cal.GRID):
                n = g[r, c]
                col = GREEN if n >= cal.MIN_PER_CELL else AMBER if n else PANEL2
                cv_.create_rectangle(c * w // 3 + 2, r * h // 3 + 2, (c + 1) * w // 3 - 2,
                                     (r + 1) * h // 3 - 2, fill=col, outline="")
                cv_.create_text(c * w // 3 + w // 6, r * h // 3 + h // 6, text=str(n),
                                fill=DARK_GREEN_TEXT if n else MUTED, font=self.app.f(10, True))

    def update_labels(self):
        n = len(self.sets)
        self.count.config(text=f"{n} photo{'s' if n != 1 else ''}",
                          fg=GREEN if n >= cal.MIN_IMAGES else FG)
        self.auto_btn.config(text=f"Auto-capture: {'ON' if self.auto else 'OFF'}")
        enough = n >= cal.MIN_IMAGES
        self.finish_btn.set_enabled(enough)
        if enough:
            g = cal.coverage(self.sets, self.size) if self.size[0] else None
            good = n >= cal.GOOD_IMAGES and g is not None and g.min() >= cal.MIN_PER_CELL
            self.finish_btn.config(text="Calibrate now" if good else f"Calibrate ({n})")
        else:
            self.finish_btn.config(text=f"Need {cal.MIN_IMAGES - n} more")

    # --- actions ------------------------------------------------------------

    def toggle_auto(self):
        self.auto = not self.auto
        self.update_labels()

    def manual(self):
        with self._lock:
            det = self._det
        if det is None or det[1] is None:
            self._say("The checkerboard isn't in view.", AMBER)
            return
        self._capture(det[0])

    def _say(self, text, color=FG, secs=2.5):
        self.hint.config(text=text, fg=color)
        self._msg_until = time.time() + secs

    def _capture(self, frame):
        if self._busy:
            return
        self._busy = True
        self._last_cap = time.time()
        d = self.app.calib
        n = len(self.sets) + 1

        def work():
            corners = cal.detect_precise(frame)
            if corners is None:
                return None
            path = cal.images_dir(d["id"]) / f"img_{n:02d}_{int(time.time())}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            return corners, path

        def ok(res):
            self._busy = False
            if res is None:
                self._say("Couldn't read the corners in that photo. Hold it steadier or flatter.", AMBER)
                return
            corners, path = res
            self.sets.append(corners)
            self.sigs.append(cal.pose_signature(corners))
            self.files.append(path)
            self.size = (frame.shape[1], frame.shape[0])
            self._say(f"Photo {len(self.sets)} saved. Move to a new spot.", GREEN)
            self.update_labels()

        def err(e):
            self._busy = False
            self._say(f"Error: {e}", RED, 6)

        self.app.run_bg(work, ok, err)

    def undo(self):
        if self.sets and not self._busy:
            self.sets.pop(), self.sigs.pop()
            p = self.files.pop()
            try:
                p.unlink()
            except OSError:
                pass
            self.update_labels()

    def finish(self):
        if len(self.sets) < cal.MIN_IMAGES or self._busy:
            return
        d = self.app.calib
        d.update(sets=list(self.sets), size=self.size, files=list(self.files))
        self.app.show("CalibResultScreen")

    def cancel(self):
        d = self.app.calib
        import shutil
        shutil.rmtree(cal.images_dir(d.get("id", "x")), ignore_errors=True)
        cal.apply_active()
        self.app.restart_camera(done=lambda: None)
        self.app.show("CameraScreen")


# ---------------------------------------------------------------------------
# Step 4: result
# ---------------------------------------------------------------------------

class CalibResultScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.res: cal.CalibResult | None = None
        self.trim = 0
        _title(self, app, "New camera profile: 4 of 4")
        self.canvas = self.photo_canvas(self)
        self.canvas.configure(height=int(185 * app.k))
        self.canvas.pack(fill="x", padx=12)
        self.verdict = tk.Label(self, text="", bg=BG, fg=FG, font=app.f(13, True))
        self.verdict.pack(pady=(4, 0))
        self.detail = tk.Label(self, text="", bg=BG, fg=MUTED, font=app.f(10), justify="center",
                               wraplength=int(720 * app.k))
        self.detail.pack()
        self.trim_box = Stepper(app, self, "Trim extra edge (px)", 0, 10, 0, 200, "{:.0f}",
                                self.set_trim)
        self.trim_box.pack(pady=2)
        bar = tk.Frame(self, bg=BG)
        bar.pack(pady=6)
        self.save_btn = Btn(bar, "Save and use this profile", self.save, GREEN, DARK_GREEN_TEXT,
                            app.f(13, True), padx=18, pady=6)
        self.save_btn.pack(side="left", padx=5)
        Btn(bar, "Add more photos", self.more, PANEL2, FG, app.f(11), padx=12, pady=6
            ).pack(side="left", padx=5)
        Btn(bar, "Redo all", self.redo, PANEL2, FG, app.f(11), padx=12, pady=6
            ).pack(side="left", padx=5)
        Btn(bar, "Discard", self.discard, PANEL2, MUTED, app.f(11), padx=14, pady=6
            ).pack(side="left", padx=5)

    def on_show(self):
        d = self.app.calib
        self.res, self.trim = None, 0
        self.trim_box.set(0)
        self.verdict.config(text="Calibrating...", fg=AMBER)
        self.detail.config(text="")
        self.save_btn.set_enabled(False)
        self.canvas.delete("all")
        self.app.run_bg(lambda: cal.compute(d["sets"], d["size"]), self._done, self._fail)

    def _fail(self, e):
        self.verdict.config(text="Calibration failed", fg=RED)
        self.detail.config(text=str(e))

    def _done(self, res):
        self.res = res
        self.show_result()

    def set_trim(self, v):
        self.trim = int(v)
        if self.res:
            d = self.app.calib
            self.res.crop = cal.valid_crop(np.array(self.res.camera_matrix),
                                           np.array(self.res.dist), d["size"], self.trim)
            self.show_result(recompute=False)

    def show_result(self, recompute=True):
        r, d = self.res, self.app.calib
        q = "good" if r.rms < cal.RMS_GOOD else "ok" if r.rms < cal.RMS_OK else "poor"
        word = {"good": ("GOOD", GREEN), "ok": ("ACCEPTABLE", AMBER), "poor": ("TOO HIGH", RED)}[q]
        self.verdict.config(text=f"Calibration error {r.rms:.2f} px: {word[0]}", fg=word[1])
        lines = [f"Straight lines: bend {r.bend_before_px:.1f} px before correction, "
                 f"{r.bend_after_px:.2f} px after.   Used {r.n_used} photos."]
        if q == "poor":
            lines.append("Too much error. Re-take photos with the checkerboard flat and sharp.")
        lines += r.notes
        self.detail.config(text="\n".join(lines))
        self.save_btn.set_enabled(q != "poor")
        try:
            raw = cv2.imread(str(d["files"][-1]))
            K, D = np.array(r.camera_matrix), np.array(r.dist)
            h, w = raw.shape[:2]
            newK, _ = cv2.getOptimalNewCameraMatrix(K, D, (w, h), 1)
            und = cv2.undistort(raw, K, D, None, newK)
            x, y, cw, ch = r.crop
            cv2.rectangle(und, (x, y), (x + cw, y + ch), (80, 220, 100), max(2, w // 400))
            both = np.hstack([cv2.resize(raw, (w // 2, h // 2)), cv2.resize(und, (w // 2, h // 2))])
            self.draw(self.canvas, both, is_bgr=True)
            self.canvas.create_text(10, 8, anchor="nw", fill=FG, text="before",
                                    font=self.app.f(10, True))
            self.canvas.create_text(self.canvas.winfo_width() // 2 + 10, 8, anchor="nw",
                                    fill=FG, text="after (green = area used)",
                                    font=self.app.f(10, True))
        except Exception:
            pass

    def more(self):
        self.app.calib["resume"] = True
        self.app.show("CalibCaptureScreen")

    def redo(self):
        import shutil
        shutil.rmtree(cal.images_dir(self.app.calib.get("id", "x")), ignore_errors=True)
        self.app.show("CalibCaptureScreen")

    def discard(self):
        import shutil
        shutil.rmtree(cal.images_dir(self.app.calib.get("id", "x")), ignore_errors=True)
        cal.apply_active()
        self.app.restart_camera(done=lambda: None)
        self.app.show("CameraScreen")

    def save(self):
        d, r = self.app.calib, self.res
        p = cal.profile_from(r, d["name"], d["res"], d["lens"], d["distance"], d["board"])
        p.id = d["id"]
        cal.save_profile(p)
        cal.set_active(p.id)
        cal.apply(p)
        self.verdict.config(text="Saved. Switching the camera to the new profile...", fg=AMBER)
        self.app.restart_camera(done=lambda: self.app.show("CalibReadyScreen"))


# ---------------------------------------------------------------------------
# Ready check
# ---------------------------------------------------------------------------

class CalibReadyScreen(Screen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        c = app.compact
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = self.photo_canvas(self)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        side = tk.Frame(self, bg=PANEL, width=int(300 * app.k))
        side.grid(row=0, column=1, sticky="ns", padx=(4, 8), pady=8)
        side.grid_propagate(False)
        self.title = tk.Label(side, text="Ready check", bg=PANEL, fg=FG, font=app.f(15, True))
        self.title.pack(pady=(10, 2))
        self.banner = tk.Label(side, text="", bg=PANEL, fg=AMBER, font=app.f(13, True),
                               wraplength=int(280 * app.k))
        self.banner.pack(pady=2)
        self.list = tk.Frame(side, bg=PANEL)
        self.list.pack(fill="x", padx=8)
        tk.Label(side, text="Tip: scan a part of known size (a drill bit or gauge) in a few spots "
                            "to confirm the sizes.", bg=PANEL, fg=MUTED, font=app.f(9),
                 wraplength=int(280 * app.k), justify="left").pack(padx=8, pady=6, side="bottom")
        row = tk.Frame(side, bg=PANEL)
        row.pack(side="bottom", pady=4)
        self.done_btn = Btn(row, "Finish", self.finish, GREEN, DARK_GREEN_TEXT, app.f(12, True),
                            padx=16, pady=5)
        self.done_btn.pack(side="left", padx=3)
        Btn(row, "Back", lambda: app.show("CameraScreen"), PANEL2, MUTED, app.f(11), padx=12,
            pady=5).pack(side="left", padx=3)
        self._stop = threading.Event()
        self._result = None
        self._lock = threading.Lock()
        self._tick_id = None
        self._shown = None

    def on_show(self):
        self._stop.set()
        self._stop = threading.Event()
        self._result, self._shown = None, None
        self.profile = cal.active()
        self.banner.config(text="Looking at the board...", fg=AMBER)
        threading.Thread(target=self._work, args=(self._stop,), daemon=True).start()
        self.tick()

    def on_hide(self):
        self._stop.set()
        if self._tick_id:
            self.after_cancel(self._tick_id)
            self._tick_id = None

    def _work(self, stop):
        while not stop.is_set():
            f = self.app.feed.latest()
            if f is not None:
                try:
                    prepared = scan_service.prepare(f)
                    checks = cal.ready_checks(self.profile, prepared, practice=isinstance(
                        self.app.feed.camera, (cam_mod.DemoCamera, cam_mod.CalibDemoCamera)))
                    img = prepared.copy()
                    import markers as mk
                    for i, pts in mk.detect_all(prepared).items():
                        cv2.polylines(img, [pts.astype(np.int32)], True, (255, 160, 0),
                                      max(2, img.shape[1] // 600), cv2.LINE_AA)
                    k = min(1.0, 900 / img.shape[1])
                    img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
                    with self._lock:
                        self._result = (img, checks)
                except Exception as e:     # keep the screen alive; show what went wrong
                    with self._lock:
                        self._result = (None, [cal.Check(False, f"Could not read the camera: {e}")])
            time.sleep(0.6)

    def tick(self):
        self._tick_id = self.after(300, self.tick)
        with self._lock:
            res = self._result
        if res is None or res is self._shown:
            return
        self._shown = res
        img, checks = res
        if img is not None:
            self.draw(self.canvas, img, is_bgr=True)
        for w in self.list.winfo_children():
            w.destroy()
        for ch in checks:
            tk.Label(self.list, text=("OK  " if ch.ok else "!!  ") + ch.text, bg=PANEL,
                     fg=GREEN if ch.ok else AMBER, font=self.app.f(10), anchor="w",
                     justify="left", wraplength=int(280 * self.app.k)).pack(fill="x", pady=2)
        ok = all(c.ok for c in checks)
        self.ready = ok
        self.banner.config(text="READY. You can start scanning." if ok else "Not ready yet",
                           fg=GREEN if ok else AMBER)

    def finish(self):
        if getattr(self, "ready", False):
            cal.mark_verified(self.profile.id)
        self.app.show("HomeScreen")
