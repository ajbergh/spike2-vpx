from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from spike2_vpx.player import Game, Player, supported_tables
from spike2_vpx_setup import Setup


@pytest.fixture
def player(tmp_path):
    setup = Setup(tmp_path)
    yield Player(setup)
    setup.log.handlers[0].close()


def test_catalog_lists_bundled_table_versions_for_supported_game_editions():
    rows = supported_tables()
    godzilla = next(row for row in rows if row.game == "Godzilla LE")
    assert godzilla.table == "Godzilla Limited Edition v2.6"
    assert godzilla.url.startswith("https://")
    assert {"Foo Fighters LE", "Foo Fighters Pro"} <= {row.game for row in rows}
    assert "Turtles LE" not in {row.game for row in rows}


def test_image_title_comes_from_the_content_probe(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    image = tmp_path / "custom name.RAW"
    image.write_bytes(b"original")
    monkeypatch.setattr(module.padlaunch.padpath, "to_wsl_many",
                        lambda paths, distro: ["/rig", "/mnt/c/custom name.RAW"])
    player.setup.wsl = Mock(return_value=CompletedProcess([], 0, b'{"title":"godzilla_le"}', b""))
    assert player.inspect_image(image) == Game("godzilla_le", str(image))
    player.setup.wsl.assert_called_once_with("python3", "-B", "/rig/inspect_vpx_card.py",
                                             "/mnt/c/custom name.RAW", timeout=150)
    assert image.read_bytes() == b"original"


def test_rejects_prepared_table_for_another_edition(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    table = tmp_path / "Jaws (PAD).vpx"
    table.touch()
    monkeypatch.setattr(module.play_spike2, "embedded_script",
                        lambda _: 'Controller.GameName = "jaws_le"\nCreateObject("PAD.Controller")')
    with pytest.raises(ValueError, match="prepared for jaws_le"):
        player.prepare_table(Game("godzilla_le", "card.raw"), table, Mock())
    assert not player.library()


def test_conversion_preserves_source_and_saves_the_pairing(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    table = tmp_path / "Original.vpx"
    table.write_bytes(b"original table")
    patched = 'Controller.GameName = "godzilla_le"\nCreateObject("PAD.Controller")'
    monkeypatch.setattr(module.play_spike2, "embedded_script",
                        lambda path: "Godzilla SEGA.VBS" if path == table else patched)

    def patch(path, *, outdir, title, log):
        assert path == str(table) and title == "godzilla_le"
        (Path(outdir) / "Godzilla limited edition (PAD).vpx").write_bytes(b"prepared")
        return 0

    monkeypatch.setattr(module.padvpx, "patch", patch)
    game = player.prepare_table(Game("godzilla_le", "card.raw"), table, Mock())
    player.save(game)
    assert table.read_bytes() == b"original table"
    assert Path(game.table).read_bytes() == b"prepared"
    assert Path(game.table).with_name("PADSPIKE2.vbs").is_file()
    reopened = Setup(tmp_path)
    try:
        assert Player(reopened).library() == [game]
        player.remove(game)
        assert player.library() == []
        assert Path(game.table).is_file()
    finally:
        reopened.log.handlers[0].close()


def test_play_refuses_a_saved_image_replaced_with_another_title(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    player.setup.vpx_executable = Mock(return_value=tmp_path / "VPinballX64.exe")
    player.setup.check_vpx = Mock(return_value=Mock(state="Ready"))
    monkeypatch.setattr(module.play_spike2, "check_controller", Mock())
    player.inspect_image = Mock(return_value=Game("jaws_le", "card.raw"))
    launch = Mock()
    monkeypatch.setattr(module.padlaunch, "Launch", launch)
    with pytest.raises(ValueError, match="different title"):
        player.play(Game("godzilla_le", "card.raw", "table.vpx"), Mock())
    launch.assert_not_called()
