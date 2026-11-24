"""Lighting setup for the desktop player."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from xml.etree.ElementTree import ParseError

import padcontroller
import padlighting


ROLE_NAMES = {"": "Unassigned", **padlighting.ROLES}
ROLE_KEYS = {name: key for key, name in ROLE_NAMES.items()}
INK, PANEL, LINE, HIGHLIGHT = "#edf2f7", "#1b2330", "#677d95", "#79ddc5"
GAME_OUTPUTS = (("Blades", {"left", "right"}), ("Speakers", {"speaker"}), ("Topper", {"topper"}))
# Title examples documented by the device-table survey in cablights.py.
KNOWN_BUILDS = (
    ("Deadpool", "Topper"), ("Elvira", "Speakers, topper"), ("Foo Fighters", "Speakers"),
    ("Godzilla", "Topper"), ("John Wick", "Blades"), ("King Kong", "Blades"),
    ("Led Zeppelin", "Blades, speakers"), ("Metallica Remastered", "Blades"),
    ("Rush", "Blades, speakers"), ("Star Wars", "Topper"), ("Venom", "Speakers"),
)


class LightingPage:
    def __init__(self, owner, page):
        self.owner = owner
        self.page = page
        self.fixtures, self.controllers, self.assignments = {}, {}, {}
        self.config_path = None
        self.selected = ""
        self.rows = {}
        self.loaded = False
        saved = padlighting.settings(owner.setup.settings.get("lighting"))
        self.override = saved.get("path") or ""
        self.enabled = tk.BooleanVar(value=bool(saved.get("enabled")))
        self.speaker_fallback = tk.BooleanVar(value=bool(saved.get("speaker_fallback")))
        self.brightness = tk.StringVar(value=saved.get("brightness", 50))
        self.path = tk.StringVar(value="Looking for a DOF cabinet configuration")
        self.status = tk.StringVar()
        self.role = tk.StringVar(value=ROLE_NAMES[""])
        self.first = tk.StringVar(value="1")
        self.length = tk.StringVar(value="")
        self.reverse = tk.BooleanVar()
        self.game_name = tk.StringVar(value="Select a game to see its lighting status.")
        self.game_status = [tk.StringVar(value="Not checked yet") for _ in GAME_OUTPUTS]
        self.game_notice = tk.StringVar()

        game_row = ttk.Frame(page)
        game_row.pack(fill="x", pady=(0, 7))
        heading = ttk.Frame(game_row)
        heading.pack(fill="x")
        ttk.Label(heading, text="Game lighting", style="Section.TLabel").pack(side="left")
        ttk.Button(heading, text="Known builds…", command=self.known_builds).pack(side="right")
        ttk.Label(game_row, textvariable=self.game_name, style="Hint.TLabel").pack(anchor="w")
        outputs = ttk.Frame(game_row)
        outputs.pack(fill="x", pady=(3, 0))
        for column, ((label, _), value) in enumerate(zip(GAME_OUTPUTS, self.game_status)):
            outputs.columnconfigure(column, weight=1, uniform="game-lighting")
            field = ttk.Frame(outputs)
            field.grid(row=0, column=column, sticky="ew")
            ttk.Label(field, text=label).pack(anchor="w")
            ttk.Label(field, textvariable=value, style="Hint.TLabel").pack(anchor="w")
        ttk.Label(game_row, textvariable=self.game_notice, style="Hint.TLabel",
                  wraplength=710).pack(anchor="w")

        ttk.Checkbutton(page, text="Use Stern expression lighting in SPIKE 2 games",
                        variable=self.enabled).pack(anchor="w")
        ttk.Checkbutton(page, text="Use speaker lighting when blade data is unavailable",
                        variable=self.speaker_fallback).pack(anchor="w")
        file_row = ttk.Frame(page)
        file_row.pack(fill="x", pady=(9, 4))
        ttk.Label(file_row, textvariable=self.path, wraplength=465,
                  style="Hint.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Browse…", command=self.browse).pack(side="right", padx=(6, 0))
        ttk.Button(file_row, text="Detect", command=self.detect).pack(side="right")

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(6, 8))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        inventory = ttk.Frame(body)
        inventory.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.list = ttk.Treeview(inventory, columns=("pixels", "source"),
                                 show="tree headings", height=5, selectmode="browse")
        for column, title, width in (("#0", "Detected fixture", 156), ("pixels", "Pixels", 60),
                                      ("source", "Stern source", 113)):
            self.list.heading(column, text=title)
            self.list.column(column, width=width, minwidth=40, stretch=column != "pixels")
        scroll = ttk.Scrollbar(inventory, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self.list.bind("<<TreeviewSelect>>", self.choose)
        self.diagram = tk.Canvas(body, width=215, height=160, bg=PANEL, highlightthickness=0)
        self.diagram.grid(row=0, column=1, sticky="nsew")
        self.diagram.bind("<Configure>", lambda _: self.draw())

        edit = ttk.Frame(page)
        edit.pack(fill="x")
        ttk.Label(edit, text="Stern source").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.source = ttk.Combobox(edit, textvariable=self.role, values=tuple(ROLE_NAMES.values()),
                                   state="readonly", width=17)
        self.source.grid(row=0, column=1, sticky="w")
        self.source.bind("<<ComboboxSelected>>", lambda _: self.draw())
        ttk.Label(edit, text="First pixel").grid(row=0, column=2, padx=(10, 4))
        self.first_input = ttk.Entry(edit, textvariable=self.first, width=5)
        self.first_input.grid(row=0, column=3)
        ttk.Label(edit, text="Count").grid(row=0, column=4, padx=(10, 4))
        self.length_input = ttk.Entry(edit, textvariable=self.length, width=5)
        self.length_input.grid(row=0, column=5)
        self.flip = ttk.Checkbutton(edit, text="Reverse", variable=self.reverse, command=self.draw)
        self.flip.grid(row=0, column=6, padx=(9, 0))
        self.test_button = ttk.Button(edit, text="Test fixture", command=self.test)
        self.test_button.grid(row=0, column=7, padx=(8, 0))
        ttk.Label(page, text="Leave Count blank to use the remaining pixels. Unassigned fixtures on the same Teensy stay dark during Stern play.",
                  wraplength=700, style="Hint.TLabel").pack(anchor="w", pady=(5, 6))
        controls = ttk.Frame(page)
        controls.pack(fill="x")
        ttk.Label(controls, text="Brightness %").pack(side="left", padx=(0, 6))
        ttk.Spinbox(controls, from_=1, to=100, width=5, textvariable=self.brightness).pack(side="left")
        ttk.Button(controls, text="Save lighting", command=self.save).pack(side="right")
        ttk.Label(page, textvariable=self.status, wraplength=715,
                  style="Hint.TLabel").pack(anchor="w", pady=(5, 0))
        self.show_selection("")

    def refresh(self):
        if not self.loaded:
            self.load(self.override)
        self.show_game(self.owner.game)

    def show_game(self, game, notice=None):
        version = f" · Image v{game.image_version}" if game and game.image_version else ""
        available = self.owner.player.game_lighting(game) if game else None
        if game:
            scan_hint = " · Checked at startup" if available is None else ""
            self.game_name.set(game.name + version + scan_hint)
        else:
            self.game_name.set("Select a game to see its lighting status.")
        for (label, roles), value in zip(GAME_OUTPUTS, self.game_status):
            if available is None:
                status = "Not checked yet"
            elif not roles.intersection(available):
                status = "No usable mapping found"
            elif label == "Blades" and not roles <= available:
                status = "Left detected" if "left" in available else "Right detected"
            else:
                status = "Detected"
            value.set(status)
        if notice is not None:
            self.game_notice.set(notice)

    def known_builds(self):
        window = tk.Toplevel(self.owner.root)
        window.title("Known lighting builds")
        window.transient(self.owner.root)
        panel = ttk.Frame(window, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Builds documented with lighting data. Outputs can vary by edition and image.",
                  wraplength=430, style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        table = ttk.Treeview(panel, columns=("game", "lighting"), show="headings", height=len(KNOWN_BUILDS))
        table.heading("game", text="Game")
        table.heading("lighting", text="Documented outputs")
        table.column("game", width=190)
        table.column("lighting", width=230)
        for row in KNOWN_BUILDS:
            table.insert("", "end", values=row)
        table.pack(fill="both", expand=True)
        ttk.Button(panel, text="Close", command=window.destroy).pack(anchor="e", pady=(10, 0))
        window.bind("<Escape>", lambda _: window.destroy())

    def detect(self):
        self.load("")

    def browse(self):
        path = filedialog.askopenfilename(parent=self.owner.root, title="Choose DOF cabinet XML",
                                          filetypes=[("Cabinet XML", "*.xml")])
        if path:
            self.load(path)

    def load(self, override):
        saved = padlighting.settings(self.owner.setup.settings.get("lighting"))
        try:
            table = getattr(self.owner.game, "table", "")
            path = Path(override).resolve() if override else padlighting.detect(
                padcontroller._dof_dirs(), Path(table).parent if table else None)
            controllers, fixtures = padlighting.read_cabinet(path) if path else ({}, {})
        except (OSError, ValueError, ParseError) as exc:
            self.status.set(f"Could not read cabinet configuration. {exc}")
            return
        self.loaded = True
        self.override = str(path) if override else ""
        same_file = str(path or "") == str(saved.get("detected_path", ""))
        old = padlighting.settings(saved.get("assignments")) if same_file else {}
        self.config_path = path
        self.controllers, self.fixtures = controllers, fixtures
        self.assignments = {}
        for name, fixture in fixtures.items():
            entry = old.get(name)
            if not isinstance(entry, dict):
                entry = {"role": "" if fixture.issue else padlighting.suggest(fixture)}
            self.assignments[name] = dict(entry)
        self.path.set(str(path) if path else "No unique Cabinet.xml detected. Choose one with Browse.")
        self.status.set(f"{len(fixtures)} fixtures detected." if path else "")
        self.list.delete(*self.list.get_children())
        self.rows = {}
        for index, (name, fixture) in enumerate(fixtures.items()):
            row = str(index)
            self.rows[row] = name
            self.list.insert("", "end", iid=row, text=name,
                             values=(fixture.count or "—", self.assignment_text(name)))
        self.show_selection("")
        if self.rows:
            self.list.selection_set("0")

    def assignment_text(self, name):
        fixture = self.fixtures[name]
        if fixture.issue:
            return "Unavailable"
        return ROLE_NAMES.get(self.assignments[name].get("role"), "Unassigned")

    def choose(self, _event):
        rows = self.list.selection()
        name = self.rows[rows[0]] if rows else ""
        if name == self.selected:
            return
        previous = self.selected
        try:
            self.commit()
        except ValueError as exc:
            self.status.set(str(exc))
            row = next(row for row, name in self.rows.items() if name == previous)
            self.list.selection_set(row)
            return
        self.show_selection(name)

    def commit(self):
        if not self.selected or self.fixtures[self.selected].issue:
            return
        role = ROLE_KEYS[self.role.get()]
        try:
            offset = int(self.first.get()) - 1
            length = int(self.length.get() or 0)
        except ValueError as exc:
            raise ValueError("Enter whole numbers for First pixel and Count.") from exc
        fixture = self.fixtures[self.selected]
        if offset < 0 or offset >= fixture.count or length < 0 or offset + length > fixture.count:
            raise ValueError(f"Select a pixel range within {fixture.name}'s {fixture.count} pixels.")
        self.assignments[self.selected] = {"role": role, "offset": offset, "length": length,
                                           "reverse": bool(self.reverse.get())}
        row = next(row for row, name in self.rows.items() if name == self.selected)
        self.list.set(row, "source", ROLE_NAMES[role])

    def show_selection(self, name):
        self.selected = name
        fixture = self.fixtures.get(name)
        entry = self.assignments.get(name, {})
        self.role.set(ROLE_NAMES.get(entry.get("role"), ROLE_NAMES[""]))
        offset = entry.get("offset", 0)
        self.first.set(str(offset + 1 if isinstance(offset, int) else 1))
        self.length.set(str(entry.get("length") or ""))
        self.reverse.set(bool(entry.get("reverse")))
        can_edit = fixture is not None and not fixture.issue
        self.source.configure(state="readonly" if can_edit else "disabled")
        for widget in (self.first_input, self.length_input, self.flip, self.test_button):
            widget.configure(state="normal" if can_edit else "disabled")
        if fixture:
            controller = self.controllers.get(fixture.controller)
            label = f"{fixture.controller} · {controller.port}" if controller else fixture.controller
            details = fixture.issue or f"{label} · {fixture.width} × {fixture.height} · {fixture.order}"
            self.status.set(details)
        self.draw()

    def draw(self):
        canvas = self.diagram
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if min(width, height) < 20:
            return
        cx = width / 2
        top = 16
        bottom = height - 16
        back = top + (bottom - top) * 0.27
        half = min(57, width / 3)
        canvas.create_rectangle(cx - half, top, cx + half, back, outline=LINE, width=2)
        canvas.create_polygon(cx - half, back + 6, cx + half, back + 6,
                              cx + half + 12, bottom, cx - half - 12, bottom,
                              fill="", outline=LINE, width=2)
        active = {}
        for name, entry in self.assignments.items():
            if name != self.selected and not self.fixtures[name].issue:
                active.setdefault(entry.get("role"), set()).add(name)
        role = ROLE_KEYS.get(self.role.get(), "")
        if self.selected and role and not self.fixtures[self.selected].issue:
            active.setdefault(role, set()).add(self.selected)

        def color(key):
            return HIGHLIGHT if key == role and self.selected in active.get(key, ()) else (
                "#6ca6d8" if active.get(key) else "#48566a")

        for side, key in ((-1, "left"), (1, "right")):
            for i in range(9):
                x = cx + side * (half - 8 + i * 1.1)
                y = back + 12 + i * max(1, (bottom - back - 24) / 8)
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color(key), outline="")
            x = cx + side * half * 0.58
            y = top + (back - top) * 0.55
            canvas.create_oval(x - 8, y - 8, x + 8, y + 8, outline=color("speaker"), width=3)
        canvas.create_line(cx - 22, top - 7, cx + 22, top - 7, fill=color("topper"), width=4)
        fixture = self.fixtures.get(self.selected)
        if fixture and not fixture.issue and role in ("left", "right"):
            x = cx - 25 if role == "left" else cx + 25
            a, b = bottom - 15, back + 17
            if self.reverse.get():
                a, b = b, a
            canvas.create_line(x, a, x, b, arrow="last", fill=HIGHLIGHT)

    def save(self):
        try:
            self.commit()
            brightness = int(self.brightness.get())
            if not 1 <= brightness <= 100:
                raise ValueError("Brightness must be between 1 and 100.")
            mappings = padlighting.assignments(self.fixtures, self.assignments)
            if self.enabled.get() and not mappings:
                raise ValueError("Assign at least one supported fixture to enable Stern lighting.")
            settings = {"path": self.override, "detected_path": str(self.config_path or ""),
                        "enabled": bool(self.enabled.get()), "brightness": brightness,
                        "speaker_fallback": bool(self.speaker_fallback.get()),
                        "assignments": self.assignments}
            old = self.owner.setup.settings.get("lighting")
            self.owner.setup.settings["lighting"] = settings
            try:
                self.owner.setup.save()
            except OSError:
                if old is None:
                    self.owner.setup.settings.pop("lighting", None)
                else:
                    self.owner.setup.settings["lighting"] = old
                raise
            self.assignments = {name: dict(entry) for name, entry in self.assignments.items()}
        except (OSError, ValueError) as exc:
            messagebox.showerror("Lighting settings", str(exc), parent=self.owner.root)
        else:
            self.status.set("Lighting settings saved.")

    def test(self):
        name = self.selected
        fixture = self.fixtures.get(name)
        if not fixture or fixture.issue or self.owner.busy:
            return
        try:
            self.commit()
            entry = dict(self.assignments[name])
            entry["role"] = entry.get("role") or "left"
            _, _, pixels = padlighting.assignments(self.fixtures, {name: entry})[0]
        except ValueError as exc:
            self.status.set(str(exc))
            return
        controller = self.controllers[fixture.controller]
        self.owner.start(lambda: padlighting.test_fixture(controller, fixture, pixels),
                         lambda _: self.status.set(f"Test finished. Released {controller.port}."),
                         f"Testing {name}")
