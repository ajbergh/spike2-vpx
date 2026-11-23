"""Setup checks and actions for the standalone Windows player."""

from dataclasses import dataclass
from pathlib import Path
import base64
import json
import logging
import os
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile

from spike2_vpx import payloads, runtime, wsl_up

APP = Path(__file__).resolve().parent
RIG = APP / "tools" / "spike2_emu"
KIT = RIG / "vpx"
sys.path.insert(0, str(KIT))
import vpxsetup  # noqa: E402

DISTRO = "SPIKE2-VPX"
VPX_RELEASE = "https://github.com/vpinball/vpinball/releases/tag/v10.8.0-2051-28dd6c3"
ROWS = {
    "wsl": "WSL 2",
    "wslg": "WSLg display and audio",
    "runtime": "SPIKE 2 runtime",
    "vpx": "Visual Pinball X",
    "controller": "PAD.Controller",
}


@dataclass(frozen=True)
class Check:
    state: str
    detail: str
    action: str = ""


def run(command, timeout=30):
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                          env=dict(os.environ, WSL_UTF8="1"), timeout=timeout)


def boot_session():
    out = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "(Get-CimInstance Win32_OperatingSystem -ErrorAction Stop)"
               ".LastBootUpTime.ToUniversalTime().Ticks"])
    boot = out.stdout.decode("ascii", "replace").strip()
    if out.returncode or not boot.isdigit():
        raise RuntimeError("Windows did not report its current boot session.")
    return boot


def elevated(exe, args, timeout):
    def ps_literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    arguments = subprocess.list2cmdline([str(arg) for arg in args])
    command = (
        f"$ErrorActionPreference='Stop'; "
        f"$p=Start-Process -FilePath {ps_literal(exe)} -ArgumentList "
        f"{ps_literal(arguments)} -Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
        "exit $p.ExitCode"
    )
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                encoded], timeout=timeout)


def distro_registration():
    record = wsl_up.distribution_record(DISTRO)
    if record is None:
        return None
    path, version = record
    return Path(path), version


class Setup:
    def __init__(self, data=None):
        self.data = Path(data or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Spike2Vpx")
        self.log_path = self.data / "logs" / "setup.log"
        self.platform_log = self.log_path.with_name("wsl-platform.log")
        self.controller_log = self.log_path.with_name("controller-registration.log")
        self.settings_path = self.data / "setup.json"
        self.runtime_dir = self.data / "runtime"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = logging.Logger("spike2-vpx-setup")
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.log.addHandler(handler)
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.settings = settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            self.settings = {}

    def save(self):
        with NamedTemporaryFile(mode="w", dir=self.data, encoding="utf-8",
                                suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(self.settings, file, indent=2)
        try:
            temporary.replace(self.settings_path)
        finally:
            temporary.unlink(missing_ok=True)

    def choose_vpx(self, exe):
        exe = Path(exe).resolve()
        if exe.name.casefold() != "vpinballx64.exe" or not exe.is_file():
            raise ValueError("Select VPinballX64.exe from the Visual Pinball X installation.")
        self.settings["vpx_exe"] = str(exe)
        self.save()

    def check_wsl(self):
        pending = self.settings.get("restart_boot")
        if pending:
            if pending == boot_session():
                return Check("Restart required", "Restart Windows, then reopen SPIKE 2 VPX.")
            self.settings.pop("restart_boot")
            self.save()
        out = run(["wsl.exe", "--status"])
        detail = runtime._why(out)
        if out.returncode == 0 and "Virtual Machine Platform" not in detail:
            return Check("Ready", "Windows reports WSL available. The runtime check verifies WSL 2 can start.")
        if any(code in detail for code in ("HCS_E_HYPERV_NOT_INSTALLED", "0x80370102")):
            return Check("Needs attention", "Enable hardware virtualization in BIOS/UEFI, then run Set up.",
                         "install_wsl")
        if "E_ACCESSDENIED" in detail:
            raise RuntimeError(detail)
        self.log.info("WSL status: %s", detail)
        return Check("Missing", "Set up enables the WSL 2 platform without installing Ubuntu.", "install_wsl")

    def check_wslg(self, linux):
        out = run(["wsl.exe", "--version"])
        match = re.search(r"(?im)^WSLg[^:\r\n]*:\s*(\d+\.\d+(?:\.\d+)*)",
                          runtime._text(out.stdout).lstrip("\ufeff"))
        if out.returncode != 0 or not match:
            return Check("Missing", "Update WSL to install its Linux display and audio support.", "update_wsl")
        if linux.state == "Ready":
            return self.check_wslg_session()
        return Check("Detected", f"WSLg {match[1]} is installed. Install the runtime to check its session.")

    def wsl(self, *command):
        return run(["wsl.exe", "-d", DISTRO, "-e", *command], timeout=60)

    def check_runtime(self):
        registered = distro_registration()
        if registered is None:
            return Check("Missing", "Set up downloads and imports this app’s SPIKE 2 Linux runtime.",
                         "install_runtime")
        base, version = registered
        if base.resolve() != self.runtime_dir.resolve():
            return Check("Needs attention", f"Another WSL distribution uses {DISTRO}. "
                         "Resolve the name conflict, then check again.")
        if version != 2:
            return Check("Needs attention", f"{DISTRO} is registered as WSL 1. "
                         f"Run wsl --set-version {DISTRO} 2 in Windows Terminal, then check again.")
        manifest = self.wsl("cat", "/etc/pad-runtime.json")
        if manifest.returncode:
            raise RuntimeError(runtime._why(manifest))
        image = json.loads(runtime._text(manifest.stdout))
        if (not isinstance(image, dict) or image.get("variant") != "full"
                or image.get("runtime_version") != runtime.RUNTIME_VERSION):
            return Check("Needs attention", f"{DISTRO} needs the full runtime version "
                         f"{runtime.RUNTIME_VERSION}. Back up its data before replacing it.")
        rig = self.wsl("wslpath", "-a", "-u", str(RIG))
        if rig.returncode:
            raise RuntimeError(runtime._why(rig))
        linux_rig = runtime._text(rig.stdout).strip()
        probe = self.wsl("bash", "-ec",
                         'for tool in arm-linux-gnueabihf-gcc gcc '
                         'debugfs fuse2fs fusermount3 ffmpeg python3; do '
                         'command -v "$tool" >/dev/null || { echo "Missing $tool"; exit 1; }; done; '
                         'for file in hwshim.c rootfs.sh playaudio.sh lcdring.py padlink.py padpath.sh; do '
                         'test -r "$1/$file" || { echo "Cannot read $file"; exit 1; }; done; '
                         'source "$1/padpath.sh"; qemu=$(pad_qemu_arm); '
                         'tmp=$(mktemp -d); trap \'rm -rf -- "$tmp"\' EXIT; '
                         'echo "int main(void) { return 0; }" | '
                         'arm-linux-gnueabihf-gcc -static -x c - -o "$tmp/probe"; '
                         '"$qemu" "$tmp/probe"; '
                         'python3 -B -c \'import runpy,sys; runpy.run_path(sys.argv[1])\' "$1/padlink.py"',
                         "setup", linux_rig)
        if probe.returncode:
            raise RuntimeError(runtime._why(probe))
        return Check("Ready", f"{DISTRO} · runtime {runtime.RUNTIME_VERSION} · emulator tools available.")

    def check_wslg_session(self):
        out = self.wsl("sh", "-ec",
                       'test -n "$DISPLAY" && test -S /mnt/wslg/.X11-unix/X0 && '
                       'test -S "${PULSE_SERVER#unix:}"')
        if out.returncode:
            return Check("Needs attention", "The runtime cannot see WSLg display or audio. "
                         "Enable GUI applications in WSL settings, restart WSL, then check again.")
        return Check("Ready", "The SPIKE 2 runtime can see the WSLg display and audio sockets.")

    def check_vpx(self):
        saved = self.settings.get("vpx_exe")
        folder = Path(saved).parent if isinstance(saved, str) and saved else vpxsetup.find_vpx(use_kit=False)
        if not folder or not (Path(folder) / "VPinballX64.exe").is_file():
            return Check("Missing", "Install Visual Pinball X 10.8 or newer (Windows x64), "
                         "then select VPinballX64.exe.")
        version = vpxsetup.vpx_version(folder)
        if version is None:
            return Check("Needs attention", "Windows could not read the VPX version. "
                         "Select a compatible VPinballX64.exe.")
        if version < vpxsetup.VPX_MIN:
            return Check("Needs attention", f"VPX {vpxsetup.dotted(version)} detected. "
                         f"Install version {vpxsetup.dotted(vpxsetup.VPX_MIN)} or newer.")
        return Check("Ready", f"VPX {vpxsetup.dotted(version)} · {folder}")

    def check_controller(self):
        return self.controller("check")

    def controller(self, action):
        exe = Path(sys.executable).with_name("python.exe")
        if not exe.is_file():
            exe = Path(sys.executable)
        args = [str(KIT / "standalone_controller.py"), action]
        if action == "register":
            out = elevated(exe, [*args, str(self.controller_log)], timeout=300)
            if out.returncode:
                raise RuntimeError(f"Controller registration failed or elevation was declined. "
                                   f"Details in {self.controller_log}.")
            return
        out = run([str(exe), *args], timeout=30)
        if out.returncode:
            raise RuntimeError(runtime._why(out))
        state, detail = json.loads(out.stdout)
        return Check(state, detail, "register_controller" if state in ("Missing", "Repair needed") else "")

    def probe(self, name, fn):
        try:
            check = fn()
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            self.log.exception("%s check failed", ROWS[name])
            check = Check("Check failed", f"{exc} See the setup log and check again.")
        self.log.info("%s — %s: %s", ROWS[name], check.state, check.detail)
        failure = self.settings.get("failure")
        if isinstance(failure, dict) and failure.get("row") == name and failure.get("detail"):
            if check.state in ("Ready", "Detected", "Restart required"):
                self.settings.pop("failure")
                self.save()
            else:
                return Check("Setup failed", f"{failure['detail']} {check.detail} See the setup log.",
                             check.action)
        return check

    def checks(self):
        if sys.platform != "win32" or sys.getwindowsversion().build < 19044:
            return {key: Check("Unavailable", "Requires Windows 11 or Windows 10 build 19044 or newer.")
                    for key in ROWS}
        wsl = self.probe("wsl", self.check_wsl)
        if wsl.state == "Ready":
            linux = self.probe("runtime", self.check_runtime)
            wslg = self.probe("wslg", lambda: self.check_wslg(linux))
            if linux.state == "Ready":
                wsl = Check("Ready", f"WSL 2 started the {DISTRO} distribution.")
        else:
            wslg = linux = Check("Waiting", "Complete WSL 2 setup first.")
        return {"wsl": wsl, "wslg": wslg, "runtime": linux,
                "vpx": self.probe("vpx", self.check_vpx),
                "controller": self.probe("controller", self.check_controller)}

    def install_platform(self, mode):
        boot = boot_session()
        out = elevated("powershell.exe",
                       ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", APP / "installer" / "setup_spike2_wsl.ps1",
                        "-Mode", mode, "-Log", self.platform_log], timeout=900)
        if out.returncode == 3010:
            self.settings["restart_boot"] = boot
            self.save()
        elif out.returncode:
            self.log.error("Elevated WSL command: %s. Platform log: %s",
                           runtime._why(out), self.platform_log)
            raise RuntimeError("WSL setup failed or administrator permission was declined.")

    def install_runtime(self):
        if distro_registration() is not None:
            raise RuntimeError(f"{DISTRO} already exists. Check its status before attempting another import.")
        if self.runtime_dir.exists() and any(self.runtime_dir.iterdir()):
            raise RuntimeError(f"A previous import left files in {self.runtime_dir}. "
                               "Move that folder to a backup location before retrying.")
        image = runtime.IMAGE
        stamp = image.sha256[:12]
        cache = self.data / "downloads" / f"{stamp}-{image.filename}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not payloads.verify(str(cache), image)[0]:
            runtime._need_room(str(cache.parent), image.size + runtime.DOWNLOAD_SLACK,
                               "download the SPIKE 2 runtime")
            payloads.download(image, str(cache), log=self.log.info)
        runtime.import_image(DISTRO, str(self.runtime_dir), str(cache))

    def repair(self, key, action):
        steps = {"install_wsl": lambda: self.install_platform("Install"),
                 "update_wsl": lambda: self.install_platform("Update"),
                 "install_runtime": self.install_runtime,
                 "register_controller": lambda: self.controller("register")}
        self.settings.pop("failure", None)
        self.save()
        self.log.info("Setting up %s", ROWS[key])
        try:
            steps[action]()
            checks = self.checks()
            if checks[key].state in ("Ready", "Detected", "Restart required"):
                return checks
            if key == "wslg" and "restart_boot" in self.settings:
                return checks
            raise RuntimeError(f"{ROWS[key]} did not pass its check after setup.")
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            self.log.exception("%s setup failed", ROWS[key])
            self.settings["failure"] = {"row": key, "detail": str(exc)}
            self.save()
            return self.checks()
