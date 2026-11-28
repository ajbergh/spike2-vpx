"""Remove the private runtime and files created by the Windows installation."""

import os
from pathlib import Path
import shutil
import stat
import sys

# Bundled Python's isolated search path does not include the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike2_vpx_setup import APP, DISTRO, Setup, distro_registration, run, runtime, storage  # noqa: E402


def stop_bundled_processes():
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32process

    executables = {APP / "python" / name for name in ("python.exe", "pythonw.exe")}
    access = (win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
              | win32con.PROCESS_TERMINATE | win32con.SYNCHRONIZE)
    for pid in win32process.EnumProcesses():
        if pid == os.getpid():
            continue
        try:
            process = win32api.OpenProcess(access, False, pid)
        except pywintypes.error:
            continue  # System processes and processes that have already exited.
        try:
            try:
                exe = Path(win32process.GetModuleFileNameEx(process, 0))
            except pywintypes.error:
                continue
            if exe in executables and win32event.WaitForSingleObject(process, 0) == win32event.WAIT_TIMEOUT:
                win32api.TerminateProcess(process, 0)
                if win32event.WaitForSingleObject(process, 10000) == win32event.WAIT_TIMEOUT:
                    raise RuntimeError(f"Could not close {exe.name} from this installation.")
        finally:
            process.Close()


def remove_runtime(setup):
    registered = distro_registration()
    if registered is None:
        return
    base, version = registered
    if not setup.owns_runtime(base):
        return
    wsl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    run([str(wsl), "--terminate", DISTRO], timeout=60)
    if distro_registration() != registered:
        raise RuntimeError("The SPIKE2-VPX WSL registration changed during uninstall. Try again.")
    # Unregister also stops the distro if it was already stopped or stale during terminate.
    result = run([str(wsl), "--unregister", DISTRO], timeout=120)
    if result.returncode or distro_registration() is not None:
        raise RuntimeError(f"WSL could not remove the SPIKE 2 runtime. {runtime._why(result)}")
    if version == 2 and (base / "ext4.vhdx").exists():
        raise RuntimeError(f"WSL left the runtime disk at {base / 'ext4.vhdx'}.")


def remove_owned_tree(path):
    # Reject redirected parents before attempting recursive deletion from an elevated uninstall.
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeError(f"Uninstall cannot safely clean the redirected folder {component}.")
    shutil.rmtree(path)


def remove_player_files(data, caches):
    for cache in caches:
        remove_owned_tree(cache)
    for name in ("downloads", "images", "lighting-scans", "logs", "rig-board"):
        remove_owned_tree(data / name)
    # Attempt only the named app files. Keep any backups or other files saved here by the user.
    if data.is_dir():
        if data.resolve() != data:
            raise RuntimeError(f"Uninstall cannot safely clean the redirected folder {data}.")
        for name in ("audio.json", "setup.json", "setup.lock", "vpx.json", "vpx.json.tmp"):
            (data / name).unlink(missing_ok=True)
        if next(data.iterdir(), None) is None:
            data.rmdir()


def remove_bytecode():
    for directory, children, _ in APP.walk():
        # Directory junctions can point outside the installed application.
        children[:] = [name for name in children if not (directory / name).is_junction()]
        if "__pycache__" in children:
            children.remove("__pycache__")
            cache = directory / "__pycache__"
            for file in cache.glob("*.pyc"):
                file.unlink()
            if next(cache.iterdir(), None) is None:
                cache.rmdir()


def uninstall():
    import standalone_controller
    from win32com.shell import shell, shellcon

    stop_bundled_processes()
    local = shell.SHGetFolderPath(0, shellcon.CSIDL_LOCAL_APPDATA, 0, 0)
    setup = Setup(Path(local) / "Spike2Vpx", log_to_file=False)
    remove_runtime(setup)
    standalone_controller.change("unregister")
    remove_player_files(setup.data, storage.cache_folders(APP))
    remove_bytecode()


if __name__ == "__main__":
    try:
        uninstall()
    except Exception as exc:
        Path(sys.argv[1]).write_text(str(exc), encoding="utf-8-sig")
        sys.exit(1)
