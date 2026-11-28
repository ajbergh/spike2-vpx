"""Check TCP liveness while the guest audio FIFO is idle."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


RIG = Path(__file__).resolve().parents[1] / "tools" / "spike2_emu"


@unittest.skipUnless(sys.platform == "linux", "Requires POSIX FIFO polling")
class AudioRelayTest(unittest.TestCase):
    def setUp(self):
        with patch.object(sys, "path", [str(RIG), *sys.path]):
            import padrelay
        self.relay = padrelay

    def test_quiet_fifo_sends_heartbeats_then_audio_and_detects_peer_close(self):
        with ExitStack() as cleanup:
            folder = cleanup.enter_context(TemporaryDirectory())
            executor = cleanup.enter_context(ThreadPoolExecutor(1))
            fifo = Path(folder) / "audio.fifo"
            os.mkfifo(fifo)
            fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            cleanup.callback(os.close, fd)
            server, player = socket.socketpair()
            cleanup.enter_context(server)
            cleanup.enter_context(player)
            player.settimeout(2)
            cleanup.enter_context(patch.object(self.relay, "TCP_HEARTBEAT_S", 0.02))
            read = executor.submit(self.relay.read_tcp_audio, fd, server, 65536, 4, 0)

            with player.makefile("rb") as received:
                self.assertEqual(received.read(8), bytes(8))
            with fifo.open("wb", buffering=0) as guest:
                self.assertEqual(guest.write(b"\x01\x02\x03\x04"), 4)
                self.assertEqual(read.result(timeout=2), b"\x01\x02\x03\x04")
                player.close()
                self.assertEqual(self.relay.read_tcp_audio(fd, server, 65536, 4, 4), b"")

    def test_heartbeat_does_not_split_an_audio_frame(self):
        with patch.object(self.relay.select, "select", side_effect=[([], [], []), ([23], [], [])]), \
                patch.object(self.relay.os, "read", return_value=b"\x02\x03\x04"), \
                socket.socket() as connection:
            self.assertEqual(self.relay.read_tcp_audio(23, connection, 65536, 4, 1), b"\x02\x03\x04")
