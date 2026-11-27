"""Identify the primary Stern title that the VPX launcher can boot."""

import json
import os
import re
import sys
from pathlib import Path

import parts


def inspect(path):
    if not os.path.isfile(path):
        raise ValueError("The runtime cannot read this image. Choose a file on a Windows drive.")
    is_spike3, _ = parts.spike3(path)
    if is_spike3:
        raise ValueError("This is a SPIKE 3 card. Choose a SPIKE 2 SD-card image.")
    layout = parts.identify(path)
    if "rootfs" not in layout or layout.get("rootfs_guessed") or "fat" not in layout:
        raise ValueError("Could not verify the SPIKE 2 system and boot partitions in this image.")
    primary = next((titles for _, _, offset, titles, subdir in parts.games_all(path)
                    if offset == layout.get("games") and subdir is None), [])
    if len(primary) != 1:
        raise ValueError("Choose an image with one playable SPIKE 2 title in its primary games partition.")
    title = primary[0]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", title):
        raise ValueError("The card has an unsupported title directory name.")
    entries = parts._entries(path, layout["games"], "/" + title) or []
    if not any(name == "game" and mode & 0o170000 == 0o100000 for name, mode in entries):
        raise ValueError("The primary title does not contain a regular game executable.")
    return title.lower()


def spike1_title(path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spike1_emu"))
    from build_rootfs import _lookup, _open_partitions, early_spike1_game_dir, find_rootfs_and_game

    stream, partitions = _open_partitions(path)
    with stream:
        root, game, title = find_rootfs_and_game(stream, partitions)
        if root is not None and _lookup(root, "/etc/version") is not None:
            if game is None:
                title = early_spike1_game_dir(root)
            elif title and _lookup(game, "/" + title + "/game") is None:
                title = None
            if title and re.fullmatch(r"[a-zA-Z0-9_-]+", title):
                return title.lower()
    raise ValueError("Could not verify a SPIKE 1 system and game on this image.")


def inspect_stern(path):
    if not os.path.isfile(path):
        raise ValueError("The runtime cannot read this image. Choose a file on a Windows drive.")
    if parts.spike3(path)[0]:
        return {"platform": "spike3"}
    if Path(path).suffix.lower() != ".iso":
        try:
            return {"title": inspect(path), "platform": "spike2"}
        except ValueError as exc:
            try:
                return {"title": spike1_title(path), "platform": "spike1"}
            except ValueError:
                raise exc from None
    return {"title": spike1_title(path), "platform": "spike1"}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect_stern(sys.argv[1])))
    except (OSError, ValueError, SystemExit) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
