"""Pinned WSL image and import support for SPIKE 2 VPX."""

import os
import shutil
import subprocess
import sys
from typing import Optional

from .payloads import Payload

RUNTIME_VERSION = 6

# Use the existing upstream image until a SPIKE 2-only runtime image is published.
IMAGE = Payload(
    key="runtime-full",
    filename="pad-runtime-full.tar.gz",
    release_tag="runtime-7",
    sha256="96d1966825e7a4593dcf1a0e62f581d3c1220ec0921dd603479e66dd50eeaeae",
    size=434233620,
    version="PAD Runtime 6 (full)",
    what="the pinned upstream Linux image for the emulator",
)

_CREATE_FLAGS = (subprocess.CREATE_NO_WINDOW
                 if sys.platform == "win32" else 0)

#: wsl.exe speaks UTF-16LE by default, which turns "Ubuntu" into
#: "U\0b\0u\0n\0t\0u\0" and breaks every comparison downstream.  WSL_UTF8=1
#: makes it speak UTF-8; the decoder below still strips NULs, because an older
#: wsl.exe ignores the variable and answering that with mojibake would be a
#: silent wrong answer rather than a loud one.
def _wsl_env():
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"
    return env


def _run(args, timeout=120, stdin=None):
    return subprocess.run(args, capture_output=True, timeout=timeout,
                          env=_wsl_env(), stdin=stdin,
                          creationflags=_CREATE_FLAGS)


def _why(out) -> str:
    """What wsl.exe actually said, wherever it said it.

    IT WRITES ITS ERRORS TO STDOUT, in UTF-16, and returns 4294967295 - so a
    message built from stderr and the return code alone says "exit 4294967295"
    for a full disk, a WSL 1 machine, virtualization switched off in the BIOS
    and a distro that will not start, which are four different problems with
    four different answers."""
    for stream in (getattr(out, "stderr", b""), getattr(out, "stdout", b"")):
        said = " ".join(_text(stream or b"").split())
        if said:
            return said
    rc = getattr(out, "returncode", "?")
    return "exit %s (WSL said nothing)" % rc


def _text(raw):
    if b"\x00" in raw:
        try:
            return raw.decode("utf-16-le", "replace")
        except Exception:                                   # noqa: BLE001
            return raw.replace(b"\x00", b"").decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


IMPORT_ROOM = 3 * 1024 ** 3

#: ...and on top of the image itself where it is downloaded to.
DOWNLOAD_SLACK = 512 * 1024 ** 2

#: shutil.disk_usage, here so a test can set the free space.
_disk_usage = shutil.disk_usage


def _free(path) -> Optional[int]:
    try:
        return _disk_usage(path).free
    except OSError:
        return None


def _drive(path) -> str:
    return os.path.splitdrive(os.path.abspath(path))[0] or path


def _need_room(path, need, what) -> None:
    """Refuse, with the drive and both numbers, when *path*'s drive has less
    than *need* bytes free.  An unreadable drive is not a refusal."""
    free = _free(path)
    if free is not None and free < need:
        raise RuntimeError(
            "not enough free space to %s: it needs about %.1f GB on %s, which "
            "has %.1f GB free. Free some space on %s, then try again."
            % (what, need / 1024 ** 3, _drive(path), free / 1024 ** 3,
               _drive(path)))


def _room_note(path) -> str:
    """" (<drive> has N GB free)" for a failure message; "" when unreadable."""
    free = _free(path)
    return ("" if free is None else " (%s has %.1f GB free)"
            % (_drive(path), free / 1024 ** 3))


def import_image(distro, target, tarball, runner=None):
    """Import a verified image as WSL2 without replacing an existing distro."""
    os.makedirs(target, exist_ok=True)
    _need_room(target, IMPORT_ROOM, "install the emulator's Linux")
    run = runner or _run
    out = run(["wsl.exe", "--import", distro, target, tarball, "--version", "2"],
              timeout=900)
    if out.returncode != 0:
        raise RuntimeError("could not import the runtime: %s%s"
                           % (_why(out), _room_note(target)))
