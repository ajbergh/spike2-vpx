"""Launch the standalone SPIKE 2 VPX Setup window."""

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from spike2_vpx_setup import ROWS, VPX_RELEASE, Setup


class SetupWindow:
    def __init__(self, root, setup):
        self.root = root
        self.setup = setup
        self.results = queue.Queue()
        self.busy = False
        self.checks = {}
        root.title("SPIKE 2 VPX")
        root.minsize(700, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)

        page = ttk.Frame(root, padding=24)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="SPIKE 2 VPX", font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(page, text="Set up → Prepare image → Prepare table → Play").pack(anchor="w", pady=(2, 18))

        checklist = ttk.LabelFrame(page, text="Setup checklist", padding=(12, 4, 12, 10))
        checklist.pack(fill="both", expand=True)
        checklist.columnconfigure(0, weight=1)
        self.labels = {}
        for index, (key, name) in enumerate(ROWS.items()):
            row = index * 3
            ttk.Label(checklist, text=name, font=("Segoe UI", 10, "bold")).grid(
                row=row, column=0, sticky="w", pady=(8, 0))
            status = ttk.Label(checklist, text="Checking…")
            status.grid(row=row, column=1, sticky="e", pady=(8, 0))
            detail = ttk.Label(checklist, wraplength=600, justify="left")
            detail.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(2, 5))
            self.labels[key] = status, detail
            if index < len(ROWS) - 1:
                ttk.Separator(checklist).grid(row=row + 2, column=0, columnspan=2, sticky="ew")
        checklist.bind("<Configure>", self.wrap_details)

        self.summary = ttk.Label(page, wraplength=650)
        self.summary.pack(anchor="w", pady=(12, 8))
        actions = ttk.Frame(page)
        actions.pack(fill="x")
        self.primary = ttk.Button(actions, text="Set up", command=self.repair)
        self.primary.pack(side="left")
        self.refresh = ttk.Button(actions, text="Check again", command=lambda: self.start(setup.checks))
        self.refresh.pack(side="left", padx=(8, 16))
        self.browse = ttk.Button(actions, text="Choose VPX…", command=self.choose_vpx)
        self.browse.pack(side="left")
        ttk.Button(actions, text="Get VPX", command=lambda: webbrowser.open(VPX_RELEASE)).pack(
            side="left", padx=8)
        ttk.Button(actions, text="Setup logs", command=self.open_logs).pack(side="right")
        self.start(setup.checks)

    def wrap_details(self, event):
        for _, detail in self.labels.values():
            detail.configure(wraplength=max(240, event.width - 28))

    def open_logs(self):
        try:
            os.startfile(self.setup.log_path.parent)
        except OSError as exc:
            messagebox.showerror("Setup logs", f"{exc}\n\n{self.setup.log_path.parent}", parent=self.root)

    def close(self):
        if self.busy:
            messagebox.showinfo("Setup is running", "Wait for the current setup step to finish.",
                                parent=self.root)
            return
        self.root.destroy()

    def start(self, work, message="Checking this PC…"):
        self.busy = True
        self.summary.configure(text=message)
        for button in (self.primary, self.refresh, self.browse):
            button.state(["disabled"])

        def worker():
            try:
                self.results.put((work(), ""))
            except Exception as exc:
                self.setup.log.exception("Setup could not finish")
                self.results.put((None, f"{exc} See the setup log."))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.finish)

    def finish(self):
        try:
            checks, error = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.finish)
            return
        self.busy = False
        for button in (self.refresh, self.browse):
            button.state(["!disabled"])
        if error:
            self.summary.configure(text=error)
            if self.next_step(self.checks):
                self.primary.state(["!disabled"])
            return

        self.checks = checks
        for key, check in checks.items():
            status, detail = self.labels[key]
            status.configure(text=check.state)
            detail.configure(text=check.detail)
        states = {check.state for check in checks.values()}
        if "Restart required" in states:
            summary = "Restart Windows, then reopen SPIKE 2 VPX to resume setup."
        elif states == {"Ready"}:
            summary = "Setup checks passed. Prepare an image and table to verify play."
        elif states & {"Setup failed", "Check failed"}:
            summary = "Review the setup logs, then retry or check again."
        else:
            summary = "Follow the instructions beside each item that needs attention."
        self.summary.configure(text=summary)
        action = self.next_step(checks)
        self.primary.configure(text="Retry setup" if "Setup failed" in states else
                               "Repair" if "Repair needed" in states else "Set up")
        if action:
            self.primary.state(["!disabled"])

    @staticmethod
    def next_step(checks):
        if any(check.state == "Restart required" for check in checks.values()):
            return None
        # Retry a failed action before moving to another component.
        for key, check in checks.items():
            if check.state == "Setup failed" and check.action:
                return key, check.action
        return next(((key, check.action) for key, check in checks.items() if check.action), None)

    def repair(self):
        def work():
            checks = self.setup.checks()
            while (step := self.next_step(checks)):
                key, action = step
                checks = self.setup.repair(key, action)
                if checks[key].state == "Setup failed":
                    break
            return checks

        self.start(work, "Setting up this PC… Windows may ask for administrator permission.")

    def choose_vpx(self):
        exe = filedialog.askopenfilename(parent=self.root, title="Choose Visual Pinball X",
                                         filetypes=[("Visual Pinball X", "VPinballX64.exe"),
                                                    ("Executables", "*.exe")])
        if not exe:
            return
        try:
            self.setup.choose_vpx(exe)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Choose Visual Pinball X", str(exc), parent=self.root)
            return
        self.start(self.setup.checks)


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        setup = Setup()
        # A second setup window must not share an active download or import.
        import msvcrt

        with (Path(setup.data) / "setup.lock").open("a+b") as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                messagebox.showinfo("SPIKE 2 VPX", "Setup is already open.", parent=root)
                return
            SetupWindow(root, setup)
            root.deiconify()
            root.mainloop()
    except OSError as exc:
        messagebox.showerror("SPIKE 2 VPX", f"Setup could not open.\n\n{exc}", parent=root)


if __name__ == "__main__":
    main()
