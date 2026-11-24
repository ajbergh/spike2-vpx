"""Cabinet fixture detection and direct SPIKE 2 lighting for DOF Teensy firmware."""

from dataclasses import dataclass
from glob import glob
import os
from pathlib import Path
import re
import struct
import threading
import time
from xml.etree import ElementTree as ET

import cablights
import coilmap


ROLES = {"left": "Left blade", "right": "Right blade", "speaker": "Speaker lighting",
         "topper": "Topper lighting"}
ARRANGEMENT = re.compile(r"(LeftRight|RightLeft|TopDown|BottomUp)(Alternate)?"
                         r"(LeftRight|RightLeft|TopDown|BottomUp)$", re.I)
VERTICAL = ("topdown", "bottomup")
MAX_PIXELS = 65535
TEENSY_CHANNELS = 8


@dataclass(frozen=True)
class TeensyConfig:
    name: str
    port: str
    lengths: tuple
    baud: int
    dtr: bool


@dataclass(frozen=True)
class Fixture:
    name: str
    controller: str
    width: int
    height: int
    first: int
    order: str
    arrangement: str
    brightness: int
    issue: str = ""

    @property
    def count(self):
        return self.width * self.height

    def positions(self):
        """Physical pixel indices in visual order, bottom first for vertical strips."""
        fast, alternate, slow = ARRANGEMENT.fullmatch(self.arrangement).groups()
        vertical = fast.casefold() in VERTICAL
        fast_reversed = fast.casefold() in ("rightleft", "bottomup")
        slow_reversed = slow.casefold() in ("rightleft", "bottomup")
        fast_size, slow_size = ((self.height, self.width) if vertical else
                                (self.width, self.height))
        ys = range(self.height - 1, -1, -1) if self.width == 1 else range(self.height)
        positions = []
        for y in ys:
            for x in range(self.width):
                minor, major = (y, x) if vertical else (x, y)
                if slow_reversed:
                    major = slow_size - 1 - major
                if fast_reversed ^ bool(alternate and major % 2):
                    minor = fast_size - 1 - minor
                positions.append(self.first - 1 + major * fast_size + minor)
        return tuple(positions)


def xml_root(path):
    with Path(path).open("rb") as stream:
        data = stream.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("The DOF XML file is too large.")
    root = ET.fromstring(data)
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def detect(dof_dirs, table_dir=None):
    """Return the configured cabinet when it resolves to a single readable path."""
    candidates = []
    directories = list(dict.fromkeys(Path(d) for d in dof_dirs if d))
    if table_dir and not directories:
        directories.append(Path(table_dir))
    for dll_dir in directories:
        root = dll_dir.parent if dll_dir.name.lower() in ("x86", "x64") else dll_dir
        folders = [dll_dir / "Config", root / "Config"]
        if table_dir:
            folders.append(Path(table_dir))
        folders.extend((dll_dir, root))
        for folder in dict.fromkeys(folders):
            global_file = folder / "GlobalConfig_B2SServer.xml"
            if not global_file.is_file():
                continue
            try:
                pattern = xml_root(global_file).findtext("CabinetConfigFilePattern", "").strip()
                pattern = re.sub(r"\{(DllDir|BinDir|GlobalConfigDir)\}",
                                 lambda m: str(folder if m[1].lower() == "globalconfigdir" else
                                               dll_dir if m[1].lower() == "bindir" else root),
                                 os.path.expandvars(pattern), flags=re.I)
                if pattern:
                    path = folder / pattern
                    if path.is_file():
                        candidates.append(path.resolve())
                    else:
                        candidates.extend(Path(p).resolve() for p in glob(str(path)) if Path(p).is_file())
            except (ET.ParseError, OSError, ValueError):
                continue
            break
    if not candidates:
        for directory in directories:
            candidates.extend(path.resolve() for path in
                              (directory / "Config" / "cabinet.xml",
                               directory / "cabinet.xml") if path.is_file())
    paths = list(dict.fromkeys(candidates))
    return paths[0] if len(paths) == 1 else None


def read_cabinet(path):
    root = xml_root(path)
    if root.tag != "Cabinet":
        raise ValueError("Choose a DirectOutput Cabinet.xml file.")
    controllers, fixtures, unsupported = {}, {}, {}
    port_counts = {}
    for item in root.findall("./OutputControllers/*"):
        name = (item.findtext("Name") or "").strip()
        if not name:
            continue
        if name in controllers or name in unsupported:
            unsupported[name] = "Duplicate controller name"
            controllers.pop(name, None)
            continue
        if item.tag != "TeensyStripController":
            unsupported[name] = f"{item.tag} output is unavailable in this build"
            continue
        port = item.findtext("ComPortName", "").strip().upper()
        try:
            lengths = tuple(int(item.findtext(f"NumberOfLedsStrip{i}", "0"))
                            for i in range(1, TEENSY_CHANNELS + 1))
            baud = int(item.findtext("ComPortBaudRate", "9600"))
            if any(int(item.findtext(f"NumberOfLedsStrip{i}", "0")) for i in (9, 10)):
                unsupported[name] = "This build supports Teensy outputs 1–8"
                continue
            if (not re.fullmatch(r"COM[1-9][0-9]*", port) or not 0 < sum(lengths) <= MAX_PIXELS
                    or min(lengths) < 0 or max(lengths) * len(lengths) > MAX_PIXELS + 1
                    or baud <= 0 or item.findtext("ComPortParity", "None").strip() != "None"
                    or item.findtext("ComPortDataBits", "8").strip() != "8"
                    or item.findtext("ComPortStopBits", "One").strip() != "One"):
                raise ValueError()
        except ValueError:
            unsupported[name] = "Invalid or unsupported Teensy serial configuration"
            continue
        controllers[name] = TeensyConfig(name, port, lengths, baud,
                                        item.findtext("ComPortDtrEnable", "").strip().lower() == "true")
        port_counts[port] = port_counts.get(port, 0) + 1
    for name, controller in list(controllers.items()):
        if port_counts[controller.port] > 1:
            unsupported[name] = f"{controller.port} is configured more than once"
            del controllers[name]

    for item in root.findall("./Toys/*"):
        if item.tag not in ("LedStrip", "RGBLed", "RGBAToyGroup", "RGBLedGroup"):
            continue
        name = (item.findtext("Name") or "").strip()
        if not name:
            continue
        if name in fixtures:
            old = fixtures[name]
            fixtures[name] = Fixture(old.name, old.controller, old.width, old.height, old.first,
                                     old.order, old.arrangement, old.brightness, "Duplicate fixture name")
            continue
        controller = item.findtext("OutputControllerName", "").strip()
        order = item.findtext("ColorOrder", "RBG").strip().upper()
        if order == "WS2812":
            order = "GRB"
        arrangement = item.findtext("LedStripArrangement", "LeftRightTopDown").strip()
        issue = ""
        try:
            width = int(item.findtext("Width", "1"))
            height = int(item.findtext("Height", "1"))
            first = int(item.findtext("FirstLedNumber", "1"))
            brightness = int(item.findtext("Brightness", "100"))
            match = ARRANGEMENT.fullmatch(arrangement)
            if (width < 1 or height < 1 or not 0 < width * height <= MAX_PIXELS or first < 1
                    or sorted(order) != ["B", "G", "R"] or not 0 <= brightness <= 100
                    or not match or (match[1].casefold() in VERTICAL) == (match[3].casefold() in VERTICAL)):
                raise ValueError()
        except ValueError:
            width, height, first, brightness = 0, 0, 0, 100
            issue = "Invalid fixture dimensions, wiring order, or brightness"
        if item.tag != "LedStrip":
            issue = f"{item.tag} is detected but requires a DOF output adapter"
        elif not issue and controller not in controllers:
            issue = unsupported.get(controller, "Output controller is missing")
        elif not issue and first + width * height - 1 > sum(controllers[controller].lengths):
            issue = "Fixture extends beyond the controller's configured pixels"
        fixtures[name] = Fixture(name, controller, width, height, first, order, arrangement,
                                 brightness, issue)
    return controllers, fixtures


def suggest(fixture):
    words = set(re.findall(r"[a-z]+", fixture.name.lower()))
    if words & {"speaker", "speakers", "ring", "rings"}:
        return "speaker"
    if "topper" in words:
        return "topper"
    sides = words & {"left", "right"}
    if len(sides) == 1 and words & {"pf", "playfield", "blade", "side"}:
        return sides.pop()
    return ""


def assignments(fixtures, saved):
    """Validate each mapped fixture and prevent two writes to the same pixel."""
    used = {}
    result = []
    for name, entry in saved.items():
        if not isinstance(entry, dict) or not entry.get("role"):
            continue
        fixture = fixtures.get(name)
        if fixture is None:
            raise ValueError(f"The cabinet no longer contains {name}.")
        if fixture.issue:
            raise ValueError(f"{name}: {fixture.issue}.")
        role = entry["role"]
        offset, length = int(entry.get("offset", 0)), int(entry.get("length", 0))
        if role not in ROLES or offset < 0 or length < 0 or offset >= fixture.count:
            raise ValueError(f"Invalid assignment for {name}.")
        length = length or fixture.count - offset
        if offset + length > fixture.count:
            raise ValueError(f"The selected range extends beyond {name}.")
        pixels = fixture.positions()[offset:offset + length]
        if entry.get("reverse"):
            pixels = pixels[::-1]
        occupied = used.setdefault(fixture.controller, set())
        if occupied.intersection(pixels):
            raise ValueError(f"{name} overlaps another lighting assignment.")
        occupied.update(pixels)
        result.append((fixture, role, pixels))
    return result


def stern_sources(tables, *, topper_enabled=False, topper_off=None, include_optional_topper=False):
    tables = Path(tables)
    device = tables / "device_xy.txt"
    rows = coilmap.parse_rows(device.read_text(encoding="utf-8", errors="replace").splitlines())
    boards = cablights.boards(tables / "node_ident.txt")
    gone, optional_topper = set(), set()
    try:
        suffix = "" if topper_enabled else ".notopper"
        census = (tables / f"node_census{suffix}.txt").read_text().splitlines()
        for line in census:
            if line.startswith("silent="):
                gone.update(int(i) for i in re.findall(r"\d+", line.partition("=")[2]))
            elif line.startswith("topper="):
                optional_topper.update(int(i) for i in re.findall(r"\d+", line.partition("=")[2]))
    except OSError:
        pass
    if topper_off:
        try:
            optional_topper.update(int(i) for i in re.findall(r"\d+", Path(topper_off).read_text()))
        except OSError:
            pass
    gone = gone - optional_topper if include_optional_topper else gone | optional_topper
    sections, _ = cablights.sections(rows, coilmap.group_node_for(device, rows), boards, gone_nodes=gone)
    sources = {}
    for section in sections:
        pixels = section["pixels"]
        if section["role"] == cablights.EXPRESSION and len(section["bars"]) == 2:
            for role, bar in zip(("left", "right"), section["bars"]):
                sources.setdefault(role, []).extend(pixels[i] for i in bar)
        elif section["role"] in (cablights.SPEAKER, cablights.TOPPER):
            sources.setdefault(section["role"], []).extend(pixels)
    # Some metadata can name channels outside the shim's published banks.
    # Preserve those pixels' positions so the remaining pattern stays aligned.
    supported = {}
    for role, pixels in sources.items():
        available = [pixel if all(0 <= node < coilmap.NODES and 0 <= index < 96 + coilmap.HI_IDX
                                  for node, index in pixel["channels"].values()) else None
                     for pixel in pixels]
        if any(available):
            supported[role] = available
    return supported


def frame(data, sources, mappings, controllers, brightness):
    buffers = {name: bytearray(sum(c.lengths) * 3) for name, c in controllers.items()}
    colors = {}
    for fixture, role, pixels in mappings:
        if role not in colors:
            levels = []
            for pixel in sources.get(role, ()):
                if pixel is None:
                    levels.append((0, 0, 0))
                    continue
                channels = {c: coilmap.strip_level(data, *addr)
                            for c, addr in pixel["channels"].items()}
                if None in channels.values():
                    return None
                white = channels.get("W", 0)
                tint = cablights.named_colour(pixel["name"]) or (255, 255, 255)
                levels.append(tuple(max(channels.get(c, 0), white * t // 255)
                                    for c, t in zip("RGB", tint)))
            colors[role] = levels
        levels = colors[role]
        if not levels:
            continue
        buffer = buffers[fixture.controller]
        scale = brightness * fixture.brightness
        for i, position in enumerate(pixels):
            pixel = levels[round(i * (len(levels) - 1) / max(1, len(pixels) - 1))]
            buffer[position * 3:position * 3 + 3] = bytes(
                pixel["RGB".index(c)] * scale // 10_000 for c in fixture.order)
    return buffers


def settings(raw):
    return raw if isinstance(raw, dict) else {}


def prepare(raw, dof_dirs, table_dir=None):
    path = raw.get("path") or detect(dof_dirs, table_dir)
    if not path:
        raise ValueError("Select your Cabinet.xml on the Lighting tab.")
    if raw.get("detected_path") and Path(path).resolve() != Path(raw["detected_path"]).resolve():
        raise ValueError("The detected cabinet file changed. Review the assignments on the Lighting tab.")
    controllers, fixtures = read_cabinet(path)
    mappings = assignments(fixtures, settings(raw.get("assignments")))
    if not mappings:
        raise ValueError("Assign at least one fixture on the Lighting tab.")
    brightness = int(raw.get("brightness", 50))
    if not 1 <= brightness <= 100:
        raise ValueError("Lighting brightness must be between 1 and 100.")
    return controllers, mappings, brightness


class SerialPort:
    """The bundled pywin32 runtime provides an exclusive native COM handle."""

    def __init__(self, config):
        import pywintypes
        import win32file

        self.api = win32file
        self.error = pywintypes.error
        self.handle = win32file.CreateFile(
            rf"\\.\{config.port}", win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None, win32file.OPEN_EXISTING, 0, None)
        try:
            dcb = win32file.GetCommState(self.handle)
            dcb.BaudRate, dcb.ByteSize, dcb.Parity, dcb.StopBits = config.baud, 8, 0, 0
            dcb.fBinary, dcb.fParity = 1, 0
            dcb.fDtrControl, dcb.fRtsControl = int(config.dtr), 0
            dcb.fOutxCtsFlow = dcb.fOutxDsrFlow = dcb.fDsrSensitivity = 0
            dcb.fOutX = dcb.fInX = dcb.fNull = dcb.fAbortOnError = 0
            win32file.SetCommState(self.handle, dcb)
            win32file.SetCommTimeouts(self.handle, (0, 0, 150, 0, 150))
            win32file.PurgeComm(self.handle, win32file.PURGE_RXCLEAR | win32file.PURGE_TXCLEAR)
        except Exception:
            self.close()
            raise

    def write(self, data):
        try:
            _, written = self.api.WriteFile(self.handle, data)
        except self.error as exc:
            raise OSError(str(exc)) from exc
        if written != len(data):
            raise OSError("The Teensy serial write timed out.")

    def read(self, count):
        try:
            return self.api.ReadFile(self.handle, count)[1]
        except self.error as exc:
            raise OSError(str(exc)) from exc

    def close(self):
        self.handle.Close()


class Teensy:
    """DOF's Teensy firmware protocol with acknowledgement on each command."""

    def __init__(self, config, open_port=SerialPort):
        self.port = open_port(config)
        self.config = config
        self.stride = max(config.lengths)
        self.last = None
        try:
            time.sleep(0.05)
            for _ in range(4):
                self.port.write(b"\0")
                if self.port.read(1) in (b"A", b"N"):
                    break
            else:
                raise OSError("The controller did not answer the Teensy handshake.")
            self.port.write(b"M")
            reply = self.port.read(3)
            if len(reply) != 3 or reply[-1:] != b"A":
                raise OSError("The controller did not report its channel capacity.")
            if self.stride > int.from_bytes(reply[:2], "big"):
                raise ValueError("Configured strip length exceeds Teensy capacity.")
            self.command(b"L" + struct.pack(">H", self.stride))
            self.clear()
        except Exception:
            self.port.close()
            raise

    def command(self, payload):
        self.port.write(payload)
        if self.port.read(1) != b"A":
            raise OSError("The Teensy did not acknowledge a lighting command.")

    def send(self, buffer):
        if buffer == self.last:
            return
        start = 0
        for channel, length in enumerate(self.config.lengths):
            if length:
                end = start + 3 * length
                if self.last is None or buffer[start:end] != self.last[start:end]:
                    self.command(b"R" + struct.pack(">HH", channel * self.stride, length) +
                                 buffer[start:end])
                start = end
        self.command(b"O")
        self.last = bytes(buffer)

    def clear(self):
        self.command(b"C")
        self.command(b"O")
        self.last = bytes(3 * sum(self.config.lengths))

    def close(self):
        try:
            self.clear()
        finally:
            self.port.close()


def test_fixture(controller, fixture, pixels):
    device = Teensy(controller)
    try:
        buffer = bytearray(sum(controller.lengths) * 3)
        steps = min(len(pixels), 12)
        for c in "RGB":
            for step in range(steps):
                buffer[:] = bytes(len(buffer))
                position = pixels[round(step * (len(pixels) - 1) / max(1, steps - 1))]
                buffer[position * 3 + fixture.order.index(c)] = 40
                device.send(buffer)
                time.sleep(0.045 if len(pixels) > 1 else 0.25)
    finally:
        device.close()


class Session:
    """Own the selected serial ports before VPX starts and until its process exits."""

    def __init__(self, configs, mappings, sources, brightness, note, open_device=Teensy):
        self.mappings = [(f, role, pixels) for f, role, pixels in mappings if sources.get(role)]
        self.configs = {f.controller: configs[f.controller] for f, _, _ in self.mappings}
        self.sources, self.brightness, self.note = sources, brightness, note
        self.devices = {}
        self.stop = threading.Event()
        self.lock = threading.Lock()
        try:
            for name, config in self.configs.items():
                try:
                    self.devices[name] = open_device(config)
                except Exception as exc:
                    raise OSError(f"{config.port}: {exc}") from exc
        except Exception:
            self.close()
            raise

    def start(self, read, alive):
        threading.Thread(target=self._run, args=(read, alive), daemon=True,
                         name="stern-lighting").start()

    def _run(self, read, alive):
        last_data = last_valid = None
        rendered = previous = None
        failed = set()
        try:
            while not self.stop.is_set() and alive():
                data = read()
                now = time.monotonic()
                if coilmap.has_magic(data):
                    if data != last_data:
                        last_data = data
                        rendered = frame(data, self.sources, self.mappings, self.configs, self.brightness)
                    if rendered is not None:
                        with self.lock:
                            if self.stop.is_set():
                                return
                            if rendered is not previous:
                                for name, device in self.devices.items():
                                    if name not in failed:
                                        try:
                                            device.send(rendered[name])
                                        except OSError as exc:
                                            failed.add(name)
                                            self.note(f"Expression output stopped for {name}: {exc}")
                                previous = rendered
                        last_valid = now
                else:
                    rendered = last_data = None
                if last_valid is not None and now - last_valid > 1:
                    with self.lock:
                        self._clear()
                    last_valid = previous = None
                self.stop.wait(1 / 50)
        except Exception as exc:
            self.note(f"Expression lighting stopped: {exc}")
        finally:
            with self.lock:
                # Keep the COM handle reserved until VPX exits, including after a feed loss.
                if not self.stop.is_set():
                    self._clear()

    def _clear(self):
        for device in self.devices.values():
            try:
                device.clear()
            except Exception as exc:
                self.note(f"Could not clear expression lighting: {exc}")

    def close(self):
        self.stop.set()
        with self.lock:
            for name, device in self.devices.items():
                try:
                    device.close()
                except Exception as exc:
                    self.note(f"Could not clear or close {name}: {exc}")
            self.devices.clear()
