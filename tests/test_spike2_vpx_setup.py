from pathlib import Path
import runpy
from subprocess import CompletedProcess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import spike2_vpx_setup as setup


def result(code=0, output=b""):
    return CompletedProcess([], code, output, b"")


def mount_error(disk, code="MountDisk/ERROR_PATH_NOT_FOUND"):
    return result(1, (f"Failed to attach disk '{disk}' to WSL2.\n"
                      f"Error code: Wsl/Service/CreateInstance/{code}").encode("utf-16-le"))


class StandaloneSetupTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = setup.Setup(folder.name)
        self.addCleanup(self.app.log.handlers[0].close)

    def test_reboot_survives_relaunch_and_clears_after_a_new_boot(self):
        with patch.object(setup, "boot_session", return_value="100"), \
                patch.object(setup, "elevated", return_value=result(3010)):
            self.app.install_platform("Install")
            reopened = setup.Setup(self.app.data)
            self.addCleanup(reopened.log.handlers[0].close)
            self.assertEqual(reopened.check_wsl().state, "Restart required")
        with patch.object(setup, "boot_session", return_value="200"), \
                patch.object(setup, "run", return_value=result()):
            self.assertEqual(reopened.check_wsl().state, "Ready")
        self.assertNotIn("restart_boot", reopened.settings_path.read_text())

    def test_wsl_failure_has_a_retry_and_does_not_set_a_restart(self):
        def checks():
            return {"wsl": self.app.probe("wsl", self.app.check_wsl)}

        with patch.object(self.app, "checks", side_effect=checks), \
                patch.object(setup, "boot_session", return_value="100"), \
                patch.object(setup, "elevated", return_value=result(1)), \
                patch.object(setup, "run", return_value=result(1)):
            check = self.app.repair("wsl", "install_wsl")["wsl"]
        self.assertEqual((check.state, check.action), ("Setup failed", "install_wsl"))
        self.assertNotIn("restart_boot", self.app.settings)
        self.assertIn("Elevated WSL command", self.app.log_path.read_text(encoding="utf-8"))

    def test_wsl_install_prompt_offers_automated_install_even_on_success_exit(self):
        prompt = "To install, run 'wsl.exe --install'.".encode("utf-16-le")
        with patch.object(setup, "run", return_value=result(output=prompt)):
            check = self.app.check_wsl()
        self.assertEqual((check.state, check.action), ("Missing", "install_wsl"))

    def test_wsl_virtualization_error_offers_firmware_guidance(self):
        for code in (b"HCS_E_HYPERV_NOT_INSTALLED", b"0x80370102"):
            with patch.object(setup, "run", return_value=result(output=code)):
                check = self.app.check_wsl()
            self.assertEqual(check.state, "Needs attention")
            self.assertIn("Intel VT-x or AMD-V", check.detail)

        with patch.object(setup, "distro_registration", return_value=(self.app.runtime_dir, 2)), \
                patch.object(self.app, "wsl", return_value=result(1, b"HCS_E_HYPERV_NOT_INSTALLED")):
            check = self.app.probe("runtime", self.app.check_runtime)
        self.assertEqual(check.state, "Needs attention")
        self.assertIn("BIOS/UEFI", check.detail)

    def test_wslg_requires_session_sockets_for_ready(self):
        version = result(output="WSL version: 2.6.0\r\nWSLg version: 1.0.71\r\n".encode("utf-16-le"))
        with patch.object(setup, "run", return_value=version), \
                patch.object(self.app, "wsl", side_effect=[result(1), result()]) as wsl:
            self.assertEqual(self.app.check_wslg(setup.Check("Missing", "")).state, "Detected")
            self.assertEqual(self.app.check_wslg(setup.Check("Ready", "")).state, "Needs attention")
            self.assertEqual(self.app.check_wslg(setup.Check("Ready", "")).state, "Ready")
        self.assertIn("PULSE_SERVER", wsl.call_args.args[-1])

    def test_runtime_only_accepts_its_own_full_wsl2_distro(self):
        with patch.object(setup, "distro_registration", return_value=(Path("another"), 2)), \
                patch.object(self.app, "wsl") as wsl:
            self.assertEqual(self.app.check_runtime().state, "Needs attention")
            wsl.assert_not_called()
        with patch.object(setup, "distro_registration", return_value=(self.app.runtime_dir, 1)):
            self.assertEqual(self.app.check_runtime().action, "convert_runtime")
        manifest = result(output=(
            f'{{"runtime_version": {setup.runtime.RUNTIME_VERSION}, "variant": "full"}}'.encode()
        ))
        with patch.object(setup, "distro_registration", return_value=(self.app.runtime_dir, 2)), \
                patch.object(self.app, "wsl", side_effect=[manifest, result(output=b"/mnt/c/Program Files"), result()]) as wsl, \
                patch.object(self.app, "arm_execution", return_value=result()) as arm:
            self.assertEqual(self.app.check_runtime().state, "Ready")
        self.assertEqual(wsl.call_args.args[-1], "/mnt/c/Program Files")
        arm.assert_called_once_with("/mnt/c/Program Files", "check")

    def test_missing_runtime_disk_offers_reinstall_only_for_an_empty_owned_folder(self):
        base = self.app.runtime_dir
        disk = base / "ext4.vhdx"
        with patch.object(setup, "distro_registration", return_value=(base, 2)), \
                patch.object(self.app, "wsl", return_value=mount_error(disk)) as wsl:
            check = self.app.check_runtime()
            self.assertEqual((check.state, check.action, check.missing_disk),
                             ("Repair needed", setup.REINSTALL_RUNTIME, disk))
            base.mkdir()
            self.assertEqual(self.app.check_runtime().action, setup.REINSTALL_RUNTIME)
            disk.touch()
            check = self.app.check_runtime()
            self.assertEqual((check.state, check.action), ("Needs attention", ""))
            disk.unlink()
            wsl.return_value = mount_error(disk, "CreateVm/HCS/ERROR_PATH_NOT_FOUND")
            check = self.app.probe("runtime", self.app.check_runtime)
            self.assertEqual((check.state, check.action), ("Check failed", ""))
            wsl.return_value = mount_error(disk.with_name("system.vhdx"))
            self.assertEqual(self.app.probe("runtime", self.app.check_runtime).action, "")

    def test_runtime_reinstall_verifies_archive_before_unregister_and_uses_selected_folder(self):
        base = self.app.data / "runtime"
        disk = base / "ext4.vhdx"
        selected = self.app.data / "new runtime"
        operations = Mock()
        operations.verify.return_value = (True, "")
        operations.unregister.return_value = result()
        with patch.object(setup.storage, "installed_folder", return_value=selected), \
                patch.object(setup, "distro_registration", side_effect=[(base, 2), (base, 2), None]), \
                patch.object(self.app, "wsl", return_value=mount_error(disk, "MountVhd/HCS/ERROR_FILE_NOT_FOUND")), \
                patch.object(setup.payloads, "verify", operations.verify), \
                patch.object(setup.runtime, "_need_room"), \
                patch.object(setup, "run", operations.unregister), \
                patch.object(setup.runtime, "import_image", operations.import_image):
            self.app.reinstall_runtime(disk, None)
        self.assertEqual([call[0] for call in operations.mock_calls], ["verify", "unregister", "import_image"])
        operations.unregister.assert_called_once_with(["wsl.exe", "--unregister", setup.DISTRO], timeout=120)
        self.assertEqual(operations.import_image.call_args.args[1], str(selected.resolve()))
        self.assertEqual(self.app.settings["runtime_dir"], str(selected.resolve()))

    def test_runtime_reinstall_keeps_registration_if_download_fails_or_disk_returns(self):
        base = self.app.runtime_dir
        disk = base / "ext4.vhdx"

        def restored_disk(progress):
            base.mkdir()
            disk.touch()
            return self.app.data / "download.tar"

        with patch.object(setup, "distro_registration", return_value=(base, 2)), \
                patch.object(self.app, "wsl", return_value=mount_error(disk)), \
                patch.object(setup, "run") as run:
            with patch.object(self.app, "runtime_archive", side_effect=RuntimeError("Download failed")):
                with self.assertRaisesRegex(RuntimeError, "Download failed"):
                    self.app.reinstall_runtime(disk, None)
            with patch.object(self.app, "runtime_archive", side_effect=restored_disk):
                with self.assertRaisesRegex(RuntimeError, "needs an empty folder"):
                    self.app.reinstall_runtime(disk, None)
            run.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows desktop setup")
    def test_desktop_setup_runs_automatic_steps_and_requires_runtime_confirmation(self):
        desktop = runpy.run_path(str(setup.APP / "Spike2Vpx.pyw"))
        player = desktop["PlayerWindow"]
        disk = self.app.runtime_dir / "ext4.vhdx"
        checks = {"runtime": setup.Check("Repair needed", "", setup.REINSTALL_RUNTIME, disk),
                  "controller": setup.Check("Missing", "", "register_controller")}
        self.assertEqual(player.next_step(checks), ("controller", "register_controller"))
        checks["controller"] = setup.Check("Ready", "")
        self.assertIsNone(player.next_step(checks))
        window = Mock(checks=checks)
        player.setup_finished(window, checks)
        window.fix_setup_item.assert_called_once_with("runtime")

        with patch.object(desktop["messagebox"], "askyesno", side_effect=[False, True]) as confirm:
            player.fix_setup_item(window, "runtime")
            window.start.assert_not_called()
            player.fix_setup_item(window, "runtime")
        self.assertEqual(confirm.call_args.kwargs["default"], desktop["messagebox"].NO)
        window.start.assert_called_once()
        window.start.call_args.args[0]()
        window.setup.repair.assert_called_once_with("runtime", setup.REINSTALL_RUNTIME,
                                                    progress=window.report_activity, missing_disk=disk)

    def test_runtime_converts_only_its_own_wsl1_distribution_as_the_player(self):
        with patch.object(setup, "distro_registration", return_value=(Path("another"), 1)), \
                patch.object(setup, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "this app's"):
                self.app.convert_runtime()
            run.assert_not_called()
        with patch.object(setup, "distro_registration", return_value=(self.app.runtime_dir, 1)), \
                patch.object(setup, "run", return_value=result()) as run:
            self.app.convert_runtime()
        run.assert_called_once_with(["wsl.exe", "--set-version", setup.DISTRO, "2"], timeout=1800)

    def test_runtime_repairs_missing_arm_handler_and_only_reports_ready_after_probe(self):
        manifest = result(output=(
            f'{{"runtime_version": {setup.runtime.RUNTIME_VERSION}, "variant": "full"}}'.encode()
        ))
        for recovery, expected in ((result(), ("Ready", "")),
                                   (result(1, b"registration refused"), ("Repair needed", "repair_arm"))):
            with patch.object(setup, "distro_registration", return_value=(self.app.runtime_dir, 2)), \
                    patch.object(self.app, "wsl", side_effect=[manifest, result(output=b"/rig"), result()]), \
                    patch.object(self.app, "arm_execution", side_effect=[result(10), recovery]) as arm:
                check = self.app.check_runtime()
            self.assertEqual((check.state, check.action), expected)
            self.assertEqual([call.args for call in arm.call_args_list], [("/rig", "check"), ("/rig", "repair")])

    def test_missing_runtime_import_uses_verified_file_and_private_storage(self):
        with patch.object(setup, "distro_registration", return_value=None), \
                patch.object(setup.payloads, "verify", return_value=(True, "")), \
                patch.object(setup.runtime, "import_image") as import_image:
            self.app.install_runtime()
        distro, target, archive = import_image.call_args.args
        self.assertEqual(distro, "SPIKE2-VPX")
        self.assertEqual(Path(target), self.app.runtime_dir.resolve())
        self.assertEqual(Path(archive).parent, self.app.data / "downloads")

    def test_runtime_install_reports_download_verification_and_import(self):
        events = []

        def download(image, destination, *, log, progress, verifying):
            progress(image.size, image.size)
            verifying()

        with patch.object(setup, "distro_registration", return_value=None), \
                patch.object(setup.payloads, "verify", return_value=(False, "missing")), \
                patch.object(setup.runtime, "_need_room"), patch.object(setup.runtime, "import_image"), \
                patch.object(setup.payloads, "download", side_effect=download):
            self.app.install_runtime(lambda *event: events.append(event))

        size = setup.runtime.IMAGE.size
        self.assertEqual(events, [
            ("Checking for a verified SPIKE 2 runtime download…",),
            ("Downloading the SPIKE 2 runtime…", 0, size),
            ("Downloading the SPIKE 2 runtime…", size, size),
            ("Verifying the runtime download…",),
            ("Installing the SPIKE 2 runtime in WSL…",),
        ])

    @unittest.skipUnless(sys.platform == "win32", "Windows installer paths")
    def test_installer_runtime_folder_is_used_for_import_and_remembered(self):
        install = self.app.data / "App"
        install.mkdir()
        selected = self.app.data / "Another drive folder"
        config = install / "storage.ini"
        config.write_text(f"[Storage]\nruntime = {selected}\n", encoding="utf-8-sig")
        with patch.object(setup, "APP", install), \
                patch.object(setup, "distro_registration", return_value=None), \
                patch.object(setup.payloads, "verify", return_value=(True, "")), \
                patch.object(setup.runtime, "import_image") as import_image:
            self.assertTrue(self.app.owns_runtime(self.app.data / "runtime"))
            self.app.install_runtime()
            self.assertEqual(Path(import_image.call_args.args[1]), selected.resolve())
            config.write_text("[Storage]\nruntime =\n", encoding="utf-8")
            reopened = setup.Setup(self.app.data)
            self.addCleanup(reopened.log.handlers[0].close)
            self.assertTrue(reopened.owns_runtime(selected))
            self.assertFalse(reopened.owns_runtime(self.app.data / "runtime"))

    def test_vpx_does_not_read_the_inherited_kit_and_saves_browse_selection(self):
        with patch.object(setup.vpxsetup, "find_vpx", return_value="") as find:
            self.assertEqual(self.app.check_vpx().state, "Missing")
        find.assert_called_once_with(use_kit=False)
        path = self.app.data / "VPinballX64.exe"
        path.touch()
        self.app.choose_vpx(path)
        with patch.object(setup.vpxsetup, "vpx_version", side_effect=[(10, 7, 4), (10, 8, 0)]):
            self.assertEqual(self.app.check_vpx().state, "Needs attention")
            self.assertEqual(self.app.check_vpx().state, "Ready")
        self.assertIn(path.name, self.app.settings_path.read_text())

    def test_controller_probe_never_offers_to_take_over_another_installation(self):
        with patch.object(setup, "run", side_effect=[
            result(output=b'["Needs attention","PAD.Controller belongs to another installation."]'),
            result(output=b'["Missing","Register the bundled server."]'),
        ]):
            self.assertEqual(self.app.check_controller().action, "")
            self.assertEqual(self.app.check_controller().action, "register_controller")

    @unittest.skipUnless(sys.platform == "win32", "Windows registry")
    def test_controller_mutations_refuse_foreign_machine_registration(self):
        import standalone_controller as controller

        with patch.object(controller, "registration", return_value="foreign"), \
                patch.object(controller.padcontroller, "main") as change:
            with self.assertRaisesRegex(RuntimeError, "another installation"):
                controller.change("register")
            controller.change("unregister")
            change.assert_not_called()
