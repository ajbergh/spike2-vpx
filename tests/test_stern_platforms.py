import json
import io
from pathlib import Path
import struct
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from spike2_vpx.player import Game, Player, game_from_row, supported_tables
from spike2_vpx_setup import Setup
from tools.spike3_emu import s3store, s3zip

import padcontroller
import padlaunch
import padstern
import padvpx
import play_spike2
import s1bridge
import spike3_image


@pytest.fixture
def player(tmp_path):
    setup = Setup(tmp_path)
    try:
        yield Player(setup)
    finally:
        for handler in setup.log.handlers:
            handler.close()


def test_bundled_table_maps_dispatch_to_their_own_runtime(monkeypatch):
    monkeypatch.setattr(padlaunch, "USER_MAPS", "")
    monkeypatch.setattr(padlaunch.padpath, "to_wsl", lambda path, _distro: str(path))
    cases = (
        ("ghostbusters_le_replica", "ghostbusters_le", "spike1", padstern.Spike1Launch),
        ("walking_dead_remastered_le", "walking_dead_remastered_le", "spike3", padstern.Spike3Launch),
    )
    for map_title, native_title, platform, backend in cases:
        mapping = padvpx.maps()[map_title]
        assert mapping["game"] == native_title
        assert mapping["platform"] == platform
        assert Path(padlaunch.HERE, mapping["patcher"]).is_file()
        assert padcontroller.VpxMap(map_title).platform == platform
        launch = padlaunch.launch_for(platform, map_title, "card.raw", dist="SPIKE2-VPX", prepared=True)
        assert isinstance(launch, backend) and launch.title == native_title
    assert {"Ghostbusters LE", "Walking Dead Remastered LE"} <= {row.game for row in supported_tables()}


def test_spike1_zip_uses_the_member_format_and_never_calls_spike2_cache(player, tmp_path, monkeypatch):
    archive = tmp_path / "ghostbusters.zip"
    with ZipFile(archive, "w") as card:
        card.writestr("../card.ISO", b"synthetic")
    inspect = Mock(side_effect=lambda path: Game("ghostbusters_le", str(path), platform="spike1"))
    monkeypatch.setattr(player, "inspect_image", inspect)
    monkeypatch.setattr(padlaunch, "Launch", Mock(side_effect=AssertionError("SPIKE 2 cache")))
    game, _ = player.prepare_image(archive)
    assert Path(game.image).suffix == ".iso"
    assert Path(game.image).read_bytes() == b"synthetic"
    assert game.platform == "spike1"
    assert game_from_row({"title": "godzilla_le", "image": "card.raw"}).platform == "spike2"


def test_spike3_import_retries_with_key_and_only_remembers_its_path(player, tmp_path, monkeypatch):
    card = tmp_path / "card.raw"
    card.touch()
    key = tmp_path / "owner.key"
    key.write_bytes(bytes(range(32)))
    monkeypatch.setattr(padlaunch.padpath, "to_wsl_many", lambda paths, _: paths)
    monkeypatch.setattr(spike3_image, "keyfile", lambda _image, selected: selected)
    monkeypatch.setattr(player.setup, "wsl", Mock(return_value=CompletedProcess(
        [], 0, b'{"platform":"spike3"}', b"")))

    def verify(_image, selected):
        if selected is None:
            raise spike3_image.KeyRequired("Select a key file.")
        assert spike3_image.read_key(selected) == bytes(range(32))
        return "walking_dead_remastered_le"

    monkeypatch.setattr(spike3_image, "title_for", verify)
    with pytest.raises(spike3_image.KeyRequired):
        player.prepare_image(card)
    game, _ = player.prepare_image(card, keyfile=key)
    assert game.platform == "spike3"
    assert player.setup.settings["spike3_keys"] == {game.title: str(key)}
    player.verify_pairing(game)


def test_key_file_formats_and_wrong_key_errors(tmp_path):
    key = tmp_path / "my-key.bin"
    key.write_bytes(bytes(range(32)))
    sidecar = tmp_path / "padkeys.json"
    sidecar.write_text(json.dumps({"otp_keyfile": key.name}), encoding="utf-8")
    assert spike3_image.read_key(key) == spike3_image.read_key(sidecar) == bytes(range(32))
    key.write_bytes(b"too short")
    with pytest.raises(spike3_image.KeyRequired):
        spike3_image.read_key(sidecar)


def test_spike1_state_drives_the_existing_switch_lamp_and_solenoid_contract(monkeypatch):
    bridge = padcontroller.Bridge()
    bridge.set_game("ghostbusters_le_replica")
    switch = s1bridge.slot(8, 48)
    coil = tuple(bridge.map.solenoids[1])
    raw_lamp, (led, *_) = next(iter(bridge.map.raw_lamp_rewrite.items()))
    block = bytearray(s1bridge.STATE_SIZE)
    offset = s1bridge.STATE_HEADER.size
    s1bridge.STATE_HEADER.pack_into(block, 0, s1bridge.STATE_MAGIC, 1, 123, 0)
    block[offset + switch] = 1
    block[offset + s1bridge.N_SLOTS + s1bridge.slot(*led) * 3] = 123
    block[offset + 4 * s1bridge.N_SLOTS + s1bridge.slot(*coil)] = 1
    monkeypatch.setattr(bridge, "_output", lambda: block)
    bridge.pipe = Mock(held={})
    bridge._poll_once()
    assert bridge.get_switch(1000 + switch)
    assert [raw_lamp, 123] in bridge.changed_lamps()
    assert [1, 1] in bridge.changed_solenoids()
    bridge.set_switch(1000 + switch, 1)
    bridge.pipe.set.assert_called_once_with(switch, 1)


def test_spike3_vpx_lamp_updates_fit_legacy_table_arrays():
    bridge = padcontroller.Bridge()
    bridge.set_game("walking_dead_remastered_le")
    lamp, leds = next((number, leds) for number, leds in bridge.map.raw_lamp_rewrite.items()
                      if len(leds) > 1)
    values = dict(zip(leds, (24, 201, 72)))
    bridge._led_level = lambda _data, node, index: values.get((node, index), 0)
    bridge._poll_lamps(b"")
    assert bridge.lamp_state[lamp] == 201
    assert all(number < 1000 for number, _ in bridge.changed_lamps())


def test_spike3_starts_with_a_stdin_key_and_only_stops_its_session(tmp_path, monkeypatch):
    key = tmp_path / "otp.key"
    key.write_bytes(bytes(range(32)))
    proc = Mock()
    proc.poll.return_value = None
    popen = Mock(return_value=proc)
    runner = Mock()
    monkeypatch.setattr(padlaunch, "LOG", str(tmp_path / "launcher.log"))
    monkeypatch.setattr(padlaunch, "BOARD", str(tmp_path / "board"))
    monkeypatch.setattr(padlaunch, "account", lambda _dist: ("pad", "/home/pad"))
    monkeypatch.setattr(padlaunch, "win_python", lambda: None)
    monkeypatch.setattr(padlaunch, "no_linux", lambda _dist: "")
    monkeypatch.setattr(padlaunch.padpath, "to_wsl", lambda path, _dist: str(path))
    monkeypatch.setattr(padlaunch, "_run", runner)
    monkeypatch.setattr(padstern.subprocess, "Popen", popen)

    launch = padstern.Spike3Launch("walking_dead_remastered_le", "card.raw", dist="SPIKE2-VPX",
                                   minutes=0, keyfile=key)
    assert launch.start() == 7
    args = popen.call_args.args[0]
    assert args[-6:] == ["spike3", launch.label, "7", "card.raw", "0", "walking_dead_remastered_le"]
    assert str(key) not in args
    proc.stdin.write.assert_called_once_with(bytes(range(32)))
    proc.stdin.close.assert_called_once()
    launch.stop()
    assert runner.call_args.args[0][-2:] == ["stop", launch.label]
    assert launch.slot is None


def test_spike3_prepared_card_needs_no_key_for_import_or_start(player, tmp_path, monkeypatch):
    title = "walking_dead_remastered_le"
    card = tmp_path / "card.raw"
    card.touch()
    monkeypatch.setattr(padstern, "prepared_spike3_title", lambda *_: title)
    monkeypatch.setattr(spike3_image, "keyfile", Mock(side_effect=AssertionError("OTP key lookup")))
    monkeypatch.setattr(padlaunch.padpath, "to_wsl_many", lambda paths, _: paths)
    monkeypatch.setattr(padlaunch.padpath, "to_wsl", lambda path, _: str(path))
    monkeypatch.setattr(player.setup, "wsl", Mock(return_value=CompletedProcess(
        [], 0, b'{"platform":"spike3"}', b"")))
    game, _ = player.prepare_image(card)
    assert game.title == title
    player.verify_pairing(game)
    launch = padstern.Spike3Launch(title, str(card), dist="SPIKE2-VPX")
    assert launch.input_key() is None
    launch.title = "different_title"
    with pytest.raises(ValueError, match="table expects different_title"):
        launch.input_key()


def test_store_title_requires_a_prepared_game_tree(tmp_path, monkeypatch):
    title = "walking_dead_remastered_le"
    games = tmp_path / "games"
    program = games / title / "game"
    program.parent.mkdir(parents=True)
    program.touch()
    monkeypatch.setattr(s3store.os, "readlink", lambda path: f"/games/{title}/game")
    assert s3store.prepared_title(tmp_path) is None
    (tmp_path / "rootfs").mkdir()
    assert s3store.prepared_title(tmp_path) == title
    program.unlink()
    assert s3store.prepared_title(tmp_path) is None


def test_spike1_single_session_and_spike3_slot_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(padlaunch, "BOARD", str(tmp_path))
    rig = {"emu": "spike1", "game": "GOT_LE", "root": "/home/pad/s1emu", "distro": "SPIKE2-VPX", "slot": 0}
    (tmp_path / "spike1-0.run").write_text(json.dumps(rig))
    assert padstern.board_record("spike1", "got_le") == rig
    assert padstern.board_record("spike3", slot=0) is None
    launch = padstern.Spike1Launch.__new__(padstern.Spike1Launch)
    with pytest.raises(RuntimeError, match="existing.*SPIKE 1"):
        launch.take_slot()


def test_compressed_zip_reader_seeks_through_zero_and_nonzero_chunks(monkeypatch):
    monkeypatch.setattr(s3zip, "CHUNK", 4096)
    monkeypatch.setattr(s3zip, "ZERO", bytes(4096))
    data = bytes(4096) + b"A" * 4096 + bytes(4096) + b"last"
    store = io.BytesIO()
    reader = s3zip.ChunkImage(io.BytesIO(data), store, len(data))
    assert store.tell() < 100
    reader.seek(4094)
    assert reader.read(8) == b"\0\0AAAAAA"
    reader.seek(-8, 2)
    assert reader.read(100) == b"\0\0\0\0last"
    reader.seek(0)
    assert reader.read(len(data)) == data


def test_spike3_zip_stays_the_library_image_and_reuses_its_store(player, tmp_path, monkeypatch, capsys):
    path = tmp_path / "Walking Dead.zip"
    key = tmp_path / "owner.key"
    key.write_bytes(bytes(range(32)))
    image = bytearray(8192)
    image[510:512] = b"\x55\xaa"
    image[450] = 0x83
    struct.pack_into("<II", image, 454, 8, 8)
    image[4096:4104] = b"LUKS\xba\xbe\x00\x02"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("walking_dead_remastered_le-0_92_0.sdcard.raw", image)
    monkeypatch.setattr(padstern, "prepared_spike3_title", lambda *_: None)
    with monkeypatch.context() as temporary:
        temporary.setattr(spike3_image, "keyfile", Mock(side_effect=AssertionError("OTP lookup on import")))
        temporary.setattr(s3zip, "open_image", Mock(side_effect=AssertionError("ZIP scanned on import")))
        added, _ = player.prepare_image(path)
    assert added.title == "walking_dead_remastered_le"
    assert Path(added.image) == path
    assert "spike3_keys" not in player.setup.settings
    def title_from_stream(stream, selected):
        assert selected == key
        stream.seek(4096)
        assert stream.read(8) == b"LUKS\xba\xbe\x00\x02"
        return "walking_dead_remastered_le"
    monkeypatch.setattr(spike3_image, "title_from_stream", title_from_stream)
    monkeypatch.setattr(player.setup, "wsl", Mock(side_effect=AssertionError("ZIP extracted as raw")))
    game, _ = player.prepare_image(path, keyfile=key)
    assert Path(game.image) == path
    assert game.platform == "spike3" and game.image_version == "0.92.0"
    assert not (player.setup.data / "images").exists()
    monkeypatch.setattr(s3zip, "open_image", Mock(side_effect=AssertionError("ZIP scanned again")))
    player.verify_pairing(game)
    card = tmp_path / "store" / "cards" / ("zip-" + game.archive_id)
    card.mkdir(parents=True)
    (card / "manifest.json").write_text(json.dumps({"tree_rev": s3store.TREE_REV}))
    monkeypatch.setattr(s3store, "win_to_wsl", lambda path: path)
    assert s3store.cmd_path(SimpleNamespace(card=str(path), store=str(tmp_path / "store"))) == 0
    assert Path(capsys.readouterr().out.strip()) == card
    with ZipFile(path, "a") as archive:
        archive.writestr("notes.txt", "changed")
    with pytest.raises(ValueError, match="changed since import"):
        player.verify_pairing(game)
