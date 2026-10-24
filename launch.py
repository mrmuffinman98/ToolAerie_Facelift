# launch.py
# Entry point used by the desktop icon. Starts app.py, and if anything goes
# wrong before the window appears (missing package, no camera library, ...)
# shows the problem in a message box instead of failing silently.

import sys
import traceback


def _show_error(text: str) -> None:
    try:
        import storage
        storage.log_error(text)
        where = f"\n\nDetails were saved to:\n{storage.log_path()}"
    except Exception:
        where = ""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Tool Aerie could not start", text.strip()[-1200:] + where)
        root.destroy()
    except Exception:
        print(text, file=sys.stderr)


if __name__ == "__main__":
    try:
        import app
        app.main()
    except SystemExit:
        raise
    except BaseException:
        _show_error(traceback.format_exc())
        sys.exit(1)
