"""Image, table and saved-game operations used by the desktop window."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from spike2_vpx_setup import DISTRO, RIG

# The setup module loads the VPX kit from the same location as the console launcher.
import padisplay
import padlaunch
import padvpx
import play_spike2
from spike2_vpx import runtime


@dataclass(frozen=True)
class Game:
    title: str
    image: str
    table: str = ""

    @property
    def name(self):
        parts = self.title.replace("_", " ").split()
        acronyms = {"le", "se", "tmnt", "jp", "aiq"}
        return " ".join(word.upper() if word in acronyms else word.title() for word in parts)


@dataclass(frozen=True)
class SupportedTable:
    game: str
    table: str
    author: str
    url: str


def supported_tables():
    """List actual bundled patch maps with the VPX release each map targets."""
    rows = set()
    for mapping in padvpx.maps().values():
        metadata = mapping["table"]
        if not mapping["listed"] or not metadata.get("name"):
            continue
        source = metadata.get("url") or ""
        link = urlsplit(source)
        url = source if link.scheme == "https" and link.hostname else ""
        rows.add(SupportedTable(Game(mapping["game"], "").name, metadata["name"],
                                metadata.get("by") or "", url))
    return sorted(rows, key=lambda row: (row.game.casefold(), row.table.casefold(), row.url))


def file_of_type(path, suffix):
    file = Path(path).resolve()
    if not file.is_file() or file.suffix.casefold() != suffix:
        raise ValueError(f"Choose an existing {suffix} file.")
    return file


def folder_images(folder):
    return sorted((p for p in Path(folder).iterdir() if p.suffix.casefold() == ".raw"
                   and p.is_file()), key=lambda p: p.name.casefold())


class Player:
    def __init__(self, setup):
        self.setup = setup
        self.directory = setup.data / "tables"
        self.log_path = setup.log_path.with_name("player.log")
        self.audio_path = setup.data / "audio.json"

    def library(self):
        rows = self.setup.settings.get("games")
        if not isinstance(rows, list):
            return []
        return [Game(row["title"], row["image"], row["table"]) for row in rows
                if isinstance(row, dict) and all(isinstance(row.get(k), str)
                                                 for k in ("title", "image", "table"))
                and row["title"] and row["image"] and row["table"]]

    def save(self, game):
        games = [row for row in self.library()
                 if (row.title, Path(row.image)) != (game.title, Path(game.image))]
        self.setup.settings["games"] = [asdict(game), *(asdict(row) for row in games)]
        self.setup.save()

    def remove(self, game):
        self.setup.settings["games"] = [asdict(row) for row in self.library() if row != game]
        self.setup.save()

    def inspect_image(self, path):
        image = file_of_type(path, ".raw")
        paths = padlaunch.padpath.to_wsl_many([str(RIG), str(image)], DISTRO)
        script = paths[0] + "/inspect_vpx_card.py"
        out = self.setup.wsl("python3", "-B", script, paths[1], timeout=150)
        if out.returncode:
            try:
                error = json.loads(out.stdout)["error"]
            except (ValueError, TypeError, KeyError):
                error = runtime._why(out)
            raise ValueError(error)
        return Game(json.loads(out.stdout)["title"], str(image))

    def prepare_image(self, path):
        game = self.inspect_image(path)
        if not padlaunch.cache_on():
            return game, "Read directly from the source image."
        launch = padlaunch.Launch(game.title, game.image, dist=DISTRO, prepared=True)
        info = launch.cache_check()
        return game, padlaunch.space_words(info or {})[1] + "."

    def compatible(self, game, table, script):
        title = play_spike2.read_title(table, script)
        mapping = padlaunch.table_map(title)
        if not mapping or padlaunch.game_for(title).casefold() != game.title:
            raise ValueError(f"This table is prepared for {title}. Choose a table for {game.name}.")
        if ("padspike2.vbs" in script.casefold()
                and not (table.parent / "PADSPIKE2.vbs").is_file()):
            raise ValueError("PADSPIKE2.vbs is missing beside this table. Choose the original VPX file to prepare a new copy.")

    def prepare_table(self, game, path, progress):
        table = file_of_type(path, ".vpx")
        script = play_spike2.embedded_script(table)
        if play_spike2.patched_script(script):
            self.compatible(game, table, script)
            return Game(game.title, game.image, str(table))
        matches = [(title, info) for title, info in padvpx.fitting(str(table), script)
                   if info["game"].casefold() == game.title]
        if not matches:
            raise ValueError(f"No patch map matches this table and {game.name}. Choose a compatible VPX file.")
        title, info = padvpx.choose(matches, title=game.title)
        if not info["output"]:
            raise ValueError("This table map does not name a prepared output file.")
        self.directory.mkdir(parents=True, exist_ok=True)
        progress(f"Creating a playable copy for {game.name}…", False)
        with TemporaryDirectory(dir=self.directory) as stage:
            stage = Path(stage)
            with self.log_path.open("ab") as log:
                if padvpx.patch(str(table), outdir=str(stage), title=title, log=log):
                    raise RuntimeError("Table conversion failed. See the player log for details.")
            prepared = stage / info["output"]
            if not (stage / "PADSPIKE2.vbs").exists():
                shutil.copyfile(RIG / "vpx" / "PADSPIKE2.vbs", stage / "PADSPIKE2.vbs")
            self.compatible(game, prepared, play_spike2.embedded_script(prepared))
            target = self.directory / f"{game.title}-{uuid4().hex[:12]}"
            stage.rename(target)
        return Game(game.title, game.image, str(target / prepared.name))

    def play(self, game, progress):
        exe = self.setup.vpx_executable()
        if exe is None or self.setup.check_vpx().state != "Ready":
            raise ValueError("Choose a compatible Visual Pinball X executable in Setup.")
        play_spike2.check_controller()
        progress("Checking the image and prepared table…", False)
        if self.inspect_image(game.image).title != game.title:
            raise ValueError("The image now contains a different title. Select the image again.")
        table = file_of_type(game.table, ".vpx")
        script = play_spike2.embedded_script(table)
        self.compatible(game, table, script)
        title = play_spike2.read_title(table, script)
        progress("Preparing the SPIKE 2 runtime…", False)
        launch = padlaunch.Launch(title, game.image, dist=DISTRO, minutes=0, prepared=True,
                                  audio_control=str(self.audio_path))
        result = play_spike2.play(launch, exe.resolve(), table, 180, progress=progress)
        if result:
            raise RuntimeError(f"Visual Pinball X exited with code {result}. Runtime log: {launch.logfile}")

    def save_preferences(self, monitor, volume, cache):
        board = self.setup.data / "rig-board"
        setting = {"mode": "fullscreen", "monitor": monitor, "border": False} if monitor else {"mode": "free"}
        padisplay.save(setting, str(board / "display.conf"))
        temporary = self.audio_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"gain": volume / 100, "muted": volume == 0}), encoding="utf-8")
        temporary.replace(self.audio_path)
        padlaunch.set_cache_on(cache)
        self.setup.settings.update(lcd_monitor=monitor, volume=volume)
        self.setup.save()
