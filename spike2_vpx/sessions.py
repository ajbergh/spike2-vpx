"""Identify and recover stale records belonging to this app's WSL runtime."""

from dataclasses import dataclass
import json
import re
import subprocess
import time
from uuid import uuid4

from spike2_vpx_setup import DISTRO, RIG, run
import padpath


class StaleSession(RuntimeError):
    pass


@dataclass(frozen=True)
class Record:
    path: object
    contents: bytes
    changed: int
    slot: int
    pid: int

    def matches(self, path):
        return path.stat().st_mtime_ns == self.changed and path.read_bytes() == self.contents


def records(setup, title):
    for path in sorted((setup.data / "rig-board").glob("slot-*.run")):
        try:
            changed = path.stat().st_mtime_ns
            contents = path.read_bytes()
            data = json.loads(contents)
        except FileNotFoundError:
            continue
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or data.get("game") != title:
            continue
        slot, pid, label = data.get("slot"), data.get("pid"), data.get("label")
        if (data.get("distro") != DISTRO or data.get("emu") != "spike2"
                or type(slot) is not int or slot < 1 or path.name != f"slot-{slot}.run"
                or type(pid) is not int or pid <= 1
                or not isinstance(label, str) or not re.fullmatch(r"PAD-VPX(?:-[0-9]+)?", label)):
            raise RuntimeError("This title has a session outside the app’s recovery scope. Close that session first.")
        yield Record(path, contents, changed, slot, pid)


def is_stale(record):
    try:
        if time.time_ns() - record.changed < 120_000_000_000 or not record.matches(record.path):
            return False
        head = ["wsl.exe", "-d", DISTRO, "-u", "root", "-e"]
        if run([*head, "test", "!", "-d", f"/proc/{record.pid}"], timeout=12).returncode:
            return False
        script = padpath.to_wsl(str(RIG), DISTRO) + "/alive.sh"
        out = run([*head, "env", f"PAD_SLOT={record.slot}", "bash", script, "--procs"], timeout=20)
        return out.returncode == 0 and out.stdout.strip() == b"0" and record.matches(record.path)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def check_available(setup, title):
    existing = list(records(setup, title))
    if not existing:
        return
    if all(is_stale(record) for record in existing):
        raise StaleSession("A stopped Stern session left a launch record behind. Recover the session to play again.")
    raise RuntimeError("A Stern session is active or its status could not be verified. Close it, then try again.")


def recover(setup, title):
    existing = list(records(setup, title))
    if not all(is_stale(record) for record in existing):
        raise RuntimeError("Recovery stopped because a session is active or its status could not be verified.")
    for record in existing:
        if not record.matches(record.path):
            raise RuntimeError("The session changed while recovering. Check the game again.")
        temporary = record.path.with_suffix(f".recover-{uuid4().hex}")
        record.path.rename(temporary)
        try:
            if not record.matches(temporary):
                try:
                    record.path.hardlink_to(temporary)
                except FileExistsError:
                    pass
                raise RuntimeError("The session changed while recovering. Check the game again.")
        finally:
            temporary.unlink()
        setup.log.info("Recovered stale slot %s for %s", record.slot, title)
