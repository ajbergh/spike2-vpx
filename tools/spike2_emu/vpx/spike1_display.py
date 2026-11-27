"""Show the SPIKE 1 DMD on the player's assigned Stern display."""

from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "spike1_emu"))
from s1dmd import HEIGHT, WIDTH, decode_frame, latest_frame


def show(capture, bounds):
    root = tk.Tk()
    root.title("Stern SPIKE 1 DMD")
    root.configure(bg="black")
    scale = 5
    if bounds:
        x, y, right, bottom = bounds
        width, height = right - x, bottom - y
        scale = max(1, min(width // WIDTH, height // HEIGHT))
        root.overrideredirect(True)
        # Tk treats negative offsets as relative to the primary screen's edge.
        root.geometry(f"{width}x{height}")
        if sys.platform == "win32":
            import ctypes

            root.update_idletasks()
            user32 = ctypes.windll.user32
            user32.GetParent.restype = ctypes.c_void_p
            hwnd = user32.GetParent(ctypes.c_void_p(root.winfo_id())) or root.winfo_id()
            user32.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, width, height, 0x14)
        else:
            root.geometry(f"{width}x{height}{x:+}{y:+}")
    picture = tk.Label(root, bg="black", borderwidth=0)
    picture.pack(expand=True)
    palette = [bytes((level * 17, level * 9, 0)) for level in range(16)]
    previous = None

    def tick():
        nonlocal previous
        try:
            frame = latest_frame(capture)
        except OSError:
            frame = None
        if frame is not None and frame != previous:
            pixels = b"".join(palette[value] for row in decode_frame(frame) for value in row)
            ppm = f"P6\n{WIDTH} {HEIGHT}\n255\n".encode("ascii") + pixels
            picture.image = tk.PhotoImage(data=ppm, format="PPM").zoom(scale)
            picture.configure(image=picture.image)
            previous = frame
        root.after(50, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    show(sys.argv[1], tuple(map(int, sys.argv[2:])))
