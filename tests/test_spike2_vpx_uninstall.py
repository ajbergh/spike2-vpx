from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

import spike2_vpx_uninstall as uninstall


def test_uninstall_stops_and_unregisters_only_its_runtime(tmp_path):
    base = tmp_path / "runtime"
    setup = uninstall.Setup(tmp_path, log_to_file=False)
    done = CompletedProcess([], 0, b"", b"")
    with patch.object(uninstall, "distro_registration", side_effect=[(base, 2), (base, 2), None]), \
            patch.object(uninstall, "run", return_value=done) as run:
        uninstall.remove_runtime(setup)
    assert [call.args[0][1:] for call in run.call_args_list] == [
        ["--terminate", uninstall.DISTRO], ["--unregister", uninstall.DISTRO],
    ]


@pytest.mark.parametrize("registration", [None, (Path("another runtime"), 2)])
def test_uninstall_leaves_absent_or_foreign_runtime_alone(registration, tmp_path):
    setup = uninstall.Setup(tmp_path, log_to_file=False)
    with patch.object(uninstall, "distro_registration", return_value=registration), \
            patch.object(uninstall, "run") as run:
        uninstall.remove_runtime(setup)
    run.assert_not_called()


def test_uninstall_reports_failed_runtime_removal(tmp_path):
    setup = uninstall.Setup(tmp_path, log_to_file=False)
    with patch.object(uninstall, "distro_registration", return_value=(setup.runtime_dir, 2)), \
            patch.object(uninstall, "run", return_value=CompletedProcess([], 1, b"Access denied", b"")):
        with pytest.raises(RuntimeError, match="Access denied"):
            uninstall.remove_runtime(setup)


def test_uninstall_cleans_local_data_and_dedicated_cache_only(tmp_path):
    data = tmp_path / "Spike2Vpx"
    downloads = data / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "runtime.tar.gz").touch()
    (data / "setup.json").touch()
    (data / "vpx.json").write_text('{"card_cache": false}', encoding="utf-8")
    (data / "vpx.json.tmp").touch()
    backup = data / "my-library-backup.json"
    backup.touch()
    cache = tmp_path / "Cards" / "SPIKE2-VPX-cardcache"
    imports = cache / "imports"
    imports.mkdir(parents=True)
    (imports / "game.raw").touch()
    original = cache.parent / "game.zip"
    original.touch()

    uninstall.remove_player_files(data, [cache])
    uninstall.remove_player_files(data, [cache])

    assert list(data.iterdir()) == [backup]
    assert list(cache.parent.iterdir()) == [original]
