# ui.py
# Shared look-and-feel and small reusable pieces for the scanner app:
# colours, buttons, +/- steppers, a question dialog and a folder picker.

from __future__ import annotations

import base64
import os
import tkinter as tk
from pathlib import Path

import cv2
import numpy as np

import storage

BG, PANEL, PANEL2 = "#12161b", "#1c222a", "#252d37"
FG, MUTED = "#e8edf2", "#8d99a8"
GREEN, AMBER, RED, BLUE = "#2ecc71", "#f5a623", "#e5534b", "#3b82f6"
DARK_GREEN_TEXT = "#06210f"


class Btn(tk.Label):
    """A flat, touch-friendly button (tk.Button ignores colours on macOS)."""

    def __init__(self, parent, text, command, color=PANEL2, fg=FG, font=None,
                 padx=18, pady=10, **kw):
        super().__init__(parent, text=text, bg=color, fg=fg, font=font,
                         padx=padx, pady=pady, cursor="hand2", **kw)
        self._color, self._fg, self._command, self._enabled = color, fg, command, True
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
        self.config(bg=self._color if on else PANEL, fg=self._fg if on else MUTED)

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


def png_photo(img: np.ndarray, is_bgr: bool = False) -> tk.PhotoImage:
    """Numpy image -> Tk PhotoImage via PNG (no PIL.ImageTk dependency)."""
    bgr = img if is_bgr else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()))


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _modal(app, top: tk.Toplevel, width: int, height: int) -> None:
    """Centre a dialog over the app window and make it modal."""
    top.transient(app)
    app.update_idletasks()
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    top.update_idletasks()
    if height is None:                      # size to the content
        height = top.winfo_reqheight() + 8
    width, height = min(width, sw - 20), min(height, sh - 40)
    x = max(0, app.winfo_rootx() + (app.winfo_width() - width) // 2)
    y = max(0, app.winfo_rooty() + (app.winfo_height() - height) // 2)
    top.geometry(f"{width}x{height}+{x}+{y}")
    try:
        top.wait_visibility()
        top.grab_set()
    except tk.TclError:
        pass
    top.focus_set()


def ask(app, title: str, message: str, buttons: list[tuple], cancel_value=None,
        left: bool = False):
    """Show a modal question and return the value of the button pressed.

    buttons: list of (label, value, background colour, text colour)
    Closing the window returns cancel_value. left=True left-aligns the text
    (for numbered steps).
    """
    top = tk.Toplevel(app, bg=PANEL)
    top.title(title)
    result = {"v": cancel_value}

    def choose(v):
        result["v"] = v
        top.destroy()

    top.protocol("WM_DELETE_WINDOW", lambda: choose(cancel_value))
    tk.Label(top, text=title, bg=PANEL, fg=FG, font=app.f(16, True)
             ).pack(padx=24, pady=(20, 6))
    tk.Label(top, text=message, bg=PANEL, fg=MUTED, font=app.f(12),
             justify="left" if left else "center",
             wraplength=int(440 * app.k)).pack(padx=24, pady=(0, 14))
    for label, value, color, fg in buttons:
        Btn(top, label, (lambda v=value: choose(v)), color, fg, app.f(13, True),
            pady=8 if app.compact else 12).pack(fill="x", padx=28, pady=4)
    tk.Frame(top, bg=PANEL, height=14).pack()
    _modal(app, top, int(520 * app.k), None)
    app.wait_window(top)
    return result["v"]


class FolderPicker:
    """Pick (or create) a folder. Raspberry Pi's built-in Tk dialog cannot make
    new folders, so this one can.

        path = FolderPicker(app, start_dir).show()   # Path, or None if cancelled
    """

    def __init__(self, app, start: Path, title: str = "Choose a folder"):
        self.app = app
        p = Path(start).expanduser()
        while not p.exists() and p != p.parent:     # climb to something that exists
            p = p.parent
        self.cwd = p
        self.title = title
        self.result: Path | None = None

    def show(self) -> Path | None:
        app = self.app
        top = self.top = tk.Toplevel(app, bg=PANEL)
        top.title(self.title)
        top.protocol("WM_DELETE_WINDOW", top.destroy)
        c = app.compact

        tk.Label(top, text=self.title, bg=PANEL, fg=FG, font=app.f(14, True)
                 ).pack(pady=(10 if c else 16, 2))
        self.path_lbl = tk.Label(top, text="", bg=PANEL, fg=GREEN, font=app.f(11),
                                 wraplength=int(560 * app.k))
        self.path_lbl.pack(padx=14)

        quick = tk.Frame(top, bg=PANEL)
        quick.pack(fill="x", padx=14, pady=(6, 4))
        places = [("Home", Path.home()), ("Desktop", Path.home() / "Desktop"),
                  ("Documents", Path.home() / "Documents")]
        places += [(p.name[:12], p) for p in storage.find_usb_drives()[:2]]
        for label, path in places:
            if path.exists():
                Btn(quick, label, (lambda q=path: self.go(q)), PANEL2, FG, app.f(10),
                    padx=10, pady=3).pack(side="left", padx=(0, 6))

        # bottom controls are packed before the list so they never get squeezed out
        bottom = tk.Frame(top, bg=PANEL)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(4, 12))
        Btn(bottom, "Cancel", top.destroy, PANEL2, FG, app.f(12), pady=6
            ).pack(side="left")
        Btn(bottom, "Use this folder", self.choose, GREEN, DARK_GREEN_TEXT,
            app.f(13, True), pady=8, padx=24).pack(side="right")

        newrow = tk.Frame(top, bg=PANEL)
        newrow.pack(side="bottom", fill="x", padx=14, pady=4)
        tk.Label(newrow, text="New folder:", bg=PANEL, fg=MUTED, font=app.f(10)
                 ).pack(side="left")
        self.new_var = tk.StringVar()
        ent = tk.Entry(newrow, textvariable=self.new_var, font=app.f(12), bg=PANEL2, fg=FG,
                       insertbackground=FG, relief="flat")
        ent.pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        ent.bind("<Return>", lambda e: self.create())
        Btn(newrow, "Create", self.create, BLUE, "#ffffff", app.f(11, True),
            padx=12, pady=3).pack(side="right")

        nav = tk.Frame(top, bg=PANEL)
        nav.pack(side="bottom", fill="x", padx=14, pady=2)
        Btn(nav, "Up", self.up, PANEL2, FG, app.f(11), padx=14, pady=3).pack(side="left")
        Btn(nav, "Open selected", self.open_selected, PANEL2, FG, app.f(11),
            padx=14, pady=3).pack(side="left", padx=6)

        listframe = tk.Frame(top, bg=PANEL)
        listframe.pack(fill="both", expand=True, padx=14, pady=4)
        sb = tk.Scrollbar(listframe, width=int(18 * app.k))
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(listframe, font=app.f(13), bg="#0b0e12", fg=FG,
                                  selectbackground=BLUE, activestyle="none",
                                  highlightthickness=0, relief="flat",
                                  yscrollcommand=sb.set, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_selected())

        self.refresh()
        _modal(app, top, int(640 * app.k), int(520 * app.k))
        app.wait_window(top)
        return self.result

    # --- navigation ------------------------------------------------------

    def refresh(self):
        self.path_lbl.config(text=storage.shorten(self.cwd, 70))
        self.listbox.delete(0, "end")
        self.subdirs: list[Path] = []
        try:
            for p in sorted(self.cwd.iterdir(), key=lambda x: x.name.lower()):
                if p.is_dir() and not p.name.startswith("."):
                    self.subdirs.append(p)
                    self.listbox.insert("end", "  " + p.name)
        except OSError:
            self.listbox.insert("end", "  (cannot open this folder)")
        if not self.subdirs:
            self.listbox.insert("end", "  (no folders here yet; make one below)")

    def go(self, path: Path):
        if path.is_dir():
            self.cwd = path
            self.refresh()

    def up(self):
        self.go(self.cwd.parent)

    def open_selected(self):
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.subdirs):
            self.go(self.subdirs[sel[0]])

    def create(self):
        name = storage.folder_name(self.new_var.get(), fallback="")
        if not name:
            return
        target = self.cwd / name
        try:
            target.mkdir(exist_ok=True)
        except OSError as e:
            self.path_lbl.config(text=f"Could not create folder: {e}", fg=RED)
            return
        self.new_var.set("")
        self.path_lbl.config(fg=GREEN)
        self.go(target)        # step into the new folder, ready to "Use this folder"

    def choose(self):
        if not os.access(self.cwd, os.W_OK):
            self.path_lbl.config(text="You can't save here (no permission). Pick another folder.",
                                 fg=RED)
            return
        self.result = self.cwd
        self.top.destroy()


class Screen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._flash_id = None
        self.toast = tk.Label(self, text="", bg=BG, fg=FG, font=app.f(12, True))

    def on_show(self): ...
    def on_hide(self): ...
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
