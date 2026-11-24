"""Place the WSLg LCD on its assigned Windows display."""

import ctypes
from pathlib import Path
import sys
import threading

import padcontroller
import padisplay
import padlaunch


GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_FRAME = 0x00C00000 | 0x00040000  # WS_CAPTION | WS_THICKFRAME
WS_EX_TOPMOST = 0x00000008
SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_ASYNCWINDOWPOS = 0x0010, 0x0020, 0x4000


def fullscreen_target(board=None):
    if sys.platform != "win32":
        return None
    setting = padisplay.load(Path(board or padlaunch.BOARD) / "display.conf")
    if setting["mode"] != "fullscreen":
        return None
    monitor = next((screen for screen in padisplay.monitors() if screen["n"] == setting["monitor"]), None)
    if monitor:
        x, y, width, height = (monitor[key] for key in ("x", "y", "w", "h"))
        return x, y, x + width, y + height
    return None


def frame_insets(path):
    try:
        with path.open(encoding="ascii") as file:
            insets = tuple(int(value) for value in file.read(80).split())
        if len(insets) == 4 and all(0 <= value <= 256 for value in insets):
            return insets
    except (OSError, ValueError):
        pass
    return None


def outer_bounds(target, insets):
    x, y, right, bottom = target
    left, top, inset_right, inset_bottom = insets
    return x - left, y - top, right + inset_right, bottom + inset_bottom


class Placement:
    def __init__(self, launch, target):
        self.launch = launch
        self.target = target
        self.launch.note(f"[display] Assigning the Stern LCD to Windows bounds {self.target}.")
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.watch, name="Stern LCD placement", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)

    def watch(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        context = user32.SetThreadDpiAwarenessContext
        context.restype = ctypes.c_void_p
        old = context(ctypes.c_void_p(-4))  # Per-monitor v2, matching padisplay's physical bounds.
        prefix = f"[rig {self.launch.slot}: {self.launch.label}] "
        frame_file = self.launch.lcd_frame_path()
        confirmed = None
        reported_error = False
        try:
            while not self.stop.is_set():
                hwnd = padcontroller.find_kit_window(" - Stern Spike 2 emulator", title_prefix=prefix)
                try:
                    if hwnd and not user32.IsIconic(ctypes.c_void_p(hwnd)):
                        insets = frame_insets(frame_file)
                        bounds = outer_bounds(self.target, insets) if insets is not None else self.target
                        if self.place(user32, hwnd, bounds) and insets is not None and confirmed != (hwnd, insets):
                            self.launch.note(f"[display] Stern LCD content fits {self.target}, "
                                             f"WSLg frame insets {insets}.")
                            confirmed = hwnd, insets
                except OSError as exc:
                    if not reported_error:
                        self.launch.note(f"[display] Windows could not position the Stern LCD yet: {exc}")
                        reported_error = True
                self.stop.wait(0.5)
        finally:
            if old:
                context(ctypes.c_void_p(old))

    def place(self, user32, hwnd, bounds):
        window = ctypes.c_void_p(hwnd)
        x, y, right, bottom = bounds
        width, height = right - x, bottom - y
        style = user32.GetWindowLongW(window, GWL_STYLE)
        topmost = user32.GetWindowLongW(window, GWL_EXSTYLE) & WS_EX_TOPMOST
        if padcontroller.window_rect(hwnd) == (x, y, width, height) and not style & WS_FRAME and topmost:
            return True
        # A repeated RAIL placement or a replacement HWND can undo the first move.
        flags = SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        frame_error = None
        if style & WS_FRAME:
            if user32.SetWindowLongW(window, GWL_STYLE, style & ~WS_FRAME):
                flags |= SWP_FRAMECHANGED
            else:
                frame_error = ctypes.WinError(ctypes.get_last_error())
        if not user32.SetWindowPos(window, ctypes.c_void_p(-1), x, y, width, height, flags):
            raise ctypes.WinError(ctypes.get_last_error())
        if frame_error:
            raise frame_error
        return False
