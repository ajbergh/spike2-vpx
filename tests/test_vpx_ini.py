"""Setting keys in Visual Pinball's ini leaves the rest of the file as VPX wrote it."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "spike2_emu" / "vpx"))
import padvpx  # noqa: E402


class IniLineEndingsTest(unittest.TestCase):
    def test_setting_a_key_keeps_the_files_line_endings_and_bom(self):
        for raw in (b"\xef\xbb\xbf[Player]\r\nCapturePUP = 0\r\n[PlayerVR]\r\nAskToTurnOn = 1\r\n",
                    b"[Player]\r\nCapturePUP = 0\r\n",
                    b"[Player]\nCapturePUP = 0\n"):
            with self.subTest(raw=raw[:12]), TemporaryDirectory() as folder:
                path = Path(folder) / "VPinballX.ini"
                path.write_bytes(raw)
                padvpx.vpx_ini_set({"CapturePUP": 1}, path=str(path))
                self.assertEqual(path.read_bytes(), raw.replace(b"CapturePUP = 0", b"CapturePUP = 1"))

    def test_a_new_key_and_section_use_them_too(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "VPinballX.ini"
            path.write_bytes(b"\xef\xbb\xbf[Player]\r\nCapturePUP = 0\r\n")
            padvpx.vpx_ini_set({"ForceDisableB2S": 1}, section="Controller", path=str(path))
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))             # no bare LF
            lines = raw[3:].decode().split("\r\n")
            self.assertIn("[Controller]", lines)
            self.assertEqual(lines[lines.index("[Controller]") + 1], "ForceDisableB2S = 1")


if __name__ == "__main__":
    unittest.main()
