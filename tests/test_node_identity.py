from pathlib import Path
import sys

RIG = Path(__file__).resolve().parents[1] / "tools" / "spike2_emu"
sys.path.insert(0, str(RIG))
from nbdir import mag_sensor_variant  # noqa: E402


def test_mag_sensor_firmware_variant_uses_validated_header(tmp_path):
    header = bytearray([16, 0, 0, 6] + [0] * 16)
    header[4 + 14] = 0x0F
    header.append(-sum(header) & 0xFF)
    firmware = tmp_path / "magsensornode-LPC812-1_42_0.hex"
    firmware.write_text(":" + header.hex() + "\n", encoding="ascii")
    assert mag_sensor_variant(firmware) == 0x0F

    header[-1] ^= 1
    firmware.write_text(":" + header.hex() + "\n", encoding="ascii")
    assert mag_sensor_variant(firmware) is None
