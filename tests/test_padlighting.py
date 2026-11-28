"""First-build coverage for cabinet detection, live RGB and Teensy handoff."""

from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "spike2_emu" / "vpx"))
import play_spike2
import padlighting as lighting


CABINET = """<Cabinet xmlns="urn:dof"><Name>Lizard Pin</Name>
<OutputControllers>
 <TeensyStripController><Name>Teensy</Name><ComPortName>COM3</ComPortName>
  <NumberOfLedsStrip1>99</NumberOfLedsStrip1><NumberOfLedsStrip5>99</NumberOfLedsStrip5>
 </TeensyStripController>
 <ArtNet><Name>Network</Name></ArtNet>
</OutputControllers>
<Toys>
 <LedStrip><Name>PF Right</Name><OutputControllerName>Teensy</OutputControllerName>
  <Width>1</Width><Height>99</Height><ColorOrder>GRB</ColorOrder>
  <LedStripArrangement>BottomUpLeftRight</LedStripArrangement></LedStrip>
 <LedStrip><Name>PF Left</Name><OutputControllerName>Teensy</OutputControllerName>
  <Height>99</Height><FirstLedNumber>100</FirstLedNumber><ColorOrder>GRB</ColorOrder>
  <LedStripArrangement>BottomUpLeftRight</LedStripArrangement></LedStrip>
 <LedStrip><Name>Speaker Rings</Name><OutputControllerName>Network</OutputControllerName>
  <Height>60</Height></LedStrip>
 <RGBLed><Name>Beacon</Name></RGBLed>
</Toys></Cabinet>"""


class LightingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "cabinet.xml"
        self.path.write_text(CABINET, encoding="utf-8")
        self.controllers, self.fixtures = lighting.read_cabinet(self.path)

    def test_inventory_and_sparse_channels(self):
        self.assertEqual(list(self.fixtures), ["PF Right", "PF Left", "Speaker Rings", "Beacon"])
        self.assertEqual(self.fixtures["PF Right"].positions(), tuple(range(99)))
        self.assertEqual(self.fixtures["PF Left"].positions(), tuple(range(99, 198)))
        self.assertEqual(lighting.suggest(self.fixtures["PF Left"]), "left")
        self.assertIn("ArtNet", self.fixtures["Speaker Rings"].issue)
        self.assertIn("RGBLed", self.fixtures["Beacon"].issue)
        self.assertEqual(self.controllers["Teensy"].lengths[:5], (99, 0, 0, 0, 99))

    def test_detect_uses_the_dof_global_file_and_refuses_ambiguous_paths(self):
        config = self.root / "Config"
        config.mkdir()
        global_config = config / "GlobalConfig_B2SServer.xml"
        global_config.write_text("<GlobalConfig><CabinetConfigFilePattern>"
                                 "{GlobalConfigDir}/../cab*.xml</CabinetConfigFilePattern></GlobalConfig>")
        # The runner's TEMP can use an 8.3 alias. Detection returns the canonical path.
        self.assertEqual(lighting.detect([self.root]), self.path.resolve())
        (self.root / "cab2.xml").write_text(CABINET)
        self.assertIsNone(lighting.detect([self.root]))

    def test_matrix_order_and_overlapping_segments(self):
        matrix = lighting.Fixture("Matrix", "Teensy", 2, 3, 1, "GRB",
                                  "TopDownAlternateRightLeft", 100)
        self.assertEqual(matrix.positions(), (5, 0, 4, 1, 3, 2))
        fixtures = {**self.fixtures, "Matrix": matrix}
        with self.assertRaisesRegex(ValueError, "overlaps"):
            lighting.assignments(fixtures, {"PF Right": {"role": "right"}, "Matrix": {"role": "topper"}})
        self.assertEqual(lighting.assignments(
            fixtures, {"PF Right": {"role": "right", "offset": 10, "length": 3, "reverse": True}}
        )[0][2], (12, 11, 10))

    def test_frame_reads_high_bank_and_maps_grb(self):
        coilmap = lighting.coilmap
        data = bytearray(coilmap.HI_READ)
        struct.pack_into("<II", data, 0, coilmap.PADLED_MAGIC, 6)
        start = coilmap.HI_OFF + 2 * coilmap.HI_IDX
        data[start:start + 3] = b"\xc8\x64\x32"
        pixels = [{"name": "Light", "channels": dict(zip("RGB", ((2, 96), (2, 97), (2, 98))))}]
        mappings = lighting.assignments(self.fixtures, {"PF Left": {"role": "left", "length": 1}})
        self.assertIsNone(lighting.frame(data[:100], {"left": pixels}, mappings, self.controllers, 50))
        result = lighting.frame(data, {"left": pixels}, mappings, self.controllers, 50)["Teensy"]
        self.assertEqual(result[297:303], b"\x32\x64\x19\x00\x00\x00")

    def test_title_sources_use_current_tables_and_ignore_absent_nodes(self):
        device = self.root / "device_xy.txt"
        rows = []
        for pixel, group, start in (("EXPRESSIVE LIGHTING L 1", 6, 0),
                                    ("EXPRESSIVE LIGHTING R 1", 6, 288),
                                    ("SPEAKER 1", 7, 0)):
            rows.extend(f"led {pixel}-{color} 0 0 0 0 {group} {start + offset} - -"
                        for offset, color in enumerate("RGB"))
        device.write_text("\n".join(rows))
        (self.root / "node_ident.txt").write_text(
            "node=2 type=ws2812node group=6 name=Cabinet Lights\n"
            "node=7 type=ws2812node group=7 name=Backbox Speaker Lights\n")
        (self.root / "group_node_exact.txt").write_text("6 2\n7 7\n")
        (self.root / "node_census.notopper.txt").write_text("silent=7\n")
        sources = lighting.stern_sources(self.root)
        self.assertEqual({role: pixels[0]["channels"]["R"] for role, pixels in sources.items()},
                         {"left": (2, 0), "right": (2, 288)})

    def test_lighting_scan_distinguishes_old_tables_from_no_mapping(self):
        launch = Mock(title="rush_le", lighting_scan="new", lighting_topper=False)
        rig = Mock(share=True, dump=self.root)
        rig.tables_dir.return_value = self.root
        (self.root / "device_xy.txt").write_text("# verified build with no device rows\n")
        (self.root / "node_ident.txt").write_text("node=0 type=cpu group=1 name=CPU\n")
        marker = self.root / "lighting-tables"
        marker.write_text("old 1\n")
        self.assertEqual(play_spike2.lighting_sources(launch, rig, time.monotonic()), ({}, None))
        rig.tables_dir.assert_not_called()
        marker.write_text("new 1\n")
        self.assertEqual(play_spike2.lighting_sources(launch, rig, time.monotonic()), ({}, ()))
        marker.write_text("new 0\n")
        self.assertEqual(play_spike2.lighting_sources(launch, rig, time.monotonic()), ({}, ()))
        (self.root / "device_xy.txt").write_text("led Unresolved 0 0 0 0 1 1 - -\n")
        self.assertEqual(play_spike2.lighting_sources(launch, rig, time.monotonic()), ({}, None))

    def test_teensy_uses_hardware_stride_and_releases_on_failed_clear(self):
        port = Mock()
        port.read.side_effect = [b"A", b"\x04\x4cA", b"A", b"A", b"A", b"A", b"A", b"N"]
        device = lighting.Teensy(self.controllers["Teensy"], open_port=lambda _: port)
        frame = bytearray(594)
        frame[297:300] = b"\x01\x02\x03"
        device.send(frame)
        self.assertEqual(port.write.call_args_list[-2].args[0][:8],
                         b"R" + struct.pack(">HH", 4 * 99, 99) + b"\x01\x02\x03")
        device.send(frame)
        with self.assertRaises(OSError):
            device.close()
        port.close.assert_called_once()

    def test_busy_second_port_releases_first_port_and_skips_session(self):
        first = Mock()
        right = self.fixtures["PF Right"]
        another = lighting.TeensyConfig("Other", "COM10", (1,), 9600, False)
        fixture = lighting.Fixture("Speaker", another.name, 1, 1, 1, "RGB", "BottomUpLeftRight", 100)
        open_device = Mock(side_effect=[first, OSError("busy")])
        with self.assertRaisesRegex(OSError, "COM10: busy"):
            lighting.Session({**self.controllers, another.name: another},
                             [(right, "right", (0,)), (fixture, "speaker", (0,))],
                             {"right": [1], "speaker": [1]}, 50, Mock(), open_device)
        first.close.assert_called_once()

    def test_launcher_claims_ports_before_vpx_and_closes_them_on_vpx_exit(self):
        for roles, fallback, expected in (
                (("left", "right", "speaker"), True, ("left", "right")),
                (("speaker",), True, ("speaker", "speaker")),
                (("left", "speaker"), True, ("left", "speaker")),
                (("speaker",), False, ()),
                ((), True, ())):
            with self.subTest(roles=roles, fallback=fallback):
                self.check_launcher_lighting(roles, fallback, expected)

    def check_launcher_lighting(self, roles, fallback, expected):
        from types import SimpleNamespace
        events = []
        launch = Mock(title="rush", slot=1, prepared_launch=True)
        launch.stop.side_effect = lambda: events.append("rig stop")
        rig = SimpleNamespace(distro="Test", root="/tmp/rig", share=True, led_path=lambda: "padled")
        mappings = [(self.fixtures["PF Left"], "left", (99,)),
                    (self.fixtures["PF Right"], "right", (0,))]
        config = (self.controllers, mappings, 50)
        sources = {role: [1] for role in roles}
        session = Mock(configs=self.controllers)
        claim = Mock(side_effect=lambda *_: events.append("claim") or session)
        report = Mock()
        session.close.side_effect = lambda: events.append("lighting close")
        vpx = Mock()
        vpx.__enter__ = Mock(return_value=vpx)
        vpx.__exit__ = Mock(return_value=False)
        vpx.wait.side_effect = lambda: events.append("VPX exit") or 9
        with (patch.object(play_spike2.padcontroller.Rig, "board_run", return_value=None),
              patch.object(play_spike2, "wait_for_rig", return_value=rig),
              patch.object(play_spike2, "wait_for_game"),
              patch.object(play_spike2.vpx_lcd, "placement_target", return_value=None),
              patch.object(play_spike2, "lighting_sources", return_value=(sources, roles)),
              patch.object(lighting, "prepare", return_value=config),
              patch.object(lighting, "Session", claim),
              patch.object(play_spike2.padcontroller, "read_block", return_value=b"LED1"),
              patch.object(play_spike2.padcontroller.coilmap, "has_magic", return_value=True),
              patch.object(play_spike2.padcontroller, "SHARE_ON", True),
              patch.object(play_spike2.subprocess, "Popen", side_effect=lambda *a, **k: events.append("VPX start") or vpx)):
            result = play_spike2.play(launch, self.root / "vpx.exe", self.root / "Rush.vpx", 10,
                                      lighting={"enabled": True, "speaker_fallback": fallback},
                                      lighting_report=report)
        self.assertEqual(result, 9)
        reported_roles, notice = report.call_args.args
        self.assertEqual(reported_roles, roles)
        if expected:
            self.assertEqual(events, ["claim", "VPX start", "VPX exit", "lighting close", "rig stop"])
            self.assertEqual(claim.call_args.args[1],
                             [(fixture, role, pixels)
                              for (fixture, _, pixels), role in zip(mappings, expected)])
            self.assertEqual("Speaker lighting on" in notice, "speaker" in expected)
        else:
            claim.assert_not_called()
            self.assertEqual(events, ["VPX start", "VPX exit", "rig stop"])
            self.assertIn("usual DOF setup", notice)
