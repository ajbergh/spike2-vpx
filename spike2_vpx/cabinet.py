"""Validate app-owned screen assignments and apply the inherited placement settings."""

import os
from pathlib import Path
import sys

import padcabinet
import padisplay
import padlaunch


ROLES = {
    "playfield": "VPX playfield",
    "backglass": "B2S backglass",
    "lcd": "Stern LCD",
}
DEFAULTS = {
    "playfield": "VPX default",
    "backglass": "B2S default",
    "lcd": "Automatic (spare screen)",
}
WINDOWED_LCD = "windowed"
LCD_RECT = "lcd_rect"
LCD_RECT_FIELDS = {"rx": "X offset", "ry": "Y offset", "w": "Width", "h": "Height"}
DISABLED_B2S = "disabled"


def screen_key(monitor):
    return monitor.get("device") or "{x},{y}:{w}x{h}".format(**monitor)


def screen_name(monitor):
    number = monitor["n"]
    native = monitor.get("display_number")
    return f"Screen {number} (Windows {native})" if native and native != number else f"Screen {number}"


def lcd_rectangle(rect):
    try:
        if not isinstance(rect, dict) or set(rect) != LCD_RECT_FIELDS.keys():
            raise ValueError
        if any(type(value) not in (int, str) for value in rect.values()):
            raise ValueError
        pixels = {key: int(value) for key, value in rect.items()}
    except ValueError:
        raise ValueError("Enter whole pixel values for the Stern LCD width, height and offsets.") from None
    if not 160 <= pixels["w"] <= 16384 or not 120 <= pixels["h"] <= 16384:
        raise ValueError("Set the Stern LCD width from 160 to 16384 and height from 120 to 16384 pixels.")
    if pixels["rx"] < 0 or pixels["ry"] < 0:
        raise ValueError("Set Stern LCD offsets to zero or more pixels from the screen's top-left corner.")
    return pixels


def lcd_bounds(lcd, rect):
    pixels = lcd_rectangle(rect)
    if lcd is None:
        raise ValueError("Choose a Stern LCD screen for the borderless region.")
    rx, ry, width, height = (pixels[key] for key in LCD_RECT_FIELDS)
    if rx + width > lcd["w"] or ry + height > lcd["h"]:
        raise ValueError(f"The Stern LCD region must fit inside {screen_name(lcd)} ({lcd['w']} × {lcd['h']}).")
    return {**lcd, "x": lcd["x"] + rx, "y": lcd["y"] + ry, "w": width, "h": height}


def resolve(layout, monitors):
    screens = {screen_key(monitor): monitor for monitor in monitors}
    roles = {}
    used = set()
    for role, label in ROLES.items():
        key = layout.get(role)
        if not key or (role == "lcd" and key == WINDOWED_LCD) or (role == "backglass" and key == DISABLED_B2S):
            continue
        if key not in screens:
            raise ValueError(f"The saved {label} screen is disconnected. Reconnect it or update Settings.")
        if key in used:
            raise ValueError("Assign each cabinet role to a different screen, or use its default.")
        roles[role] = screens[key]
        used.add(key)
    if not layout.get("lcd"):
        spare = next((m for m in monitors if not m["primary"] and screen_key(m) not in used), None)
        if spare:
            roles["lcd"] = spare
    backglass = roles.get("backglass")
    if (backglass and not backglass.get("display_number")
            and sum(m["x"] == backglass["x"] for m in monitors) > 1):
        raise ValueError("B2S needs a unique horizontal position for the backglass screen. "
                         "Move it sideways in Windows Display settings or use B2S default.")
    if LCD_RECT in layout:
        lcd_bounds(roles.get("lcd"), layout[LCD_RECT])
    return roles


def saved_layout(settings, monitors):
    layout = settings.get("cabinet_layout")
    if isinstance(layout, dict):
        return {role: value for role, value in layout.items()
                if (role in ROLES and isinstance(value, str))
                or (role == LCD_RECT and isinstance(value, dict))}
    # Preserve the LCD-only setting from previous app versions.
    legacy = settings.get("lcd_monitor")
    if legacy:
        return {"lcd": next((screen_key(m) for m in monitors if m["n"] == legacy),
                            f"previous monitor {legacy}")}
    if legacy == 0:
        return {"lcd": WINDOWED_LCD}
    return {}


def backglass_available():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"B2S.Server\CLSID",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return bool(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return False


def backglass_warnings(table, roles):
    if "backglass" not in roles or not table or not Path(table).is_file():
        return []
    table = Path(table)
    issues = []
    if not table.with_suffix(".directb2s").is_file():
        issues.append("Attach a .directb2s file to use the assigned backglass screen.")
    if not backglass_available():
        issues.append("Register B2S Server for 64-bit VPX to display a backglass.")
    res = table.with_suffix(".res")
    if res.is_file() and padcabinet._lines(res)[:1] != [padcabinet.RES_MARK]:
        issues.append("This table's custom .res controls its backglass. Review it to change placement.")
    return issues


def apply_lcd(board, lcd, monitors, rect=None):
    setting = {"mode": "fullscreen", "monitor": lcd["n"], "border": False} if lcd else {"mode": "free"}
    if rect is not None:
        setting.update(lcd_rectangle(rect), mode="region")
    padisplay.save(setting, str(Path(board) / "display.conf"), monitors)


def apply(board, roles, monitors, table=None, *, lcd_rect=None):
    playfield = roles.get("playfield")
    backglass = roles.get("backglass")
    kit = padcabinet.update(padlaunch.kit_load(), backglass=backglass["n"] if backglass else 0)
    if playfield:
        kit.update(player={"monitor": playfield["n"]}, player_frame=False)
    else:
        kit.pop("player", None)
    if table:
        # Let placement failures reach the player. The patch-time helper logs and continues.
        path = os.fspath(table)
        companion = Path(path).with_suffix(".directb2s").is_file()
        table_kit = kit if companion else padcabinet.update(kit, backglass=0)
        # B2S owns its DMD and table settings. Only place the backglass.
        padcabinet.write_res(path, table_kit, monitors)
    padlaunch.kit_save(kit)
    apply_lcd(board, roles.get("lcd"), monitors, lcd_rect)
