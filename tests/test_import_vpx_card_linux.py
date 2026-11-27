"""Exercise card inspection and sparse allocation on a Linux filesystem."""

from io import BytesIO
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


RIG = Path(__file__).resolve().parents[1] / "tools" / "spike2_emu"


def make_card(folder):
    system = folder / "system"
    (system / "lib").mkdir(parents=True)
    (system / "usr").mkdir()
    games = folder / "games"
    (games / "spk").mkdir(parents=True)
    (games / "godzilla_le").mkdir()
    (games / "godzilla_le/game").write_bytes(b"Synthetic game executable")

    partition_size = 8 * 1024**2
    system_offset = 2 * 1024**2
    game_offset = system_offset + partition_size
    raw = bytearray(game_offset + partition_size)
    raw[510:512] = b"\x55\xaa"
    partitions = ((0x0c, 2048, 2048), (0x83, system_offset // 512, partition_size // 512),
                  (0x83, game_offset // 512, partition_size // 512))
    for index, (kind, start, count) in enumerate(partitions):
        struct.pack_into("<B3xB3xII", raw, 446 + index * 16, 0, kind, start, count)

    for tree, offset in ((system, system_offset), (games, game_offset)):
        image = tree.with_suffix(".ext4")
        with image.open("wb") as file:
            file.truncate(partition_size)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-d", tree, image], check=True)
        raw[offset:offset + partition_size] = image.read_bytes()

    free_block = int(subprocess.check_output(["debugfs", "-R", "ffb", image], text=True).split()[-1])
    unused = game_offset + free_block * 4096
    raw[unused:unused + 4096] = b"x" * 4096
    return raw, unused


@unittest.skipUnless(sys.platform == "linux", "Requires Linux ext4 tooling and hole punching")
class SparseImportTest(unittest.TestCase):
    def setUp(self):
        with patch.object(sys, "path", [str(RIG), *sys.path]):
            import import_vpx_card
        self.importer = import_vpx_card

    def test_publishes_playable_sparse_image(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            raw, unused = make_card(folder)
            library = folder / "library"
            raw.append(1)  # The Windows reader sends this after successful ZIP verification.
            with patch.object(subprocess, "check_output", return_value=r"\\wsl.localhost\SPIKE2-VPX\library"):
                result = self.importer.import_card(BytesIO(raw), library, len(raw) - 1, len(raw), "godzilla_le")

            image, = library.iterdir()
            self.assertEqual(result["title"], "godzilla_le")
            self.assertTrue(result["image"].endswith("\\" + image.name))
            self.assertEqual(self.importer.inspect(image), "godzilla_le")
            self.assertEqual(image.stat().st_size, len(raw) - 1)
            self.assertLess(image.stat().st_blocks * 512, len(raw) // 4)
            with image.open("rb") as file:
                file.seek(unused)
                self.assertEqual(file.read(4096), bytes(4096))

    def test_missing_verification_marker_leaves_no_image(self):
        with TemporaryDirectory() as folder:
            library = Path(folder)
            with self.assertRaisesRegex(ValueError, "did not finish verification"):
                self.importer.import_card(BytesIO(b"card"), library, 4, 4096, "-")
            self.assertEqual(list(library.iterdir()), [])
