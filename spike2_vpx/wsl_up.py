"""Read registrations without booting any WSL distribution."""

import sys


def registered_distros():
    """``{casefolded name: Lxss GUID}`` of every registered distro, read from
    the registry WSL itself keeps (``wsl -l`` reads the same keys), so no
    wsl.exe; None when the registry cannot be read (then ask wsl.exe)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
        out = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, guid) as sub:
                        name = winreg.QueryValueEx(sub, "DistributionName")[0]
                except OSError:
                    continue
                if name:
                    out[str(name).casefold()] = guid
        return out
    except FileNotFoundError:
        return {}                      # WSL never had a distro on this PC
    except Exception:                                   # noqa: BLE001
        return None


def distribution_record(name):
    """Return the registered base path and WSL version, or None for an absent distro."""
    names = registered_distros()
    if names is None:
        raise RuntimeError("Windows did not report its registered WSL distributions.")
    guid = names.get(name.casefold())
    if not guid:
        return None
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       rf"Software\Microsoft\Windows\CurrentVersion\Lxss\{guid}") as key:
        base = winreg.QueryValueEx(key, "BasePath")[0]
        version = winreg.QueryValueEx(key, "Version")[0]
    return base.removeprefix("\\\\?\\"), version
