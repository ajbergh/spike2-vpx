import os
from pathlib import Path
from subprocess import CompletedProcess, run
from unittest.mock import Mock
from zipfile import ZipFile

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


def test_zip_import_extracts_one_verified_card_without_using_archive_paths(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    source = tmp_path / "Stern image.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("../../card.RAW", b"card")
        archive.writestr("readme.txt", b"instructions")
    original = source.read_bytes()

    def inspect(path):
        assert path.read_bytes() == b"card"
        return Game("godzilla_le", str(path))

    player.inspect_image = inspect
    monkeypatch.setattr(module.padlaunch, "cache_on", lambda: False)
    game, _ = player.prepare_image(source)
    assert game.title == "godzilla_le"
    assert Path(game.image).parent == tmp_path / "images"
    assert Path(game.image).read_bytes() == b"card"
    assert list((tmp_path / "images").iterdir()) == [Path(game.image)]
    assert source.read_bytes() == original


def test_zip_import_rejects_ambiguous_images_and_cleans_failed_verification(player, tmp_path):
    source = tmp_path / "Stern image.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("card1.raw", b"card")
        archive.writestr("card2.raw", b"card")
    with pytest.raises(ValueError, match="one non-empty"):
        player.import_archive(source)
    with ZipFile(source, "w") as archive:
        archive.writestr("card1.raw", b"card")
    player.inspect_image = Mock(side_effect=ValueError("Not a SPIKE 2 image"))
    with pytest.raises(ValueError, match="Not a SPIKE 2 image"):
        player.import_archive(source)
    assert not list((tmp_path / "images").iterdir())


def test_cabinet_uses_device_identity_and_preflights_backglass(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module
    from spike2_vpx import cabinet

    screens = [
        {"n": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "device": "play"},
        {"n": 2, "x": -1280, "y": 0, "w": 1280, "h": 720, "primary": False, "device": "lcd"},
        {"n": 3, "x": 0, "y": -720, "w": 1280, "h": 720, "primary": False, "device": "glass",
         "display_number": 4},
    ]
    image, table = tmp_path / "card.raw", tmp_path / "Godzilla (PAD).vpx"
    image.touch()
    table.touch()
    game = Game("godzilla_le", str(image), str(table))
    kit_save = Mock()
    monkeypatch.setattr(module.padlaunch, "kit_save", kit_save)
    monkeypatch.setattr(module.padlaunch, "kit_load", lambda: {})
    monkeypatch.setattr(module.padisplay, "monitors", lambda: screens)
    monkeypatch.setattr(cabinet, "backglass_available", lambda: True)
    layout = {"playfield": "play", "backglass": "glass", "lcd": "lcd"}
    player.save_layout(layout, screens)
    assert kit_save.call_args.args[0]["player"] == {"monitor": 1}
    assert "x=-1280" in (tmp_path / "rig-board" / "display.conf").read_text()
    assert "Attach a .directb2s" in player.preflight(game)[0]
    source = tmp_path / "original.directb2s"
    source.write_text("<DirectB2SData/>")
    companion = player.attach_backglass(game, source)
    assert companion.name == "Godzilla (PAD).directb2s"
    with pytest.raises(ValueError, match="already exists"):
        player.attach_backglass(game, source)
    assert not player.preflight(game)
    cabinet.apply(tmp_path / "rig-board", cabinet.resolve(layout, screens), screens, table)
    assert cabinet.padcabinet.parse_screenres(table.with_suffix(".res").read_text())[4] == "4"
    table.with_suffix(".res").write_text("custom")
    assert "custom .res" in player.preflight(game)[0]
    cabinet.apply(tmp_path / "rig-board", cabinet.resolve(layout, screens), screens, table)
    assert table.with_suffix(".res").read_text() == "custom"
    screens[1]["n"], screens[2]["n"] = 3, 2
    assert cabinet.resolve(layout, screens)["lcd"]["n"] == 3
    with pytest.raises(ValueError, match="different screen"):
        cabinet.resolve({"lcd": "play", "playfield": "play"}, screens)
    screens.pop()
    with pytest.raises(ValueError, match="backglass screen is disconnected"):
        player.preflight(game)


def test_play_defaults_to_borderless_fullscreen_on_a_spare_display(player, tmp_path, monkeypatch):
    from spike2_vpx import cabinet
    from spike2_vpx import player as module

    primary = {"n": 1, "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True, "device": "primary"}
    spare = {"n": 2, "x": -1280, "y": 0, "w": 1280, "h": 720, "primary": False, "device": "spare"}
    screens = [primary, spare]
    image, table = tmp_path / "card.raw", tmp_path / "Godzilla (PAD).vpx"
    image.touch()
    table.touch()
    game = Game("godzilla_le", str(image), str(table))
    player.setup.vpx_executable = Mock(return_value=tmp_path / "VPinballX64.exe")
    player.setup.check_vpx = Mock(return_value=Mock(state="Ready"))
    player.inspect_image = Mock(return_value=game)
    monkeypatch.setattr(module.padisplay, "monitors", lambda: screens)
    monkeypatch.setattr(module.play_spike2, "check_controller", Mock())
    monkeypatch.setattr(module.play_spike2.padcontroller.Rig, "board_run", Mock(return_value=None))
    monkeypatch.setattr(module.play_spike2, "embedded_script", lambda _: (
        'Controller.GameName = "godzilla_le"\nCreateObject("PAD.Controller")'))
    monkeypatch.setattr(module.padlaunch, "Launch", Mock())
    path = tmp_path / "rig-board" / "display.conf"

    def start(*_, **__):
        assert "monitor=2 mode=fullscreen border=0" in path.read_text()
        assert "x=-1280 y=0 w=1280 h=720 fs=1" in path.read_text()
        return 0

    monkeypatch.setattr(module.play_spike2, "play", start)
    assert player.play(game, Mock()) == 0
    assert "lcd" not in cabinet.resolve({}, [primary])
    assert "lcd" not in cabinet.resolve({"backglass": "spare"}, screens)
    assert "lcd" not in cabinet.resolve({"lcd": cabinet.WINDOWED_LCD}, screens)
    assert cabinet.resolve({"lcd": "primary"}, [primary])["lcd"] == primary


def test_moved_image_can_restore_the_saved_pairing(player, tmp_path):
    old = Game("godzilla_le", str(tmp_path / "missing.raw"), str(tmp_path / "table.vpx"))
    player.save(old)
    with pytest.raises(ValueError, match="Import image"):
        player.preflight(old)
    with pytest.raises(ValueError, match="Godzilla LE"):
        player.relink_image(old, Game("godzilla_pro", "wrong.raw"))
    game = player.relink_image(old, Game("godzilla_le", str(tmp_path / "new.raw")))
    assert player.library() == [game]
    assert game.table == old.table


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher")
def test_export_runs_from_another_folder_and_preserves_paths_and_exit_code(player, tmp_path, monkeypatch):
    from spike2_vpx import player as module

    app = tmp_path / "app & files"
    app.mkdir()
    monkeypatch.setattr(module, "APP", app)
    (app / "Play-Spike2Vpx.ps1").write_text(
        "param($Card, $Table, $AppGame)\n"
        "[IO.File]::WriteAllText($env:SPIKE2_EXPORT_TEST_ARGS, "
        "(ConvertTo-Json -Compress @($Card, $Table, $AppGame)))\n"
        "exit 37\n", encoding="ascii")
    image, table = tmp_path / "L’été 100%!.raw", tmp_path / "Godzilla's & Sons (PAD).vpx"
    image.touch()
    table.touch()
    monkeypatch.setattr(module.play_spike2, "embedded_script", lambda _: (
        'Controller.GameName = "godzilla_le"\nCreateObject("PAD.Controller")'))
    player.inspect_image = Mock(return_value=Game("godzilla_le", str(image)))
    script = tmp_path / "Launch Godzilla.bat"
    player.export(Game("godzilla_le", str(image), str(table)), script)
    result_file = tmp_path / "arguments.json"
    result = run(["cmd.exe", "/d", "/c", str(script)], cwd=app, capture_output=True, timeout=20,
                 env=dict(os.environ, SPIKE2_EXPORT_TEST_ARGS=str(result_file)))
    assert result.returncode == 37, result.stderr.decode(errors="replace")
    assert module.json.loads(result_file.read_text(encoding="utf-8-sig")) == [
        str(image), str(table), "godzilla_le"]
