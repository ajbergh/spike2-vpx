"""Run the SPIKE 1 and SPIKE 3 backends on the standalone player's WSL board."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import padlaunch
import padpath


def board_record(platform, title=None, slot=None, *, board=None):
    root = Path(board or padlaunch.BOARD)
    records = []
    for path in root.glob(f"{platform}-*.run"):
        try:
            if time.time() - path.stat().st_mtime > 120:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("emu") != platform:
            continue
        index, game = record.get("slot"), record.get("game")
        if (type(index) is not int or path.name != f"{platform}-{index}.run"
                or not isinstance(game, str) or not record.get("root") or not record.get("distro")):
            continue
        if (slot is None or index == slot) and (title is None or game.casefold() == title.casefold()):
            records.append(record)
    return max(records, key=lambda r: r["started"] if type(r.get("started")) is int else 0,
               default=None)


def prepared_spike3_title(card, distro):
    """Find a prepared card using the same store and identity as the launcher."""
    script = Path(padlaunch.RIG).parent / "spike3_emu" / "s3store.py"
    try:
        _, home = padlaunch.account(distro)
        command = padlaunch.wsl_head(distro, root=True) + ["-e", "env"]
        if home:
            command.append("HOME=" + home)
        result = padlaunch._run(command + ["python3", "-B", padpath.to_wsl(str(script), distro),
                                          "path", "--title", padpath.to_wsl(str(card), distro)], timeout=25)
        if result.returncode == 0:
            title = padlaunch._text(result.stdout).strip()
            if re.fullmatch(r"[A-Za-z0-9_-]+", title):
                return title
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pass
    return None


class SternLaunch:
    # First boot can include a toolchain build or extraction of the games partition.
    first_boot_timeout = 1800
    def __init__(self, title, card, dist=None, minutes=240, *, game_dir=None, prepared=False,
                 audio_control=None, cache_update=None, keyfile=None):
        if game_dir or not card:
            raise ValueError("Choose a card image for this Stern title.")
        self.map_title = title
        self.title = padlaunch.game_for(title)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.title):
            raise ValueError("Invalid native game name in the table map.")
        self.card = card
        self.distro = dist if dist is not None else padlaunch.distro()
        self.minutes = minutes
        self.hidden = False
        self.mute = False
        self.label = f"PAD-VPX-{os.getpid()}-{uuid4().hex[:12]}"
        self.logfile = Path(padlaunch.LOG).with_name(f"padvpx-{title}-{self.label}.log")
        self.script = padpath.to_wsl(str(Path(padlaunch.RIG).parent / "vpx_stern_session.sh"), self.distro)
        self.slot = None
        self.proc = None
        self.viewer = None
        self.audio_control = audio_control
        self.cache_update = cache_update
        self.keyfile = keyfile
        self.windows_lcd = None
        self.lighting_scan = ""
        self.lighting_topper = False

    def note(self, message):
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        with self.logfile.open("a", encoding="utf-8") as log:
            log.write(message + "\n")

    def head(self):
        _, home = padlaunch.account(self.distro)
        args = padlaunch.wsl_head(self.distro, root=True) + ["-e", "env"]
        if home:
            args.append("HOME=" + home)
        return args

    def environment(self):
        env = [f"PAD_LABEL={self.label}", f"PAD_VPX_SESSION={self.label}", f"PAD_VPX_GAME={self.title}",
               f"PAD_BOARD={padpath.to_wsl(padlaunch.BOARD, self.distro)}",
               f"PAD_HIDDEN={int(self.hidden)}", f"PAD_AUDIO={int(not (self.hidden or self.mute))}"]
        python = padlaunch.win_python()
        if python:
            env.append("PAD_WINPYTHON=" + padpath.to_wsl(python, self.distro))
        if self.audio_control:
            env.append("PAD_AUDIO_CTL=" + self.audio_control)
        return env

    def record(self):
        record = board_record(self.platform, self.title, self.slot)
        return record if record and record.get("label") == self.label else None

    def start(self):
        why = padlaunch.no_linux(self.distro)
        if why:
            raise RuntimeError(why)
        key = self.input_key()
        self.slot = self.take_slot()
        Path(padlaunch.BOARD).mkdir(parents=True, exist_ok=True)
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        if self.cache_update:
            self.cache_update(f"Preparing the {padlaunch.PLATFORMS[self.platform]} runtime. The first launch may take longer.")
        args = self.head() + self.environment() + [
            "bash", self.script, self.platform, self.label, str(self.slot),
            padpath.to_wsl(self.card, self.distro), str(self.minutes), self.title,
        ]
        with self.logfile.open("ab") as log:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE if key is not None else subprocess.DEVNULL,
                                         stdout=log, stderr=log, creationflags=padlaunch._CREATE_NO_WINDOW)
        if key is not None:
            try:
                self.proc.stdin.write(key)
            except BrokenPipeError:
                pass
            finally:
                try:
                    self.proc.stdin.close()
                except BrokenPipeError:
                    pass
        return self.slot

    def input_key(self):
        return None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        viewer = self.viewer
        self.viewer = None
        if viewer is not None and viewer.poll() is None:
            try:
                viewer.terminate()
                viewer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                viewer.kill()
            except OSError as exc:
                self.note(f"DMD viewer cleanup failed. {exc}")
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            # The session script also accepts a stop while WSL is still starting.
            try:
                padlaunch._run(self.head() + ["bash", self.script, "stop", self.label], timeout=30)
                proc.wait(timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                self.note(f"Session cleanup did not finish. {exc}")
                proc.terminate()
        self.slot = None


def stop_record(record):
    label = record.get("label")
    if not isinstance(label, str) or not re.fullmatch(r"PAD-VPX-[0-9]+-[a-f0-9]{12}", label):
        raise RuntimeError("This session belongs to another player. Close it in the app that started it.")
    distro = record["distro"]
    script = padpath.to_wsl(str(Path(padlaunch.RIG).parent / "vpx_stern_session.sh"), distro)
    return padlaunch._run(padlaunch.wsl_head(distro, root=True) + [
        "-e", "bash", script, "stop", label,
    ], timeout=30)


class Spike1Launch(SternLaunch):
    platform = "spike1"

    def take_slot(self):
        record = board_record(self.platform)
        if record:
            raise RuntimeError(f"Close the existing {record['game']} SPIKE 1 session before playing.")
        return 0

    def environment(self):
        env = super().environment() + ["S1_PIVOT=1"]
        try:
            wiring = padlaunch.encoder_lamp_env(padlaunch.table_map(self.map_title).get("encoder_lamps") or {})
        except (TypeError, ValueError, AttributeError) as exc:
            self.note(f"Ignoring invalid encoder lamp wiring. {exc}")
        else:
            if wiring:
                env.append("S1_ENCODER_LAMPS=" + wiring)
        return env

    def open_display(self, rig, target):
        script = Path(__file__).with_name("spike1_display.py")
        command = [sys.executable, str(script), str(Path(rig.dump) / "spi0.cap")]
        if target:
            command.extend(str(value) for value in target)
        with self.logfile.open("ab") as log:
            self.viewer = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           creationflags=padlaunch._CREATE_NO_WINDOW)


class Spike3Launch(SternLaunch):
    platform = "spike3"

    def take_slot(self):
        if board_record(self.platform, self.title):
            raise RuntimeError(f"Close the existing {self.title} SPIKE 3 session before playing.")
        for slot in (7, 6, 5, 4):
            if not board_record(self.platform, slot=slot):
                return slot
        raise RuntimeError("Every SPIKE 3 runtime slot is in use.")

    def input_key(self):
        import spike3_image

        prepared = prepared_spike3_title(self.card, self.distro)
        if prepared == self.title:
            return None
        if prepared:
            raise ValueError(f"The prepared SPIKE 3 card contains {prepared}, but the table expects {self.title}.")
        path = spike3_image.keyfile(self.card, self.keyfile)
        if path is None:
            raise spike3_image.KeyRequired("Preparing this encrypted SPIKE 3 card requires its OTP key file.")
        return spike3_image.read_key(path)

    def lcd_frame_path(self):
        return Path(padlaunch.BOARD) / f"lcd-frame-{self.slot}-{self.label}.txt"

    def environment(self):
        env = super().environment() + [
            "PAD_S3_KEY_STDIN=1", "PAD_S3_AUTOGUIDE=walk", "PAD_S3_PIVOT=0", "PAD_S3_PLAYFIELD=0",
            f"PAD_TITLE_TAG=[rig {self.slot}: {self.label}]",
        ]
        if self.windows_lcd:
            x, y, right, bottom = self.windows_lcd
            env += ["PAD_WINDOWS_LCD=1", f"PAD_WINDOWS_LCD_W={right - x}",
                    f"PAD_WINDOWS_LCD_H={bottom - y}", "PAD_WINDOWS_LCD_FRAME=" + self.lcd_frame_path().name]
        return env

    def ready(self):
        # s3prog reports attract over stdout after boot and Guided Setup.
        if not hasattr(self, "_progress_offset"):
            self._progress_offset = 0
            self._progress_tail = b""
        try:
            with self.logfile.open("rb") as log:
                log.seek(self._progress_offset)
                self._progress_tail += log.read()
                self._progress_offset = log.tell()
        except OSError:
            return False
        if b"[s3run] progress run " in self._progress_tail:
            return True
        self._progress_tail = self._progress_tail[-256:]
        return False
