"""Seekable access to a Stern card ZIP without writing an expanded image."""

from contextlib import contextmanager
import hashlib
import io
from pathlib import PurePosixPath
import os
import struct
from tempfile import TemporaryFile
from zipfile import ZipFile
import zlib


CHUNK = 2 * 1024 * 1024
ZERO = bytes(CHUNK)
IMAGE_SUFFIXES = {".raw", ".iso", ".img"}
EXTENDED = {0x05, 0x0F, 0x85}
FAT = {0x06, 0x0B, 0x0C, 0x0E}
PROBE_LIMIT = 262144  # 128 MiB covers the boot and initial Linux headers on stock Stern cards.


def member_for(archive):
    images = [member for member in archive.infolist()
              if not member.is_dir() and not member.filename.startswith("__MACOSX/")
              and PurePosixPath(member.filename).suffix.lower() in IMAGE_SUFFIXES]
    if len(images) != 1 or not images[0].file_size:
        raise ValueError("Import a ZIP containing one non-empty card image.")
    return images[0]


def identity(archive, member):
    stat = os.fstat(archive.fp.fileno())
    fields = (member.filename, member.CRC, member.file_size, member.compress_size,
              stat.st_size, stat.st_mtime_ns)
    return hashlib.sha256(repr(fields).encode("utf-8")).hexdigest()


def is_spike3(archive, member):
    """Recognize the stock SPIKE 3 boot label or an early Linux LUKS volume."""
    with archive.open(member) as image:
        def sector(lba):
            if not 0 < lba <= PROBE_LIMIT:
                return b""
            image.seek(lba * 512)
            return image.read(512)

        def entry(table, index):
            offset = 446 + index * 16
            return table[offset + 4], *struct.unpack_from("<II", table, offset + 8)

        def marked(kind, start):
            header = sector(start)
            if kind == 0x83:
                return header.startswith(b"LUKS\xba\xbe")
            return kind in FAT and any(header[o:o + 11].rstrip(b" \0") == b"SPIKE3" for o in (43, 71))

        mbr = image.read(512)
        if mbr[510:] != b"\x55\xaa":
            return False
        partitions = sorted((entry(mbr, index) for index in range(4)), key=lambda part: part[1])
        for kind, start, size in partitions:
            if size and kind in FAT | {0x83} and marked(kind, start):
                return True
        for kind, base, size in partitions:
            if not size or kind not in EXTENDED:
                continue
            lba = base
            for _ in range(32):
                ebr = sector(lba)
                if ebr[510:] != b"\x55\xaa":
                    break
                kind, relative, count = entry(ebr, 0)
                if count and kind == 0x83 and marked(kind, lba + relative):
                    return True
                link, relative, count = entry(ebr, 1)
                if not relative or not count or link not in EXTENDED:
                    break
                lba = base + relative
    return False


class ChunkImage:
    """An anonymous scratch file of independent deflate chunks, with zero chunks omitted."""

    def __init__(self, source, storage, size):
        self.storage = storage
        self.size = size
        self.offset = 0
        self.entries = []
        self.cached_index = None
        self.cached_chunk = b""
        remaining = size
        while remaining:
            block = source.read(min(CHUNK, remaining))
            if not block:
                raise ValueError("The card image in the ZIP is truncated.")
            if block == ZERO[:len(block)]:
                self.entries.append(None)
            else:
                encoded = zlib.compress(block, level=1)
                self.entries.append((storage.tell(), len(encoded)))
                storage.write(encoded)
            remaining -= len(block)
        if source.read(1):
            raise ValueError("The ZIP card image is larger than its directory entry.")

    def tell(self):
        return self.offset

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.offset, io.SEEK_END: self.size}[whence]
        if base + offset < 0:
            raise ValueError("Negative card image offset.")
        self.offset = base + offset
        return self.offset

    def read(self, size=-1):
        available = max(0, self.size - self.offset)
        size = available if size < 0 else min(size, available)
        parts = []
        while size:
            index, offset = divmod(self.offset, CHUNK)
            if index != self.cached_index:
                entry = self.entries[index]
                if entry is None:
                    self.cached_chunk = ZERO
                else:
                    start, length = entry
                    self.storage.seek(start)
                    self.cached_chunk = zlib.decompress(self.storage.read(length))
                self.cached_index = index
            take = min(size, CHUNK - offset)
            parts.append(self.cached_chunk[offset:offset + take])
            self.offset += take
            size -= take
        return b"".join(parts)


@contextmanager
def open_image(archive, member, *, directory=None):
    with archive.open(member) as source, TemporaryFile(dir=directory) as storage:
        yield ChunkImage(source, storage, member.file_size)


def archive_id(path):
    with ZipFile(path) as archive:
        return identity(archive, member_for(archive))
