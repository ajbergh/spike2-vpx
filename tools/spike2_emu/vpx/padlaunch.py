#!/usr/bin/env python3
"""padlaunch.py - start a Spike 2 run for a Visual Pinball table, and stop it.

Visual Pinball opens a patched table, the table creates PAD.Controller and
names its title, and the controller looks for a run of that title on the rig
board. When there is none, this starts one - no app in the loop:

  * the CARD for the title: PAD_VPX_CARD, else the card `padvpx patch --card`
    (or `padvpx card`) recorded for the title in the app’s vpx.json, else the
    newest `<title>-*.sdcard.raw` in the kit's library directory
    (PAD_VPX_LIBRARY or the kit's `library`);
  * the Linux it runs in: PAD_WSL_DISTRO, else the kit's `distro`, else
    PAD-Runtime when that distro is installed, else the machine's default;
  * a rig slot of its own, taken with riglock.sh (`PAD-VPX` as its holder),
    so the app's and a session's rigs are never disturbed - on the app's own
    board (.pad-rig in the Windows profile, where the controller looks for
    the run)
    and in the app's own rig (its data disk, when the app keeps one);
  * `watch.sh` as the desktop user, VISIBLE (the LCD window on the desktop)
    and with SOUND, since this run is for playing, and the Windows Python
    beside this one for the sound bridge (PAD-95) when there is one.

What this started, Controller.Stop stops (killgame.sh, then the slot is
released); a run the user started another way is left alone.
"""
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.dirname(HERE)
if RIG not in sys.path:
    sys.path.insert(0, RIG)

import padpath    # noqa: E402
from padloading import COPYING  # noqa: E402

DATA_ROOT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Spike2Vpx")
KIT = os.path.join(DATA_ROOT, "vpx.json")
USER_MAPS = os.path.join(DATA_ROOT, "maps")
LOG = os.path.join(DATA_ROOT, "logs", "play", "controller.log")
BOARD = os.path.join(DATA_ROOT, "rig-board")
LABEL = "PAD-VPX"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s launch: %s\n" % (time.strftime("%H:%M:%S"), msg))
    except OSError:
        pass


# ---------------------------------------------------------------- the kit --

def kit_load():
    try:
        with open(KIT, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def kit_save(d):
    os.makedirs(os.path.dirname(KIT), exist_ok=True)
    tmp = KIT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, sort_keys=True)
    os.replace(tmp, KIT)


def remember_card(title, card):
    d = kit_load()
    d.setdefault("cards", {})[title] = card
    kit_save(d)


#: PAD-964: whether a run copies its card into the emulator's cache first
#: (the kit's `card_cache`, on unless turned off: `padvpx cache off`, the
#: Visual Pinball tab's box). Off, the game reads the image where it lies
#: (PAD_CARD_CACHE=0): nothing lands on the disk, and a first start is slow -
#: Batman 1.14 took 148 s to attract off D: through 9p against 36 s cached.
CACHE_KEY = "card_cache"


def cache_on(kit=None):
    kit = kit_load() if kit is None else kit
    return kit.get(CACHE_KEY, True) is not False


def set_cache_on(on):
    d = kit_load()
    if on:
        d.pop(CACHE_KEY, None)
    else:
        d[CACHE_KEY] = False
    kit_save(d)
    return cache_on(d)


def gb(kb):
    """`6.6 GB` for a size in KB, as the tab and the loading window say it."""
    return "%.1f GB" % (float(kb) / 1048576.0)


def parse_check(text):
    """`cardmount.sh --cache-check` read: ({state, need_kb, free_kb, keep_kb,
    evict_n, evict_kb, kind, host, label, img} per `check` line, keyed by
    image path, and the `cache` line as {dir, kind, avail_kb, size_kb,
    limit_gb, used_kb} under "cache"). Lines it does not know are skipped."""
    out = {"checks": {}, "cache": None}
    for line in (text or "").splitlines():
        p = line.rstrip("\r\n").split("\t")
        try:
            if p[0] == "check" and len(p) >= 11:
                out["checks"][p[10]] = {
                    "state": p[1], "need_kb": int(p[2] or 0), "free_kb": int(p[3] or 0),
                    "keep_kb": int(p[4] or 0), "evict_n": int(p[5] or 0), "evict_kb": int(p[6] or 0),
                    "kind": p[7], "host": p[8], "label": p[9], "img": p[10]}
            elif p[0] == "cache" and len(p) >= 7:
                out["cache"] = {"dir": p[1], "kind": p[2], "avail_kb": int(p[3] or 0),
                                "size_kb": int(p[4] or 0), "limit_gb": int(p[5] or 0),
                                "used_kb": int(p[6] or 0)}
        except ValueError:
            continue
    return out


def space_words(chk):
    """What starting the game costs the disk, in one sentence, from a `check`
    record: the launch's log line, the loading window's step and the tab's
    tip say the same thing. (state, words)."""
    state = chk.get("state") or ""
    need, free = chk.get("need_kb") or 0, chk.get("free_kb") or 0
    where = "the Windows drive behind WSL (%s)" % chk["host"] if chk.get("host") else "the cache"
    if state == "cached":
        words = "The card image is in the emulator's cache"
    elif state == "off":
        words = ("Caching is off: the game reads the card image (%s) where it lies, a slow start"
                 % gb(need))
    elif state == "copying":
        words = "The card image (%s) is being copied into the emulator's cache now" % gb(need)
    elif state == "room":
        words = ("Copying the card image (%s) into the emulator's cache, this first start only (%s free there)"
                 % (gb(need), gb(free)))
    elif state == "evict":
        n = chk.get("evict_n") or 0
        words = ("Copying the card image (%s) into the emulator's cache once %d older card%s (%s) make%s room, "
                 "this first start only" % (gb(need), n, "" if n == 1 else "s", gb(chk.get("evict_kb") or 0),
                                             "s" if n == 1 else ""))
    elif state == "full":
        words = ("No room in %s for the card image (%s needed with %s kept free, %s free): the game reads it "
                 "where it lies, a slow start" % (where, gb(need), gb(chk.get("keep_kb") or 0), gb(free)))
    elif state == "unavailable":
        words = "The selected Windows cache folder is unavailable. The game reads the source image directly"
    else:
        words = "What the card image costs the disk could not be told"
    return state, words


def _version_key(path):
    """`rush_le-1_19_0.Release.16G.sdcard.raw` -> (1, 19, 0): newest last.
    A Spike 1 card's two parts (`GOT_LE-1_37.iso`) or dots
    (`transformers_pin-1.0.18.iso`) read the same way (PAD-943)."""
    m = re.search(r"-(\d+)[._](\d+)(?:[._](\d+))?", os.path.basename(path))
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


RUNTIME_DISTRO = "SPIKE2-VPX"


def read_table_map(path):
    """Read a map, optionally sharing the wiring from a sibling shadow map."""
    with open(path, encoding="utf-8") as file:
        mapping = json.load(file)
    if not isinstance(mapping, dict):
        raise ValueError("Expected a VPX map object.")
    base = mapping.pop("base_map", None)
    if base is None:
        return mapping
    if not isinstance(base, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+\.shadow\.json", base):
        raise ValueError("A shared VPX map must name a sibling shadow map.")
    with Path(path).with_name(base).open(encoding="utf-8") as file:
        wiring = json.load(file)
    if not isinstance(wiring, dict) or "base_map" in wiring:
        raise ValueError("A shared VPX base map must contain its own wiring.")
    return {**wiring, **mapping}


def table_map(title):
    """Map metadata in PAD.Controller's user/kit lookup order."""
    if title:
        for directory in (USER_MAPS, HERE):
            for suffix in (".vpxmap.json", ".shadow.json"):
                try:
                    m = read_table_map(os.path.join(directory, title + suffix))
                    if (m.get("platform") or "spike2").lower() == "spike2":
                        return m
                except (OSError, ValueError):
                    pass
    return {}


def encoder_lamp_env(mapping):
    """Map's optional encoder_lamps -> the responder's adapter wiring."""
    entries = []
    for motor, led in mapping.items():
        if motor == "_":
            continue
        node, index = (int(n) for n in motor.split(","))
        if (not isinstance(led, list) or len(led) != 2 or any(type(n) is not int for n in led)
                or not (0 <= node < 16 and 0 <= index < 256 and 0 <= led[0] < 16 and 0 <= led[1] < 64)):
            raise ValueError("encoder_lamps needs node,motor -> [node, LED] within Spike 1 slots")
        entries.append("%d,%d:%d,%d" % (node, index, *led))
    return ";".join(entries)


def game_for(title):
    """A table variant's native game; maps without game keep their title."""
    game = table_map(title).get("game")
    return game if isinstance(game, str) and game else title


def card_for(title, platform="spike2"):
    """Find the SPIKE 2 card image for this title."""
    env = os.environ.get("PAD_VPX_CARD")
    if env and os.path.isfile(env):
        return env
    kit = kit_load()
    game = game_for(title)
    for key in dict.fromkeys((title, game)):
        card = (kit.get("cards") or {}).get(key)
        if card and os.path.isfile(card):
            return card
    for lib in libraries(platform, kit):
        pattern = "%s-*.sdcard.raw" % game.lower()
        found = [os.path.join(lib, name) for name in sorted(os.listdir(lib))
                 if fnmatch.fnmatchcase(name.lower(), pattern)]
        if found:
            # of one version, the stock Release build over a custom one beside
            # it (turtles_pro-1_59_0.1987-upscaled tied with the Release and
            # was taken - PAD-811); `padvpx card` names a custom one
            return max(found, key=lambda p: (_version_key(p), ".Release." in os.path.basename(p)))
    return None


def libraries(platform="spike2", kit=None):
    """Return the configured SPIKE 2 card folders in search order."""
    kit = kit_load() if kit is None else kit
    out = []
    env = os.environ.get("PAD_VPX_LIBRARY")
    if env:
        out.append(env)
    per = (kit.get("libraries") or {}).get(platform)
    if per:
        out.append(per)
    lib = kit.get("library")
    if lib:
        out.append(lib)
    return [d for d in out if os.path.isdir(d)]


def card_places(title, platform="spike2"):
    kit = kit_load()
    libs = libraries(platform, kit)
    return ("PAD_VPX_CARD, vpx.json cards[%s]%s, the librar%s %s"
            % (title, "" if not (kit.get("cards") or {}).get(title) else " (missing file)",
               "y" if len(libs) < 2 else "ies", ", ".join(libs) if libs else
               os.environ.get("PAD_VPX_LIBRARY") or kit.get("library") or "(none set)"))


def distros():
    try:
        r = subprocess.run(["wsl.exe", "-l", "-q"], stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                           creationflags=_CREATE_NO_WINDOW)
    except Exception:                                       # noqa: BLE001
        return []
    if r.returncode != 0:
        # with no WSL (or no distro) its lines are the complaint, not names
        return []
    out = r.stdout
    text = out.decode("utf-16-le", "replace") if b"\x00" in out else out.decode("utf-8", "replace")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def distro():
    """Which WSL distro runs the game: the env, the kit, our runtime, default."""
    env = os.environ.get("PAD_WSL_DISTRO")
    if env:
        return env
    kit = kit_load().get("distro")
    if kit:
        return kit
    if RUNTIME_DISTRO in distros():
        return RUNTIME_DISTRO
    return None


#: What Play and the launcher say when this PC has no Linux to run the game
#: in. It used to come out as "no rig slot free for <title>: <wsl.exe's own
#: words>", which read like a game already running (PAD-806).
NO_LINUX = "Open SPIKE 2 VPX and complete Setup before launching a game."


def registered():
    """The WSL distros Windows has registered (HKCU ...\\Lxss), read without
    wsl.exe: [] when there is no Lxss key at all (WSL never set up), None
    off Windows or when the registry cannot be read (then nobody knows, and
    nothing is refused on it)."""
    try:
        import winreg
    except ImportError:
        return None
    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Lxss") as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as dk:
                        names.append(winreg.QueryValueEx(dk, "DistributionName")[0])
                except OSError:
                    continue
    except FileNotFoundError:
        return []
    except OSError:
        return None
    return names


def no_linux(dist=None):
    """Why a run cannot start in `dist` (None: the machine's default) before
    any wsl.exe is asked, or "" when it can - or when that cannot be told."""
    names = registered()
    if names is None:
        return ""
    if not names:
        return NO_LINUX
    if dist and dist.lower() not in [n.lower() for n in names]:
        return ("The game cannot start: the Linux it runs in (%s) is not installed "
                "on this PC. Complete Setup in SPIKE 2 VPX, then play again." % dist)
    return ""


def rig_dir():
    """The rig (watch.sh and friends) as Windows sees it: PAD_VPX_RIG (a
    checkout's rig under test, PAD-943), the kit's `rig` (an installed app's
    tools/spike2_emu), else the one beside this file."""
    return os.environ.get("PAD_VPX_RIG") or kit_load().get("rig") or RIG


def win_python():
    """`python.exe` beside the interpreter running us, for the sound bridge."""
    if sys.platform != "win32":
        return None
    d = os.path.dirname(sys.executable)
    for name in ("python.exe",):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def wsl_head(dist=None, root=False):
    head = ["wsl.exe"]
    if dist:
        head += ["-d", dist]
    if root:
        head += ["-u", "root"]
    return head


def host_drive(dist):
    """Find the Windows drive backing this distro for the card-cache disk check."""
    if sys.platform != "win32" or not dist:
        return ""
    app = os.path.dirname(os.path.dirname(RIG))
    if app not in sys.path:
        sys.path.append(app)
    from spike2_vpx.wsl_up import distribution_record

    try:
        record = distribution_record(dist)
        letter = os.path.splitdrive(record[0])[0] if record else ""
        return padpath.to_wsl(letter + "\\", dist).rstrip("/") if letter else ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        log("Could not find the distro's host drive: %s" % exc)
        return ""


_ACCOUNT = {}


def account(dist=None):
    """(user, home) of the account wsl.exe logs into with no -u, asked of
    `whoami` and `getent` (no shell variables: wsl.exe re-parses its line and
    a `$HOME` comes out empty). ('', '') when WSL does not answer."""
    if dist in _ACCOUNT:
        return _ACCOUNT[dist]
    user = home = ""
    try:
        user = _text(_run(wsl_head(dist) + ["-e", "whoami"], timeout=30).stdout).strip()
        if user:
            row = _text(_run(wsl_head(dist) + ["-e", "getent", "passwd", user], timeout=30).stdout)
            parts = row.strip().split(":")
            if len(parts) >= 6:
                home = parts[5]
    except Exception:                                       # noqa: BLE001
        pass
    _ACCOUNT[dist] = (user, home)
    return _ACCOUNT[dist]


def _run(args, timeout=60):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                          creationflags=_CREATE_NO_WINDOW)


def _text(b):
    # wsl.exe's own complaints (no distro, WSL not answering) are UTF-16;
    # what bash prints is UTF-8 and never carries a NUL
    b = b or b""
    return b.decode("utf-16-le" if b"\x00" in b else "utf-8", "replace")


# ------------------------------------------------------------- the launch --

class Launch:
    """One run started for a table: a slot, a watch.sh, and the stop for it."""

    def __init__(self, title, card, dist=None, minutes=240, *, game_dir=None, prepared=False,
                 audio_control=None, cache_update=None, activity=None):
        self.title = game_for(title)
        self.card = card
        self.game_dir = game_dir
        self.audio_control = audio_control
        self.cache_update = cache_update
        self.activity = activity
        self.cache_log_offset = None
        self.windows_lcd = None
        self.lighting_topper = False
        self.lighting_scan = ""
        self.prepared = prepared or bool(game_dir)
        self.label = "%s-%d" % (LABEL, os.getpid()) if self.prepared else LABEL
        self.distro = dist if dist is not None else distro()
        self.minutes = minutes
        self.slot = None
        self.proc = None
        self.as_root = False
        # every path below is for THIS run's distro, so that distro converts
        # it, not the machine's default (PAD-882)
        # Prepared launches need this kit's support for an explicit directory title.
        self.rig_wsl = padpath.to_wsl(RIG if game_dir else rig_dir(), self.distro)
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        self.logfile = os.path.join(os.path.dirname(LOG), "padvpx-%s.log" % title)
        self.check = None       # cache_check(): what the card costs the disk (PAD-964)

    def rig_cmd(self, script, env=()):
        """How a rig script is called from here: as root, the way the run
        itself starts (head()), on the app's board and in the app's rig,
        `env` entries before it. Root because the board may be root's already
        (the app's runs are root) and because taking a slot >= 1 also mounts
        its rootfs, which only root can (riglock.sh, "RUN IT AS ROOT"). A
        distro whose account has no home gets the plain user, as head()
        does."""
        user, home = account(self.distro)
        if home and user:
            head = wsl_head(self.distro, root=True) + ["-e", "env", "HOME=" + home]
        else:
            head = wsl_head(self.distro) + ["-e", "env"]
        return head + self.board_env() + list(env) + ["bash", "%s/%s" % (self.rig_wsl, script)]

    def rig_head(self):
        """How every riglock.sh call starts (rig_cmd)."""
        return self.rig_cmd("riglock.sh")

    def cache_env(self):
        """Use the same cache and disk-space rules for preflight and launch."""
        if not cache_on():
            return ["PAD_CARD_CACHE=0"]
        if sys.platform == "win32" and (self.distro or "").casefold() == RUNTIME_DISTRO.casefold():
            app = Path(RIG).parents[1]
            if str(app) not in sys.path:
                sys.path.append(str(app))
            from spike2_vpx.storage import cache_folder

            folder = cache_folder(app)
            if folder is not None:
                cache = padpath.to_wsl(str(folder), self.distro)
                drive = padpath.to_wsl(folder.anchor, self.distro).rstrip("/")
                return ["PAD_CARD_CACHE_DIR=" + cache, "PAD_CARD_CACHE_DRIVE=" + drive]
        drive = host_drive(self.distro)
        return ["PAD_HOST_DRIVE=" + drive] if drive else []

    def cache_check(self, cards=None):
        """What the card (or each of `cards`) costs the disk before the run:
        `cardmount.sh --cache-check`, a dry run of the copier's own room rule
        in the run's distro and rig, nothing copied (PAD-964). The record for
        this run's card (parse_check), or None when WSL did not answer - a
        start is never refused on it. With `cards`, every record by image path
        and the cache's own line: {"checks", "cache"}."""
        imgs = padpath.to_wsl_many(list(cards) if cards is not None else [self.card], self.distro)
        cmd = self.rig_cmd("cardmount.sh", self.cache_env()) + [imgs[0], "--cache-check"] + imgs[1:]
        try:
            r = _run(cmd, timeout=120)
        except Exception as exc:                            # noqa: BLE001
            log("cache check not answered: %s" % exc)
            return None
        got = parse_check(_text(r.stdout))
        if not got["checks"]:
            log("cache check said nothing (%s): %s" % (r.returncode, (_text(r.stderr) or "").strip()[-200:]))
            return None
        if cards is not None:
            return got
        return got["checks"].get(imgs[0]) or next(iter(got["checks"].values()))

    def note(self, line):
        """A line of this run's own log (the loading window reads it)."""
        try:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def board_env(self):
        """Share the Windows rig board with the WSL run."""
        if sys.platform == "win32":
            return ["PAD_BOARD=%s" % padpath.to_wsl(BOARD, self.distro)]
        return []

    def take_slot(self):
        """A slot of the kit's own: the kit's `slot` when it names one (`use`
        keeps it if it is already PAD-VPX's, takes it if free), else the first
        free one."""
        want = None if self.prepared else kit_load().get("slot")
        what = "%s for Visual Pinball" % self.title
        if want:
            cmd = self.rig_head() + ["use", str(int(want)), self.label, what]
        else:
            cmd = self.rig_head() + ["take", "--any", self.label, what]
        r = _run(cmd)
        out = _text(r.stdout) + _text(r.stderr)
        m = re.search(r"slot=(\d+)", out)
        if want and r.returncode == 0 and not m:
            self.slot = int(want)
            return self.slot
        if want and not m:
            # the kit's slot is somebody else's now: any free one, then
            log("slot %s is not ours (%s); taking any" % (want, out.strip()[-120:]))
            r = _run(self.rig_head() + ["take", "--any", self.label, what])
            out = _text(r.stdout) + _text(r.stderr)
            m = re.search(r"slot=(\d+)", out)
        if not m:
            # the LAST lines say why (riglock.sh's own word), the slot table
            # above them only what it saw
            raise RuntimeError("no rig slot free for %s: %s" % (self.title, out.strip()[-300:]))
        self.slot = int(m.group(1))
        if self.prepared and r.returncode:
            raise RuntimeError("could not prepare rig slot %d: %s" % (self.slot, out.strip()[-300:]))
        return self.slot

    def env(self):
        env = self.board_env() + ["PAD_SLOT=%d" % self.slot, "PAD_LABEL=%s" % self.label,
                                  "PAD_HIDDEN=0", "PAD_AUDIO=1", "PAD_PLAYFIELD=0"]
        if self.lighting_scan:
            env.append("PAD_LIGHTING_SCAN=" + self.lighting_scan)
        if self.audio_control:
            env.append("PAD_AUDIO_CTL=" + self.audio_control)
        if self.windows_lcd:
            x, y, right, bottom = self.windows_lcd
            env.extend(["PAD_WINDOWS_LCD=1", "PAD_WINDOWS_LCD_W=%d" % (right - x),
                        "PAD_WINDOWS_LCD_H=%d" % (bottom - y),
                        "PAD_WINDOWS_LCD_FRAME=" + self.lcd_frame_path().name])
        if self.prepared:
            env += ["PAD_SELECT=0", "PAD_TOPPER=%d" % self.lighting_topper, "PAD_BALL_FEED=0",
                     "PAD_AUTO_ATTRACT=0", "PAD_RECORD=0", "PAD_PIVOT=", "PAD_SAVESTATES=0",
                     "PAD_VPX_PERSIST=1"]
            if self.lighting_topper:
                env.append("PAD_GL2_OFF=1")
            if self.game_dir:
                return env + ["PAD_GAME_DIR=" + self.game_dir, "PAD_GAME_DIR_TITLE=" + self.title,
                              "PAD_CARD="]
        card = padpath.to_wsl(self.card, self.distro)
        env.append("PAD_CARD=%s" % card)
        env += self.cache_env()
        py = win_python()
        if py:
            env.append("PAD_WINPYTHON=%s" % padpath.to_wsl(py, self.distro))
        return env

    def head(self):
        """The app's own Windows launch shape (emulate_core.watch_cmd): `wsl
        -u root` with the desktop user's HOME and PAD_PIVOT=1, which boots
        the guest as root (checkpointable: save states work) and drops every
        helper back to the user, whose WSLg and sound session they need. A
        distro whose account has no home gets the plain user launch."""
        user, home = account(self.distro)
        if home and user:
            return wsl_head(self.distro, root=True) + ["-e", "env", "HOME=" + home, "PAD_PIVOT=1"], True
        return wsl_head(self.distro) + ["-e", "env"], False

    def base_rig(self):
        """The ordinary rig every slot is layered over (padpath.sh's
        PAD_BASE_ROOT: $PAD_HOME/spike2root), or "" when it cannot be told."""
        _, home = account(self.distro)
        return home + "/spike2root" if home else ""

    def start(self):
        why = no_linux(self.distro)
        if why:
            log(why)
            raise RuntimeError(why)
        if self.prepared and self.distro == "SPIKE2-VPX":
            if self.activity:
                self.activity("Checking ARM execution in the SPIKE 2 runtime…")
            cmd = wsl_head(self.distro, root=True) + ["-e", "bash", self.rig_wsl + "/vpx_arm_exec.sh", "repair"]
            self.note("[setup] Checking ARM execution in the SPIKE 2 runtime.")
            with open(self.logfile, "ab") as out:
                result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                        timeout=30, creationflags=_CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("WSL could not start the ARM guest. Open Setup and run Repair. See " + self.logfile)
        base = self.base_rig()
        if base:
            if self.activity:
                self.activity("Preparing the SPIKE 2 filesystem…")
            card = padpath.to_wsl(self.card, self.distro) if self.card else ""
            cmd = wsl_head(self.distro) + [
                "-e", "bash", self.rig_wsl + "/prepare_vpx_root.sh", base, card,
            ]
            self.note("[setup] Checking the SPIKE 2 filesystem.")
            with open(self.logfile, "ab") as out:
                result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                        timeout=900, creationflags=_CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("Could not prepare the SPIKE 2 filesystem. See " + self.logfile)
        if self.game_dir:
            self._start_watch()
            return self.slot
        # PAD-964: what the card costs the disk, said in the run's log before
        # anything is copied - the loading window shows it, and a start with
        # no room for the copy (the game reads the image where it lies, 2.5
        # minutes to attract instead of 40 s) says so instead of "Reading the
        # card image" for all of them. Never a refusal: a slow game beats none.
        if self.activity:
            self.activity("Checking space for the SD-card cache…")
        self.check = self.cache_check()
        copying = False
        if self.check:
            state, words = space_words(self.check)
            self.note("[card-check] %s: %s" % (state, words))
            log("card check for %s: %s: %s" % (self.title, state, words))
            copying = state in ("room", "evict", "copying")
            if self.cache_update and copying:
                self.cache_update("Caching the SD-card image. This launch may take longer than usual.")
        if self.activity:
            self.activity("Preparing the SD-card cache…" if copying else "Starting Stern…")
        self._start_watch()
        log("started %s on slot %d from %s (distro %s, log %s)"
            % (self.title, self.slot, self.card, self.distro or "default", self.logfile))
        return self.slot

    def lcd_frame_path(self):
        return Path(BOARD) / f"lcd-frame-{self.slot}-{self.label}.txt"

    def _start_watch(self):
        head, self.as_root = self.head()
        if self.slot is None:
            self.take_slot()
        if self.windows_lcd:
            self.lcd_frame_path().unlink(missing_ok=True)
        cmd = head + self.env() + ["bash", "%s/watch.sh" % self.rig_wsl, str(self.minutes)]
        with open(self.logfile, "ab") as out:
            if self.activity and self.card:
                self.cache_log_offset = out.tell()
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                         creationflags=_CREATE_NO_WINDOW)

    def poll_cache_progress(self):
        if self.cache_log_offset is None:
            return
        latest = None
        try:
            with open(self.logfile, "rb") as stream:
                stream.seek(self.cache_log_offset)
                for raw in stream:
                    if not raw.endswith(b"\n"):
                        break  # Wait for the writer to finish the line.
                    self.cache_log_offset = stream.tell()
                    line = raw.decode("utf-8", "replace")
                    if copy := COPYING.match(line):
                        done, total, _ = map(int, copy.groups())
                        latest = ("Copying the SD-card image into the cache…", done * 2**20, total * 2**20)
                    elif line.startswith(("[card] local cache ready", "[card] using local cache")):
                        latest = ("Starting Stern from the local cache…",)
                    elif line.startswith(("[card] copy stalled", "[card] cache not usable", "[card] disk too full")):
                        latest = ("Reading the SD-card image from its source…",)
        except OSError:
            return
        if latest:
            self.activity(*latest)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.slot is None:
            return
        try:
            head, _ = self.head() if getattr(self, "as_root", False) else (wsl_head(self.distro) + ["-e", "env"], False)
            _run(head + self.board_env() + ["PAD_SLOT=%d" % self.slot, "PAD_LABEL=%s" % self.label,
                                            "bash", "%s/killgame.sh" % self.rig_wsl], timeout=120)
            _run(self.rig_head() + ["release", str(self.slot), self.label], timeout=30)
            log("stopped %s on slot %d" % (self.title, self.slot))
        except Exception as exc:                            # noqa: BLE001
            log("stop of slot %d failed: %s" % (self.slot, exc))
        if self.proc is not None:
            try:
                self.proc.wait(timeout=10)
            except Exception:                               # noqa: BLE001
                pass
        if self.windows_lcd:
            try:
                self.lcd_frame_path().unlink(missing_ok=True)
            except OSError as exc:
                log("LCD frame cleanup failed: %s" % exc)
        self.slot = None
