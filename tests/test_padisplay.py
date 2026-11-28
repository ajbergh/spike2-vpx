"""Where the Stern LCD's window goes, with monitors left of or above the main one."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "spike2_emu" / "vpx"))
sys.path.insert(0, str(ROOT / "tools" / "spike2_emu"))
import padisplay  # noqa: E402
import padloading  # noqa: E402

#: a 2560x1440 screen left of a 3840x2160 main one
MAIN = {"n": 1, "x": 0, "y": 0, "w": 3840, "h": 2160, "primary": True}
LEFT = {"n": 2, "x": -2560, "y": 468, "w": 2560, "h": 1440, "primary": False}


def game_line(line):
    """display.conf's game line as {key: int}, mode aside."""
    return {k: int(v) for k, v in (t.split("=", 1) for t in line.split()[1:]) if k != "mode"}


class GameLineTest(unittest.TestCase):
    """padglhost moves the window to the line's x y, which are X root
    coordinates: counted from the virtual desktop's top-left."""

    def test_a_monitor_left_of_the_main_one_moves_the_x_origin(self):
        line = game_line(padisplay.resolve({"mode": "window", "monitor": 1, "rx": 1500, "ry": 100,
                                            "w": 1024, "h": 576}, [MAIN, LEFT]))
        self.assertEqual((line["rx"], line["ry"]), (1500, 100))         # the choice, as made
        self.assertEqual((line["x"], line["y"], line["w"], line["h"]), (2560 + 1500, 100, 1024, 576))
        line = game_line(padisplay.resolve({"mode": "fullscreen", "monitor": 2}, [MAIN, LEFT]))
        self.assertEqual((line["x"], line["y"], line["w"], line["h"], line["fs"]), (0, 468, 2560, 1440, 1))

    def test_a_monitor_above_moves_it_down(self):
        above = {"n": 2, "x": 200, "y": -1080, "w": 1920, "h": 1080, "primary": False}
        line = game_line(padisplay.resolve({"mode": "fullscreen", "monitor": 1}, [MAIN, above]))
        self.assertEqual((line["x"], line["y"], line["w"], line["h"]), (0, 1080, 3840, 2160))

    def test_one_monitor_is_unchanged(self):
        line = game_line(padisplay.resolve({"mode": "fullscreen", "monitor": 1}, [MAIN]))
        self.assertEqual((line["x"], line["y"], line["w"], line["h"], line["fs"]), (0, 0, 3840, 2160, 1))

    def test_free_has_no_line(self):
        self.assertIsNone(padisplay.resolve({"mode": "free"}, [MAIN, LEFT]))


class WindowsPlacementTest(unittest.TestCase):
    """The loading screen is a Windows window, so it stays in Windows' own coordinates."""

    def test_place_stays_in_windows_coordinates(self):
        p = padisplay.place({"mode": "fullscreen", "monitor": 2}, [MAIN, LEFT])
        self.assertEqual((p["x"], p["y"], p["w"], p["h"]), (LEFT["x"], LEFT["y"], LEFT["w"], LEFT["h"]))
        self.assertIsNone(padisplay.place({"mode": "free"}, [MAIN]))

    def test_the_loading_screen_opens_where_the_game_will(self):
        with patch.object(padisplay, "load", return_value={"mode": "fullscreen", "monitor": 2}), \
                patch.object(padisplay, "monitors", return_value=[MAIN, LEFT]):
            self.assertEqual(padloading.first_rect(), (LEFT["x"], LEFT["y"], LEFT["w"], LEFT["h"]))


if __name__ == "__main__":
    unittest.main()
