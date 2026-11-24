"""Prepared VPX launch ordering and ownership, without starting WSL or VPX."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "spike2_emu" / "vpx"))
import play_spike2 as play  # noqa: E402


class PreparedLaunchTest(unittest.TestCase):
    def setUp(self):
        logs = TemporaryDirectory()
        self.addCleanup(logs.cleanup)
        redirected = patch.object(play.padlaunch, "LOG", str(Path(logs.name) / "controller.log"))
        redirected.start()
        self.addCleanup(redirected.stop)

    def test_raw_image_detection_prefers_a_unique_title_match(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            image = folder / "godzilla_le-1_16_0_spike2.Release.8G.sdcard.RAW"
            image.touch()
            (folder / "godzilla_pro.sdcard.raw").touch()
            self.assertEqual(play.card_in_folder(folder, "godzilla_le"), image)
            (folder / "godzilla_le-1_14_0.sdcard.raw").touch()
            with self.assertRaisesRegex(ValueError, "Multiple .raw images.*-Card"):
                play.card_in_folder(folder, "godzilla_le")

    def test_raw_image_detection_accepts_a_single_custom_filename(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "video").mkdir()
            image = folder / "custom build.raw"
            image.touch()
            self.assertEqual(play.card_in_folder(folder, "godzilla_le"), image)

    def test_prepared_run_skips_the_card_and_standalone_helpers(self):
        mod = play.padlaunch
        with patch.object(mod.padpath, "to_wsl", return_value="/rig") as to_wsl, \
                patch.object(mod, "no_linux", return_value=""), \
                patch.object(mod.Launch, "base_rig", return_value=""), \
                patch.object(mod.Launch, "_start_watch") as watch:
            launch = mod.Launch("godzilla_le", None, dist="PAD-Runtime", minutes=0,
                                game_dir="/mnt/d/temp/stern firmware/godzilla")
            launch.slot = 2
            launch.board_env = Mock(return_value=[])
            launch.cache_check = Mock()
            launch.start()

        watch.assert_called_once_with()
        launch.cache_check.assert_not_called()
        to_wsl.assert_called_once_with(mod.RIG, "PAD-Runtime")
        env = dict(value.split("=", 1) for value in launch.env())
        self.assertEqual(env["PAD_GAME_DIR"], "/mnt/d/temp/stern firmware/godzilla")
        self.assertEqual(env["PAD_GAME_DIR_TITLE"], "godzilla_le")
        self.assertEqual(env["PAD_AUDIO"], "1")
        for name in ("PLAYFIELD", "HIDDEN", "BALL_FEED", "TOPPER", "RECORD"):
            self.assertEqual(env["PAD_" + name], "0")
        self.assertEqual(env["PAD_CARD"], "")

    def test_prepared_card_uses_card_mount_without_the_virtual_playfield(self):
        mod = play.padlaunch
        image = "/mnt/d/temp/stern firmware/godzilla/godzilla_le.sdcard.raw"
        with patch.object(mod.padpath, "to_wsl", side_effect=lambda path, _: path), \
                patch.object(mod, "no_linux", return_value=""), \
                patch.object(mod, "win_python", return_value=None), \
                patch.object(mod, "log"), \
                patch.object(mod.Launch, "base_rig", return_value=""), \
                patch.object(mod.Launch, "_start_watch") as watch:
            launch = mod.Launch("godzilla_le", image, dist="PAD-Runtime", minutes=0, prepared=True)
            launch.slot = 2
            launch.board_env = Mock(return_value=[])
            launch.cache_check = Mock(return_value=None)
            launch.cache_env = Mock(return_value=[])
            launch.start()
            env = dict(value.split("=", 1) for value in launch.env())

        launch.cache_check.assert_called_once_with()
        watch.assert_called_once_with()
        self.assertEqual(env["PAD_CARD"], image)
        self.assertEqual(env["PAD_PLAYFIELD"], "0")
        self.assertEqual(env["PAD_SELECT"], "0")

    def test_card_prepares_the_guest_filesystem_before_starting_the_rig(self):
        mod = play.padlaunch
        with patch.object(mod.padpath, "to_wsl", return_value="/mnt/c/Game Card.raw"), \
                patch.object(mod, "no_linux", return_value=""), \
                patch.object(mod.Launch, "base_rig", return_value="/home/pad/spike2root"), \
                patch.object(mod.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as prepare:
            launch = mod.Launch("godzilla_le", "C:/Game Card.raw", dist="SPIKE2-VPX", prepared=True)
            launch.slot = 2
            launch.cache_check = Mock(return_value=None)
            launch._start_watch = Mock(side_effect=lambda: prepare.assert_called_once())
            launch.start()

        self.assertEqual(prepare.call_args.args[0][-2:],
                         ["/home/pad/spike2root", "/mnt/c/Game Card.raw"])
        launch._start_watch.assert_called_once_with()

    def test_vpx_loads_while_stern_starts_and_exit_releases_the_run(self):
        calls = []
        launch = SimpleNamespace(title="godzilla_le", slot=2, start=lambda: calls.append("game"),
                                 stop=lambda: calls.append("stop"))
        exe = Path("VPX Install/VPinballX64.exe")
        table = Path("Tables/Godzilla (PAD).vpx")
        rig = SimpleNamespace(distro="PAD-Runtime", root="/slots/2/root", prepared_launch=True)
        proc = MagicMock()
        proc.__enter__.return_value.wait.side_effect = lambda: calls.append("wait VPX") or 0

        with patch.object(play.padcontroller.Rig, "board_run", return_value=None), \
                patch.object(play, "wait_for_rig", side_effect=lambda *_: calls.append("rig") or rig), \
                patch.object(play, "wait_for_game", side_effect=lambda *_: calls.append("ready") or rig), \
                patch.object(play.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch.object(play.subprocess, "Popen", side_effect=lambda *_a, **_k:
                             calls.append("open VPX") or proc) as popen:
            self.assertEqual(play.play(launch, exe, table, 30, overlap=True), 0)
        self.assertEqual(calls, ["game", "rig", "open VPX", "ready", "wait VPX", "stop"])
        self.assertEqual(popen.call_args.args, ([str(exe), "-ExtMinimized", "-play", str(table)],))
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["PAD_ROOT_WSL"], rig.root)
        self.assertEqual(env["PAD_SLOT"], "2")
        self.assertEqual(env["PAD_VPX_LINK"], "1")
        self.assertEqual(env["PAD_WSL_DISTRO"], rig.distro)

    def test_older_controller_waits_for_emulator_before_starting_player(self):
        launch = Mock(title="godzilla_le", slot=2)
        rig = SimpleNamespace(distro="SPIKE2-VPX", root="/slots/2/root", prepared_launch=True)
        with patch.object(play.padcontroller.Rig, "board_run", return_value=None), \
                patch.object(play, "wait_for_rig", return_value=rig), \
                patch.object(play, "wait_for_game", side_effect=RuntimeError("boot failed")), \
                patch.object(play.subprocess, "Popen") as popen, \
                self.assertRaisesRegex(RuntimeError, "boot failed"):
            play.play(launch, Path("VPinballX64.exe"), Path("table.vpx"), 30)
        popen.assert_not_called()
        launch.stop.assert_called_once_with()

    def test_failed_emulator_during_overlap_closes_only_the_player_it_started(self):
        launch = Mock(title="godzilla_le", slot=2)
        rig = SimpleNamespace(distro="SPIKE2-VPX", root="/slots/2/root", prepared_launch=True)
        with patch.object(play.padcontroller.Rig, "board_run", return_value=None), \
                patch.object(play, "wait_for_rig", return_value=rig), \
                patch.object(play, "wait_for_game", side_effect=RuntimeError("boot failed")), \
                patch.object(play.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                patch.object(play.subprocess, "Popen") as popen, \
                self.assertRaisesRegex(RuntimeError, "boot failed"):
            proc = popen.return_value.__enter__.return_value
            proc.poll.return_value = None
            play.play(launch, Path("VPinballX64.exe"), Path("table.vpx"), 30, overlap=True)
        proc.terminate.assert_called_once_with()
        launch.stop.assert_called_once_with()

    def test_closing_player_while_game_starts_skips_the_boot_wait(self):
        launch = Mock(title="godzilla_le")
        player = Mock()
        player.poll.return_value = 0
        with patch.object(play.padcontroller, "block_in_wsl") as probe:
            self.assertFalse(play.wait_for_game(launch, object(), play.time.monotonic() + 30, player))
        probe.assert_not_called()

    def test_rig_wait_ignores_other_launchers_and_rejects_the_wrong_game(self):
        launch = Mock(title="godzilla_le", slot=2, label="PAD-VPX-321")
        foreign = {"game": "godzilla_le", "label": "PAD-VPX-456"}
        wrong_title = {"game": "godzilla_pro", "label": "PAD-VPX-321"}
        with patch.object(play.padcontroller.Rig, "board_slot", side_effect=[foreign, wrong_title]) as board, \
                patch.object(play.time, "sleep"), self.assertRaisesRegex(RuntimeError, "different title"):
            play.wait_for_rig(launch, play.time.monotonic() + 30)
        self.assertEqual(board.call_count, 2)

    def test_controller_waits_for_prepared_launcher_without_starting_another_game(self):
        pc = play.padcontroller
        bridge = pc.Bridge()
        bridge.set_game("godzilla_le")
        record = {"label": "PAD-VPX-321", "root": "/slots/2/root", "slot": 2,
                  "game": bridge.native_title, "distro": "SPIKE2-VPX"}
        with patch.dict(pc.os.environ, {"PAD_ROOT_WSL": ""}), \
                patch.object(pc.Rig, "board_run", return_value=record), \
                patch.object(pc, "read_block", return_value=None), \
                patch.object(pc, "PlayerGrips"), \
                patch.object(pc.threading, "Thread"), \
                patch.object(pc.padlaunch, "Launch") as start, \
                patch.object(pc.padlaunch, "card_for") as card:
            bridge.run()
        start.assert_not_called()
        card.assert_not_called()
        self.assertIsNone(bridge.launch)
        self.assertEqual(bridge.external_start, record["label"])
        block = b"test block"
        with patch.object(pc, "SHARE_ON", True), patch.object(pc, "read_block", return_value=block), \
                patch.object(bridge, "_rig_up", return_value=True), patch.object(bridge, "_attach") as attach:
            bridge._poll_starting()
        attach.assert_called_once_with(block)

    def test_failed_game_start_never_opens_vpx_and_cleans_up(self):
        launch = Mock(title="godzilla_le")
        launch.start.side_effect = RuntimeError("game died")
        with patch.object(play.padcontroller.Rig, "board_run", return_value=None), \
                patch.object(play.subprocess, "Popen") as popen, \
                self.assertRaisesRegex(RuntimeError, "game died"):
            play.play(launch, Path("VPinballX64.exe"), Path("table.vpx"), 30)
        popen.assert_not_called()
        launch.stop.assert_called_once_with()
