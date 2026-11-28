"""Receive a verified ZIP member from Windows and keep one sparse RAW in WSL."""

import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

import cardcopy
from inspect_vpx_card import imported_image_name, inspect


def receive(source, image, size, budget):
    zero = bytes(128 * 1024)
    offset = allocated = 0
    while offset < size:
        block = source.read(min(len(zero), size - offset))
        if not block:
            raise ValueError("The ZIP image transfer stopped before it was complete.")
        if block != zero[:len(block)]:
            # Count rounded allocations, including the final partial block.
            allocated += ((len(block) + 4095) // 4096) * 4096
            if allocated > budget:
                raise ValueError("Free up space on the drive containing the SPIKE 2 runtime and import again.")
            image.seek(offset)
            image.write(block)
        offset += len(block)
    if source.read(1) != b"\x01":
        raise ValueError("The ZIP image transfer did not finish verification.")
    image.truncate(size)


def punch_unused(path, image):
    # Reuse the cache copier's filesystem allocation map. The seek-gap heuristic
    # is for reading off slow drives and is unnecessary when punching holes.
    fallocate = ctypes.CDLL(None, use_errno=True).fallocate
    fallocate.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64)
    punch_keep_size = 0x02 | 0x01  # FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE
    for start, end in cardcopy.unused_ranges(path):
        if fallocate(image.fileno(), punch_keep_size, start, end - start):
            raise OSError(os.strerror(ctypes.get_errno()))


def import_card(source, directory, size, budget, expected):
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as stage:
        path = Path(stage) / "card.raw"
        with path.open("w+b") as image:
            receive(source, image, size, budget)
            image.flush()
            title = inspect(path)
            if expected != "-" and title != expected:
                return {"title": title}
            punch_unused(path, image)
            os.fsync(image.fileno())
        destination = directory / imported_image_name(title)
        windows_path = subprocess.check_output(["wslpath", "-w", str(directory)], text=True).strip()
        path.replace(destination)
    return {"title": title, "image": windows_path + "\\" + destination.name}


def main(argv):
    size, budget, expected = argv
    directory = Path.home() / ".local/share/spike2-vpx/images"
    try:
        result = import_card(sys.stdin.buffer, directory, int(size), int(budget), expected)
    except (OSError, ValueError, struct.error, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
