"""Portable preferences and previewable support reports."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import re
from math import pi, sin
from struct import pack
import sys
from tempfile import NamedTemporaryFile
import wave
from zipfile import ZIP_DEFLATED, ZipFile

from spike2_vpx.runtime import RUNTIME_VERSION


PROFILES = ("Desktop", "Cabinet")
BACKUP_FORMAT = "spike2-vpx-library"
LAYOUT_ROLES = {"playfield", "backglass", "lcd"}


def atomic_write(path, content):
    path = Path(path)
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def backup(settings, cache):
    preferences = {key: settings[key] for key in ("games", "cabinet_layout", "display_profiles",
                                                   "display_profile", "lcd_monitor", "volume") if key in settings}
    return {"format": BACKUP_FORMAT, "version": 1, "preferences": preferences, "card_cache": cache}


def read_backup(path):
    from spike2_vpx.player import game_from_row

    with Path(path).open("rb") as file:
        payload = file.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("This library backup exceeds the supported size.")
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ValueError("Choose a SPIKE 2 VPX library backup in JSON format.") from exc
    if (not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT
            or type(data.get("version")) is not int or data["version"] != 1):
        raise ValueError("This SPIKE 2 VPX backup format is unsupported.")
    preferences = data.get("preferences")
    if not isinstance(preferences, dict) or type(data.get("card_cache")) is not bool:
        raise ValueError("The backup is missing its preferences.")
    games = preferences.get("games", [])
    if not isinstance(games, list) or any(game_from_row(row) is None for row in games):
        raise ValueError("The backup contains an invalid game pairing.")
    for row in games:
        if not re.fullmatch(r"[a-z0-9_-]+", row["title"]):
            raise ValueError("The backup contains an invalid game title.")
        for key, suffixes in (("image", {".raw", ".iso", ".img", ".zip"}), ("table", {".vpx"}), ("artwork", {".png", ".gif"})):
            path = row.get(key, "")
            if not isinstance(path, str) or (path and (not Path(path).is_absolute()
                    or Path(path).suffix.casefold() not in suffixes or any(c in path for c in "\x00\r\n"))):
                raise ValueError(f"The backup contains an invalid {key} path.")
    profiles = preferences.get("display_profiles", {})
    if not isinstance(profiles, dict) or set(profiles) - set(PROFILES):
        raise ValueError("The backup contains an invalid display profile.")
    for layout in [preferences.get("cabinet_layout", {}), *profiles.values()]:
        if not isinstance(layout, dict) or any(role not in LAYOUT_ROLES or not isinstance(key, str)
                                               for role, key in layout.items()):
            raise ValueError("The backup contains an invalid display assignment.")
    volume = preferences.get("volume", 100)
    if type(volume) is not int or not 0 <= volume <= 100:
        raise ValueError("The backup contains an invalid volume.")
    if preferences.get("display_profile", "Desktop") not in PROFILES:
        raise ValueError("The backup contains an invalid active display profile.")
    legacy = preferences.get("lcd_monitor", 0)
    if type(legacy) is not int or legacy < 0:
        raise ValueError("The backup contains an invalid LCD setting.")
    return backup(preferences, data["card_cache"])


def app_version():
    bundled = Path(__file__).resolve().parents[1] / "python"
    if platform.system() == "Windows" and Path(sys.executable).resolve().parent == bundled:
        import winreg
        try:
            key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\SPIKE2-VPX_is1"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
                return winreg.QueryValueEx(handle, "DisplayVersion")[0]
        except OSError:
            pass
    return "Source checkout"


def report(setup, checks):
    lines = ["SPIKE 2 VPX support report", datetime.now(timezone.utc).isoformat(timespec="seconds"),
             f"App version: {app_version()}", f"Runtime package version: {RUNTIME_VERSION}",
             f"Windows: {platform.version()}", f"Python: {platform.python_version()}", "", "Setup checks"]
    for key, check in checks.items():
        version = re.search(r"\b(VPX|WSLg|runtime) ([0-9]+(?:\.[0-9]+)*)", check.detail)
        lines.append(f"{key}: {check.state}" + (f" ({version[0]})" if version else ""))
    lines.extend(("", "Recent errors"))
    directory = setup.log_path.parent
    logs = [(name, directory / name) for name in ("setup.log", "player.log",
                                                  "controller-registration.log", "wsl-platform.log")]
    logs.append(("controller.log", directory / "play" / "controller.log"))
    try:
        recent = sorted((directory / "play").glob("padvpx-*.log"), key=lambda file: file.stat().st_mtime,
                        reverse=True)[:2]
    except OSError:
        recent = []
    logs.extend(("runtime log", file) for file in recent)
    for name, path in logs:
        try:
            with path.open("rb") as file:
                size = file.seek(0, 2)
                file.seek(max(0, size - 32768))
                tail = file.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        errors = [line for line in tail.splitlines() if re.search(
            r"\b(error|fail(?:ed|ure)?|exception|denied|timeout|exited)\b", line, re.IGNORECASE)]
        for line in errors[-10:]:
            line = re.sub(r"""["'][A-Za-z]:[\\/][^"']*["']|[A-Za-z]:[\\/].*|/mnt/[a-z]/.*|/home/.*|\\\\[^\s].*""",
                          "[path removed]", line)
            line = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[email removed]", line)
            lines.append(f"{name}: {line[:300]}")
    return "\n".join(lines) + "\n"


def write_report(path, text):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("support-report.txt", text)
    atomic_write(path, buffer.getvalue())


def test_tone(volume):
    import winsound

    with NamedTemporaryFile(suffix=".wav", delete=False) as file:
        path = Path(file.name)
    try:
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            samples = bytearray()
            for i in range(8000):
                envelope = min(i / 300, (7999 - i) / 300, 1)
                sample = sin(2 * pi * 523.25 * i / 16000)
                samples.extend(pack("<h", int(6500 * volume / 100 * envelope * sample)))
            audio.writeframes(samples)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    finally:
        path.unlink(missing_ok=True)
