"""Exercise the playback callback without a physical audio device."""

from pathlib import Path
import runpy
import sys
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import Mock


def test_playback_waits_for_complete_frames_and_exits_with_truncated_tail(monkeypatch, tmp_path):
    first_queued, release_second, all_queued = Event(), Event(), Event()
    chunks = iter([b"\x08\x00\x0c", b"\x00\x7f", b""])

    def read(_):
        chunk = next(chunks)
        if chunk.startswith(b"\0"):
            first_queued.set()
            assert release_second.wait(2)
        elif not chunk:
            all_queued.set()
        return chunk

    class Output:
        def __init__(self, *, callback, **_):
            self.callback = callback

        def __enter__(self):
            assert first_queued.wait(2)
            output = bytearray(4)
            self.callback(output, 1, None, None)
            assert output == bytes(4)
            release_second.set()
            assert all_queued.wait(2)
            self.callback(output, 1, None, None)
            assert output == b"\x04\x00\x06\x00"

        def __exit__(self, *_):
            pass

    ctl = tmp_path / "audio.json"
    ctl.write_text('{"gain": 0.5}', encoding="utf-8")
    monkeypatch.setenv("PAD_AUDIO_CTL", str(ctl))
    monkeypatch.setenv("PAD_AUDIO_PREBUFFER_MS", "0")
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(query_hostapis=lambda: [], RawOutputStream=Output))
    monkeypatch.setitem(sys.modules, "numpy", None)
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/spike2_emu/padplay.py"))["main"]
    close = Mock()
    monkeypatch.setitem(main.__globals__, "open_source", lambda _: (read, close, "test", ["44100", "2"]))

    deadline = time.monotonic() + 5

    def sleep(_):
        assert time.monotonic() < deadline, "Player waited for a truncated frame after EOF"
        time.sleep(0.001)

    monkeypatch.setitem(main.__globals__, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
    main()
    close.assert_called_once_with()
