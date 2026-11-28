"""Setup checks and actions for the standalone Windows player."""

from dataclasses import dataclass
from pathlib import Path
import base64
import json
import logging
import os
import re
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile

from spike2_vpx import payloads, runtime, storage, wsl_up

APP = Path(__file__).resolve().parent
RIG = APP / "tools" / "spike2_emu"
KIT = RIG / "vpx"
sys.path.insert(0, str(KIT))
import vpxsetup  # noqa: E402

DISTRO = "SPIKE2-VPX"
REINSTALL_RUNTIME = "reinstall_runtime"
VPX_DEFAULT_EXE = Path(r"C:\vPinball\VisualPinbal") / "VPinballX64.exe"
VPX_RELEASE = "https://github.com/vpinball/vpinball/releases/tag/v10.8.0-2051-28dd6c3"
VIRTUALIZATION_HELP = "https://support.microsoft.com/en-us/windows/experience/enable-virtualization-on-windows"
VIRTUALIZATION_GUIDANCE = (
    "WSL 2 could not use hardware virtualization. In Task Manager → Performance → CPU, "
    "check Virtualization. If disabled, enable Intel VT-x or AMD-V (SVM Mode) in your PC's "
    "BIOS/UEFI, then restart and reopen the app. On a virtual machine, check nested "
    "virtualization on the host."
)
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
    missing_disk: Path | None = None


def missing_runtime_mount(detail, disk):
    detail = detail.casefold()
    path = re.escape(" ".join(str(disk).casefold().split()))
    return (
        re.search(path + r"['\"’”]", detail) is not None
        and re.search(r"\bwsl/service/createinstance/mount(?:disk|vhd)/(?:hcs/)?"
                      r"error_(?:path|file)_not_found\b", detail) is not None
    )


def empty_runtime_folder(folder):
    """Accept an empty or missing directory on a reachable drive."""
    if not folder.is_absolute() or folder == Path(folder.anchor):
        return False
    try:
        Path(folder.anchor).stat()
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return True
        return (stat.S_ISDIR(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                and next(folder.iterdir(), None) is None)
    except OSError:
        return False


def needs_virtualization(detail):
    text = detail.casefold()
    return any(code in text for code in (
        "hcs_e_hyperv_not_installed", "0x80370102", "wsl_e_wsl2_not_supported",
    ))


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
    def __init__(self, data=None, *, log_to_file=True):
        self.data = Path(data or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Spike2Vpx")
        self.log_path = self.data / "logs" / "setup.log"
        self.platform_log = self.log_path.with_name("wsl-platform.log")
        self.controller_log = self.log_path.with_name("controller-registration.log")
        self.settings_path = self.data / "setup.json"
        self.log = logging.Logger("spike2-vpx-setup")
        if log_to_file:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.log.addHandler(handler)
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.settings = settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            self.settings = {}

    @property
    def runtime_dir(self):
        return storage.installed_folder(APP, "runtime") or self.data / "runtime"

    def owns_runtime(self, base):
        base = base.resolve()
        saved = self.settings.get("runtime_dir")
        if isinstance(saved, str) and saved:
            return base == Path(saved).resolve()
        # Older installers used this location and did not record it in setup.json.
        return base in (self.runtime_dir.resolve(), (self.data / "runtime").resolve())

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
        missing = Check("Missing", "Select Set up and approve the Windows administrator prompt. "
                        "The app installs WSL 2 without adding Ubuntu.", "install_wsl")
        try:
            out = run(["wsl.exe", "--status"])
        except FileNotFoundError:
            return missing
        detail = runtime._why(out)
        status = detail.casefold()
        if needs_virtualization(detail):
            return Check("Needs attention", VIRTUALIZATION_GUIDANCE, "install_wsl")
        if out.returncode == 0 and not any(message in status for message in (
            "--install", "virtual machine platform", "wsl_e_wsl_optional_component_required",
        )):
            return Check("Ready", "Windows reports WSL available. The runtime check verifies WSL 2 can start.")
        if "e_accessdenied" in status:
            raise RuntimeError(detail)
        self.log.info("WSL status: %s", detail)
        return missing

    def check_wslg(self, linux):
        out = run(["wsl.exe", "--version"])
        match = re.search(r"(?im)^WSLg[^:\r\n]*:\s*(\d+\.\d+(?:\.\d+)*)",
                          runtime._text(out.stdout).lstrip("\ufeff"))
        if out.returncode != 0 or not match:
            return Check("Missing", "Set up updates WSL to add Linux display and audio support.", "update_wsl")
        if linux.state == "Ready":
            return self.check_wslg_session()
        return Check("Detected", f"WSLg {match[1]} is installed. Install the runtime to check its session.")

    def wsl(self, *command, timeout=60):
        return run(["wsl.exe", "-d", DISTRO, "-e", *command], timeout=timeout)

    def arm_execution(self, linux_rig, mode):
        return run(["wsl.exe", "-d", DISTRO, "-u", "root", "-e", "bash",
                    linux_rig + "/vpx_arm_exec.sh", mode], timeout=30)

    def check_runtime(self):
        registered = distro_registration()
        if registered is None:
            return Check("Missing", "Set up downloads and imports this app’s SPIKE 2 Linux runtime.",
                         "install_runtime")
        base, version = registered
        if not self.owns_runtime(base):
            return Check("Needs attention", f"Another WSL distribution uses {DISTRO}. "
                         "Resolve the name conflict, then check again.")
        if version != 2:
            return Check("Repair needed", f"{DISTRO} is registered as WSL 1. "
                         "Back up its data, then select Convert runtime to convert it to WSL 2.",
                         "convert_runtime")
        manifest = self.wsl("cat", "/etc/pad-runtime.json")
        if manifest.returncode:
            detail = runtime._why(manifest)
            disk = base / "ext4.vhdx"
            if missing_runtime_mount(detail, disk):
                self.log.info("Runtime disk mount failed. %s", detail)
                if empty_runtime_folder(base):
                    return Check("Repair needed", f"WSL cannot find the runtime disk at {disk}. "
                                 "Restore it from backup if available, or click to reinstall the runtime.",
                                 REINSTALL_RUNTIME, disk)
                return Check("Needs attention", f"WSL cannot open the runtime disk at {disk}. "
                             "Check the drive connection, folder contents, and permissions, then check again.")
            raise RuntimeError(detail)
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
                         'python3 -B -c \'import runpy,sys; runpy.run_path(sys.argv[1])\' "$1/padlink.py"',
                         "setup", linux_rig)
        if probe.returncode:
            raise RuntimeError(runtime._why(probe))
        arm = self.arm_execution(linux_rig, "check")
        if arm.returncode == 10:
            self.log.info("Restoring ARM execution for %s: %s", DISTRO, runtime._why(arm))
            arm = self.arm_execution(linux_rig, "repair")
            if arm.returncode:
                return Check("Repair needed", f"WSL could not restore ARM execution. {runtime._why(arm)}",
                             "repair_arm")
        if arm.returncode:
            raise RuntimeError(runtime._why(arm))
        return Check("Ready", f"{DISTRO} · runtime {runtime.RUNTIME_VERSION} · ARM execution verified in a chroot.")

    def repair_arm(self):
        registered = distro_registration()
        if registered is None or not self.owns_runtime(registered[0]) or registered[1] != 2:
            raise RuntimeError("ARM repair requires this app's SPIKE 2 WSL 2 distribution.")
        rig = self.wsl("wslpath", "-a", "-u", str(RIG))
        if rig.returncode:
            raise RuntimeError(runtime._why(rig))
        out = self.arm_execution(runtime._text(rig.stdout).strip(), "repair")
        if out.returncode:
            raise RuntimeError(runtime._why(out))

    def convert_runtime(self):
        registered = distro_registration()
        if registered is None or not self.owns_runtime(registered[0]) or registered[1] != 1:
            raise RuntimeError("Conversion requires this app's SPIKE 2 WSL 1 distribution.")
        out = run(["wsl.exe", "--set-version", DISTRO, "2"], timeout=1800)
        if out.returncode:
            raise RuntimeError(runtime._why(out))

    def check_wslg_session(self):
        out = self.wsl("sh", "-ec",
                       'test -n "$DISPLAY" && test -S /mnt/wslg/.X11-unix/X0 && '
                       'test -S "${PULSE_SERVER#unix:}"')
        if out.returncode:
            return Check("Needs attention", "The runtime cannot see WSLg display or audio. "
                         "Enable GUI applications in WSL settings, restart WSL, then check again.")
        return Check("Ready", "The SPIKE 2 runtime can see the WSLg display and audio sockets.")

    def vpx_executable(self):
        saved = self.settings.get("vpx_exe")
        if isinstance(saved, str) and saved:
            exe = Path(saved)
            if exe.is_file():
                return exe
        return VPX_DEFAULT_EXE if VPX_DEFAULT_EXE.is_file() else None

    def check_vpx(self):
        exe = self.vpx_executable()
        if exe is None or not exe.is_file():
            return Check("Missing", "Choose VPinballX64.exe from your Visual Pinball X installation. "
                         "If needed, install version 10.8 or newer (Windows x64) first.")
        folder = exe.parent
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
            detail = str(exc)
            if name == "runtime" and needs_virtualization(detail):
                check = Check("Needs attention", VIRTUALIZATION_GUIDANCE)
            else:
                check = Check("Check failed", f"{detail} See the setup log and check again.")
        self.log.info("%s — %s: %s", ROWS[name], check.state, check.detail)
        failure = self.settings.get("failure")
        if isinstance(failure, dict) and failure.get("row") == name and failure.get("detail"):
            if check.state in ("Ready", "Detected", "Restart required"):
                self.settings.pop("failure")
                self.save()
            else:
                return Check("Setup failed", f"{failure['detail']} {check.detail} See the setup log.",
                             check.action, check.missing_disk)
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

    def install_runtime(self, progress=None):
        if distro_registration() is not None:
            raise RuntimeError(f"{DISTRO} already exists. Check its status before attempting another import.")
        target = self.runtime_install_target()
        archive = self.runtime_archive(progress)
        self.import_runtime(target, archive, progress)

    def runtime_install_target(self):
        target = self.runtime_dir.resolve()
        if not empty_runtime_folder(target):
            raise RuntimeError(f"The runtime needs an empty folder on an available drive. Check {target} "
                               "or run the installer again to choose another location.")
        return target

    def runtime_archive(self, progress):
        image = runtime.IMAGE
        stamp = image.sha256[:12]
        cache = self.data / "downloads" / f"{stamp}-{image.filename}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress("Checking for a verified SPIKE 2 runtime download…")
        if not payloads.verify(str(cache), image)[0]:
            runtime._need_room(str(cache.parent), image.size + runtime.DOWNLOAD_SLACK,
                               "download the SPIKE 2 runtime")
            if progress:
                progress("Downloading the SPIKE 2 runtime…", 0, image.size)
            payloads.download(image, str(cache), log=self.log.info,
                              progress=(lambda done, total: progress("Downloading the SPIKE 2 runtime…", done, total))
                              if progress else None,
                              verifying=(lambda: progress("Verifying the runtime download…")) if progress else None)
        return cache

    def import_runtime(self, target, archive, progress):
        if progress:
            progress("Installing the SPIKE 2 runtime in WSL…")
        runtime.import_image(DISTRO, str(target), str(archive))
        self.settings["runtime_dir"] = str(target)
        self.save()

    def reinstall_runtime(self, missing_disk, progress):
        target = self.runtime_install_target()
        check = self.check_runtime()
        if check.action != REINSTALL_RUNTIME or check.missing_disk != missing_disk:
            raise RuntimeError("The runtime status changed. Check again before reinstalling.")
        archive = self.runtime_archive(progress)
        if self.runtime_install_target() != target:
            raise RuntimeError("The runtime installation folder changed. Check again before reinstalling.")
        runtime._need_room(str(Path(target.anchor)), runtime.IMPORT_ROOM, "install the SPIKE 2 runtime")
        if (distro_registration() != (missing_disk.parent, 2)
                or not empty_runtime_folder(missing_disk.parent)):
            raise RuntimeError("The runtime status changed. Check again before reinstalling.")
        if progress:
            progress("Removing the stale SPIKE 2 WSL registration…")
        self.log.info("Unregistering %s with missing disk %s", DISTRO, missing_disk)
        out = run(["wsl.exe", "--unregister", DISTRO], timeout=120)
        if out.returncode:
            raise RuntimeError(f"WSL could not remove the stale runtime registration. {runtime._why(out)}")
        if distro_registration() is not None:
            raise RuntimeError(f"{DISTRO} is still registered. Check its status before importing the runtime.")
        self.import_runtime(target, archive, progress)

    def repair(self, key, action, *, progress=None, missing_disk=None):
        steps = {"install_wsl": lambda: self.install_platform("Install"),
                 "update_wsl": lambda: self.install_platform("Update"),
                 "install_runtime": lambda: self.install_runtime(progress),
                 REINSTALL_RUNTIME: lambda: self.reinstall_runtime(missing_disk, progress),
                 "convert_runtime": self.convert_runtime,
                 "repair_arm": self.repair_arm,
                 "register_controller": lambda: self.controller("register")}
        self.settings.pop("failure", None)
        self.save()
        self.log.info("Setting up %s", ROWS[key])
        try:
            if progress:
                message = f"Setting up {ROWS[key]}…"
                if action in ("install_wsl", "update_wsl", "register_controller"):
                    message += " Check for a Windows administrator prompt."
                progress(message)
            steps[action]()
            if progress:
                progress("Checking the installation…")
            checks = self.checks()
            if checks[key].state in ("Ready", "Detected", "Restart required"):
                return checks
            if key == "wslg" and "restart_boot" in self.settings:
                return checks
            raise RuntimeError(f"{ROWS[key]} did not pass its check after setup.")
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            self.log.exception("%s setup failed", ROWS[key])
            detail = str(exc)
            if key == "runtime" and needs_virtualization(detail):
                detail = VIRTUALIZATION_GUIDANCE
            self.settings["failure"] = {"row": key, "detail": detail}
            self.save()
            if progress:
                progress("Checking what needs attention…")
            return self.checks()
