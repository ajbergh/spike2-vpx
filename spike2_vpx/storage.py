"""Storage locations selected in the Windows installer."""

from configparser import ConfigParser
from pathlib import Path, PureWindowsPath


def installed_folder(app, key):
    config = ConfigParser(interpolation=None)
    config.read(Path(app) / "storage.ini", encoding="utf-8-sig")
    value = config.get("Storage", key, fallback="")
    folder = PureWindowsPath(value)
    if folder.is_absolute() and len(folder.drive) == 2:
        return Path(value)
    return None
