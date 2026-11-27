"""Storage locations selected in the Windows installer."""

from configparser import ConfigParser
import ctypes
from pathlib import Path, PureWindowsPath
import struct


def installed_folder(app, key):
    config = ConfigParser(interpolation=None)
    config.read(Path(app) / "storage.ini", encoding="utf-8-sig")
    value = config.get("Storage", key, fallback="")
    folder = PureWindowsPath(value)
    if folder.is_absolute() and len(folder.drive) == 2:
        return Path(value)
    return None


def cache_folder(app):
    folder = installed_folder(app, "cache")
    return folder / "SPIKE2-VPX-cardcache" if folder is not None else None


def set_sparse(file, *, unused=None):
    """Mark a Windows file sparse, or deallocate unused byte ranges in a sparse file."""
    import msvcrt
    from ctypes import wintypes

    device_io = ctypes.WinDLL("kernel32", use_last_error=True).DeviceIoControl
    device_io.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    device_io.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(file.fileno())
    returned = wintypes.DWORD()
    if unused is None:
        if not device_io(handle, 0x900C4, None, 0, None, 0, ctypes.byref(returned), None):  # FSCTL_SET_SPARSE
            raise ctypes.WinError(ctypes.get_last_error())
        return
    for start, end in unused:
        bounds = struct.pack("<qq", start, end)
        if not device_io(handle, 0x980C8, bounds, len(bounds), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())  # FSCTL_SET_ZERO_DATA
