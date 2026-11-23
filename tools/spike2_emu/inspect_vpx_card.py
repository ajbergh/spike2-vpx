"""Identify the primary title that the VPX launcher can boot from a raw card."""

import json
import os
import re
import sys

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


if __name__ == "__main__":
    try:
        print(json.dumps({"title": inspect(sys.argv[1])}))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
