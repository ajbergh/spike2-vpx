"""SPIKE 2 VPX desktop player."""

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from spike2_vpx.player import Player, folder_images, supported_tables
from spike2_vpx_setup import ROWS, VPX_RELEASE, Setup
import padisplay
import padlaunch
import play_spike2


class PlayerWindow:
    def __init__(self, root, setup):
        self.root = root
        self.setup = setup
        self.player = Player(setup)
        self.results = queue.Queue()
        self.busy = False
        self.playing = False
        self.checks = {}
        self.game = None
        self.cache_note = ""
        self.first_check = True
        root.title("SPIKE 2 VPX")
        root.geometry("940x680")
        root.minsize(820, 600)
        root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(root)
        style.configure("Heading.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Hint.TLabel", foreground="#40556A")
        page = ttk.Frame(root, padding=(24, 18))
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="SPIKE 2 VPX", style="Heading.TLabel").pack(anchor="w", pady=(0, 14))
        self.tabs = ttk.Notebook(page)
        self.tabs.pack(fill="both", expand=True)
        self.games_page = ttk.Frame(self.tabs, padding=18)
        self.tables_page = ttk.Frame(self.tabs, padding=18)
        self.setup_page = ttk.Frame(self.tabs, padding=18)
        self.settings_page = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.games_page, text="Games")
        self.tabs.add(self.tables_page, text="Supported Tables")
        self.tabs.add(self.setup_page, text="Setup")
        self.tabs.add(self.settings_page, text="Settings")

        self.build_games()
        self.build_supported_tables()
        self.build_setup()
        self.build_settings()

        status = ttk.Frame(page)
        status.pack(fill="x", pady=(12, 0))
        self.summary = ttk.Label(status, wraplength=650, justify="left")
        self.summary.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=110)
        self.progress.pack(side="right", padx=(12, 0))
        self.tabs.select(self.setup_page)
        self.reload_library()
        self.check_again()

    def build_games(self):
        page = self.games_page
        library = ttk.Frame(page, width=250)
        library.pack(side="left", fill="y", padx=(0, 20))
        ttk.Label(library, text="Game library", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        shelf = ttk.Frame(library)
        shelf.pack(fill="both", expand=True)
        self.listing = ttk.Treeview(shelf, columns=("image",), selectmode="browse", height=10)
        self.listing.heading("#0", text="Title")
        self.listing.heading("image", text="Image")
        self.listing.column("#0", width=120, stretch=False)
        self.listing.column("image", width=125, stretch=False)
        scroll = ttk.Scrollbar(shelf, orient="vertical", command=self.listing.yview)
        self.listing.configure(yscrollcommand=scroll.set)
        self.listing.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listing.bind("<<TreeviewSelect>>", self.select_game)
        self.library_hint = ttk.Label(library, style="Hint.TLabel", wraplength=230)
        self.library_hint.pack(anchor="w", pady=(6, 0))
        self.remove_button = ttk.Button(library, text="Remove pairing…", command=self.remove_game)
        self.remove_button.pack(anchor="w", pady=(10, 0))

        detail = ttk.Frame(page)
        detail.pack(side="left", fill="both", expand=True)
        self.game_title = ttk.Label(detail, style="Section.TLabel")
        self.game_title.pack(anchor="w", pady=(0, 12))
        image = ttk.LabelFrame(detail, text="Prepare image", padding=12)
        image.pack(fill="x", pady=(0, 10))
        self.image_text = ttk.Label(image, wraplength=440, justify="left")
        self.image_text.pack(anchor="w")
        images = ttk.Frame(image)
        images.pack(fill="x", pady=(8, 0))
        self.image_button = ttk.Button(images, text="Choose image…", command=self.choose_image)
        self.image_button.pack(side="left")
        self.folder_button = ttk.Button(images, text="Choose folder…", command=self.choose_folder)
        self.folder_button.pack(side="left", padx=(8, 0))

        table = ttk.LabelFrame(detail, text="Prepare table", padding=12)
        table.pack(fill="x", pady=(0, 10))
        self.table_text = ttk.Label(table, wraplength=440, justify="left")
        self.table_text.pack(anchor="w")
        self.table_button = ttk.Button(table, text="Choose table…", command=self.choose_table)
        self.table_button.pack(anchor="w", pady=(8, 0))

        self.play_button = ttk.Button(detail, text="Play", command=self.play)
        self.play_button.pack(anchor="w", pady=(4, 6))
        self.play_hint = ttk.Label(detail, style="Hint.TLabel", wraplength=440, justify="left")
        self.play_hint.pack(anchor="w")

    def build_supported_tables(self):
        page = self.tables_page
        ttk.Label(page, text="Supported SPIKE 2 games", style="Section.TLabel").pack(anchor="w")
        ttk.Label(page, text="Choose the VPX table and version listed for your game. "
                             "You supply the SD-card image and table.", wraplength=700).pack(
            anchor="w", pady=(4, 10))
        search = ttk.Frame(page)
        search.pack(fill="x", pady=(0, 8))
        self.table_filter = tk.StringVar()
        ttk.Label(search, text="Search games or tables").pack(side="left")
        ttk.Entry(search, textvariable=self.table_filter).pack(side="left", fill="x", expand=True,
                                                               padx=(10, 0))
        results = ttk.Frame(page)
        results.pack(fill="both", expand=True)
        self.catalog = ttk.Treeview(results, columns=("game", "table"), show="headings", selectmode="browse")
        self.catalog.heading("game", text="SPIKE 2 game")
        self.catalog.heading("table", text="Compatible VPX table")
        self.catalog.column("game", width=200, minwidth=140, stretch=False)
        self.catalog.column("table", width=550, minwidth=280)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.catalog.yview)
        self.catalog.configure(yscrollcommand=scroll.set)
        self.catalog.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        footer = ttk.Frame(page)
        footer.pack(fill="x", pady=(10, 0))
        self.catalog_detail = ttk.Label(footer, style="Hint.TLabel", wraplength=530)
        self.catalog_detail.pack(side="left", fill="x", expand=True)
        self.table_page_button = ttk.Button(footer, text="Open table page", command=self.open_table_page)
        self.table_page_button.pack(side="right", padx=(12, 0))
        self.table_rows = supported_tables()
        self.catalog.bind("<<TreeviewSelect>>", self.show_supported_table)
        self.table_filter.trace_add("write", self.filter_supported_tables)
        self.filter_supported_tables()

    def filter_supported_tables(self, *_):
        terms = self.table_filter.get().casefold().split()
        self.catalog.delete(*self.catalog.get_children())
        for index, row in enumerate(self.table_rows):
            description = f"{row.game} {row.table}".casefold()
            if all(term in description for term in terms):
                self.catalog.insert("", "end", iid=str(index), values=(row.game, row.table))
        self.show_supported_table()

    def show_supported_table(self, _event=None):
        selected = self.catalog.selection()
        row = self.table_rows[int(selected[0])] if selected else None
        if row:
            details = []
            if row.author:
                details.append(f"Table by {row.author}.")
            if not row.url:
                details.append("No table page is listed for this version.")
            self.catalog_detail.configure(text=" ".join(details))
        else:
            count = len(self.catalog.get_children())
            self.catalog_detail.configure(text=f"{count} compatible table entries." if count else
                                          "No compatible table entries match this search.")
        self.table_page_button.state(["!disabled"] if row and row.url else ["disabled"])

    def open_table_page(self):
        selected = self.catalog.selection()
        if selected and (url := self.table_rows[int(selected[0])].url):
            webbrowser.open(url)

    def build_setup(self):
        checklist = ttk.LabelFrame(self.setup_page, text="Setup checklist", padding=(12, 4, 12, 10))
        checklist.pack(fill="both", expand=True)
        checklist.columnconfigure(0, weight=1)
        self.labels = {}
        for index, (key, name) in enumerate(ROWS.items()):
            row = index * 3
            ttk.Label(checklist, text=name, style="Section.TLabel").grid(
                row=row, column=0, sticky="w", pady=(6, 0))
            status = ttk.Label(checklist, text="Checking…")
            status.grid(row=row, column=1, sticky="e", pady=(6, 0))
            detail = ttk.Label(checklist, wraplength=680, justify="left")
            detail.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(2, 4))
            self.labels[key] = status, detail
            if index < len(ROWS) - 1:
                ttk.Separator(checklist).grid(row=row + 2, column=0, columnspan=2, sticky="ew")
        checklist.bind("<Configure>", self.wrap_details)
        actions = ttk.Frame(self.setup_page)
        actions.pack(fill="x", pady=(10, 0))
        self.primary = ttk.Button(actions, text="Set up", command=self.repair)
        self.primary.pack(side="left")
        self.refresh = ttk.Button(actions, text="Check again", command=self.check_again)
        self.refresh.pack(side="left", padx=8)
        self.browse = ttk.Button(actions, text="Choose VPX…", command=self.choose_vpx)
        self.browse.pack(side="left")
        ttk.Button(actions, text="Get VPX", command=lambda: webbrowser.open(VPX_RELEASE)).pack(
            side="left", padx=8)
        ttk.Button(actions, text="Open logs", command=self.open_logs).pack(side="right")

    def build_settings(self):
        page = self.settings_page
        page.columnconfigure(1, weight=1)
        settings = self.setup.settings
        show_monitor = settings.get("lcd_monitor")
        monitors = padisplay.monitors()
        choices = {"Windowed · last position": 0}
        choices.update({f"Full screen · Monitor {m['n']} ({m['w']} × {m['h']})": m["n"] for m in monitors})
        self.monitor_choices = choices
        self.monitor = tk.StringVar(value=next((name for name, number in choices.items()
                                                if number == show_monitor), next(iter(choices))))
        volume = settings.get("volume", 100)
        self.volume = tk.IntVar(value=volume if isinstance(volume, int) and 0 <= volume <= 100 else 100)
        self.cache = tk.BooleanVar(value=padlaunch.cache_on())
        ttk.Label(page, text="LCD placement", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=10)
        ttk.Combobox(page, textvariable=self.monitor, values=list(choices),
                     state="readonly", width=40).grid(row=0, column=1, sticky="w", padx=16)
        ttk.Label(page, text="Game volume", style="Section.TLabel").grid(row=1, column=0, sticky="w", pady=10)
        ttk.Spinbox(page, textvariable=self.volume, from_=0, to=100, width=6).grid(
            row=1, column=1, sticky="w", padx=16)
        ttk.Label(page, text="SD-card cache", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=10)
        ttk.Checkbutton(page, variable=self.cache, text="Cache images in the WSL runtime").grid(
            row=2, column=1, sticky="w", padx=16)
        self.save_options = ttk.Button(page, text="Save settings", command=self.save_settings)
        self.save_options.grid(row=3, column=0, sticky="w", pady=(12, 20))
        ttk.Separator(page).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Button(page, text="VPX and runtime setup…",
                   command=lambda: self.tabs.select(self.setup_page)).grid(row=5, column=0, sticky="w")
        ttk.Button(page, text="Open logs", command=self.open_logs).grid(row=5, column=1, sticky="w", padx=16)

    def wrap_details(self, event):
        for _, detail in self.labels.values():
            detail.configure(wraplength=max(240, event.width - 28))

    def open_logs(self):
        try:
            os.startfile(self.setup.log_path.parent)
        except OSError as exc:
            messagebox.showerror("Logs", str(exc), parent=self.root)

    def close(self):
        if self.busy:
            text = "Close Visual Pinball X to finish the game." if self.playing else "Wait for the current step to finish."
            messagebox.showinfo("SPIKE 2 VPX", text, parent=self.root)
            return
        self.root.destroy()

    def ready(self, *keys):
        return all(key in self.checks and self.checks[key].state == "Ready" for key in keys)

    def update_buttons(self):
        image = self.ready("wsl", "runtime")
        states = (
            (self.image_button, image), (self.folder_button, image),
            (self.table_button, self.game is not None),
            (self.remove_button, self.game is not None and self.game in self.saved),
            (self.play_button, self.game is not None and bool(self.game.table) and self.ready(*ROWS)),
            (self.primary, self.next_step(self.checks)), (self.refresh, True),
            (self.browse, True), (self.save_options, True),
        )
        for button, enabled in states:
            button.state(["!disabled"] if enabled and not self.busy else ["disabled"])
        if not image:
            hint = "Complete WSL and runtime setup to inspect an image."
        elif not self.game:
            hint = "Choose an SD-card image to identify its game."
        elif not self.game.table:
            hint = "Choose a compatible original or prepared VPX table."
        elif not self.ready(*ROWS):
            hint = "Complete the remaining checks in Setup before playing."
        else:
            hint = "The game will start first. Close VPX to end the run."
        self.play_hint.configure(text=hint)

    def reload_library(self, select=None):
        self.saved = self.player.library()
        self.listing.delete(*self.listing.get_children())
        for index, game in enumerate(self.saved):
            key = str(index)
            self.listing.insert("", "end", iid=key, text=game.name, values=(Path(game.image).name,))
            if select is not None and game == select:
                self.listing.selection_set(key)
        self.library_hint.configure(text="" if self.saved else "Prepared pairings appear here.")
        self.show_game(select)

    def select_game(self, _event):
        chosen = self.listing.selection()
        if chosen and not self.busy:
            game = self.saved[int(chosen[0])]
            if game != self.game:
                self.cache_note = ""
                self.show_game(game)

    def remove_game(self):
        if not messagebox.askyesno("Remove pairing", f"Remove {self.game.name} from the library? "
                                   "Its image and table files will stay in place.", parent=self.root):
            return
        try:
            self.player.remove(self.game)
        except OSError as exc:
            messagebox.showerror("Remove pairing", str(exc), parent=self.root)
            return
        self.cache_note = ""
        self.reload_library()
        self.summary.configure(text="Pairing removed.")

    def show_game(self, game):
        self.game = game
        self.game_title.configure(text=game.name if game else "Add a game")
        image = game.image if game else "Choose a SPIKE 2 .raw file or its folder."
        if game and self.cache_note:
            image += "\n" + self.cache_note
        self.image_text.configure(text=image)
        self.table_text.configure(text=game.table if game and game.table else
                                  "Use a compatible .vpx table or an existing (PAD) copy.")
        self.update_buttons()

    def start(self, work, done, message):
        if self.busy:
            return
        self.busy = True
        self.summary.configure(text=message)
        self.progress.start(12)
        self.update_buttons()

        def worker():
            try:
                self.results.put(("done", work(), done))
            except Exception as exc:
                self.setup.log.exception("Desktop operation failed")
                self.results.put(("error", str(exc), None))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(80, self.finish)

    def report(self, message, playing):
        self.results.put(("progress", message, playing))

    def finish(self):
        while True:
            try:
                kind, value, extra = self.results.get_nowait()
            except queue.Empty:
                self.root.after(80, self.finish)
                return
            if kind == "progress":
                self.summary.configure(text=value)
                if extra:
                    self.playing = True
                    self.root.withdraw()
                continue
            self.busy = False
            self.progress.stop()
            if self.playing:
                self.playing = False
                self.root.deiconify()
            if kind == "done":
                extra(value)
            else:
                self.summary.configure(text=value)
                messagebox.showerror("SPIKE 2 VPX", value, parent=self.root)
            self.update_buttons()
            return

    def check_again(self):
        self.start(self.setup.checks, self.show_checks, "Checking this PC…")

    def show_checks(self, checks):
        self.checks = checks
        for key, check in checks.items():
            status, detail = self.labels[key]
            status.configure(text=check.state)
            detail.configure(text=check.detail)
        states = {check.state for check in checks.values()}
        if "Restart required" in states:
            summary = "Restart Windows, then reopen SPIKE 2 VPX to resume setup."
        elif self.ready(*ROWS):
            summary = "Setup complete."
            if self.first_check or self.tabs.select() == str(self.setup_page):
                self.tabs.select(self.games_page)
        elif states & {"Setup failed", "Check failed"}:
            summary = "Review the setup logs, then retry or check again."
        else:
            summary = "Follow the instructions beside each item that needs attention."
        self.first_check = False
        self.summary.configure(text=summary)
        self.primary.configure(text="Retry setup" if "Setup failed" in states else
                               "Repair" if "Repair needed" in states else "Set up")

    @staticmethod
    def next_step(checks):
        if any(check.state == "Restart required" for check in checks.values()):
            return None
        for key, check in checks.items():
            if check.state == "Setup failed" and check.action:
                return key, check.action
        return next(((key, check.action) for key, check in checks.items() if check.action), None)

    def repair(self):
        def work():
            checks = self.setup.checks()
            while (step := self.next_step(checks)):
                key, action = step
                self.report(f"Setting up {ROWS[key]}…", False)
                checks = self.setup.repair(key, action)
                if checks[key].state == "Setup failed":
                    break
            return checks

        self.start(work, self.show_checks, "Setting up this PC… Windows may ask for administrator permission.")

    def choose_vpx(self):
        exe = filedialog.askopenfilename(parent=self.root, title="Choose Visual Pinball X",
                                         filetypes=[("Visual Pinball X", "VPinballX64.exe"),
                                                    ("Executables", "*.exe")])
        if not exe:
            return

        def work():
            self.setup.choose_vpx(exe)
            return self.setup.checks()

        self.start(work, self.show_checks, "Checking the selected VPX installation…")

    def choose_image(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a SPIKE 2 image",
                                          filetypes=[("SPIKE 2 SD-card image", "*.raw"), ("All files", "*.*")])
        if path:
            self.load_image(path)

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Choose an SD-card image folder")
        if not folder:
            return
        try:
            candidates = folder_images(folder)
        except OSError as exc:
            messagebox.showerror("Choose folder", str(exc), parent=self.root)
            return
        if not candidates:
            messagebox.showinfo("Choose folder", "This folder contains no .raw images.", parent=self.root)
            return
        if len(candidates) == 1:
            self.load_image(candidates[0])
            return
        if self.game:
            try:
                match = play_spike2.card_in_folder(Path(folder), self.game.title)
            except ValueError:
                pass
            else:
                self.load_image(match)
                return
        self.pick_candidate(candidates)

    def pick_candidate(self, candidates):
        dialog = tk.Toplevel(self.root)
        dialog.title("Choose an image")
        dialog.geometry("580x320")
        dialog.transient(self.root)
        pane = ttk.Frame(dialog, padding=12)
        pane.pack(fill="both", expand=True)
        ttk.Label(pane, text="This folder has several .raw images. Choose one to inspect.").pack(anchor="w")
        choices = tk.Listbox(pane, exportselection=False)
        choices.pack(fill="both", expand=True, pady=8)
        for path in candidates:
            choices.insert("end", path.name)

        def accept(_event=None):
            selected = choices.curselection()
            if selected:
                dialog.destroy()
                self.load_image(candidates[selected[0]])

        choices.bind("<Double-1>", accept)
        choices.bind("<Return>", accept)
        ttk.Button(pane, text="Choose image", command=accept).pack(side="right")
        ttk.Button(pane, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        choices.selection_set(0)
        choices.focus_set()
        dialog.grab_set()

    def load_image(self, path):
        def done(result):
            image, self.cache_note = result
            saved = next((row for row in self.saved if row.title == image.title and
                          Path(row.image) == Path(image.image)), None)
            self.reload_library(saved or image)
            self.summary.configure(text=f"{image.name} · image verified.")

        self.start(lambda: self.player.prepare_image(path), done, "Inspecting the SD-card image…")

    def choose_table(self):
        game = self.game
        path = filedialog.askopenfilename(parent=self.root, title=f"Choose a VPX table for {game.name}",
                                          filetypes=[("Visual Pinball X table", "*.vpx")])
        if not path:
            return

        def work():
            prepared = self.player.prepare_table(game, path, self.report)
            self.player.save(prepared)
            return prepared

        def done(prepared):
            self.reload_library(prepared)
            self.summary.configure(text=f"Saved {prepared.name} to the game library.")

        self.start(work, done, "Checking table compatibility…")

    def play(self):
        game = self.game
        self.start(lambda: self.player.play(game, self.report),
                   lambda _: self.summary.configure(text="Game ended. The runtime has been stopped."),
                   f"Starting {game.name}…")

    def save_settings(self):
        try:
            volume = self.volume.get()
            if not 0 <= volume <= 100:
                raise ValueError("Enter a game volume from 0 to 100.")
            self.player.save_preferences(self.monitor_choices[self.monitor.get()], volume, self.cache.get())
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Save settings", str(exc), parent=self.root)
        else:
            self.summary.configure(text="Settings saved.")

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
                messagebox.showinfo("SPIKE 2 VPX", "SPIKE 2 VPX is already open.", parent=root)
                return
            PlayerWindow(root, setup)
            root.deiconify()
            root.mainloop()
    except OSError as exc:
        messagebox.showerror("SPIKE 2 VPX", f"SPIKE 2 VPX could not open.\n\n{exc}", parent=root)


if __name__ == "__main__":
    main()
