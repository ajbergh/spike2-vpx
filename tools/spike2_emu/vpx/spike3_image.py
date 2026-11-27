"""Verify a SPIKE 3 image using the owner's existing OTP key."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pinball_decryptor.plugins.stern import spike3
from pinball_decryptor.plugins.stern.ext4 import S_IFMT, S_IFREG
from pinball_decryptor.plugins.stern.spike3_keys import Spike3KeyError, Spike3KeySource


class KeyRequired(ValueError):
    """The player can offer the owner a key-file picker and retry."""


def keyfile(image, selected=None):
    import padlaunch

    path = Path(image)
    candidates = (selected, str(path) + ".padkeys.json", path.with_name("padkeys.json"),
                  padlaunch.kit_load().get("spike3_keyfile"),
                  Path.home() / ".pad" / "spike3_padkeys.json")
    return next((Path(candidate) for candidate in candidates
                 if isinstance(candidate, (str, os.PathLike)) and Path(candidate).is_file()), None)


def read_key(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(64 * 1024 + 1)
        if len(data) == 32:
            return data
        if re.fullmatch(rb"[0-9a-fA-F]{64}", data.strip()):
            return bytes.fromhex(data.decode("ascii").strip())
        value = json.loads(data.decode("utf-8-sig"))["otp_keyfile"]
        if not isinstance(value, str):
            raise ValueError
        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return bytes.fromhex(value)
        with (Path(path).parent / value).open("rb") as stream:
            key = stream.read(33)
        if len(key) == 32:
            return key
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        pass
    raise KeyRequired("Choose your SPIKE 3 OTP key file or a padkeys.json that points to it.")


def title_for(image, key_path):
    with open(image, "rb") as stream:
        return title_from_stream(stream, key_path)


def title_from_stream(stream, key_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "spike1_emu"))
    from build_rootfs import _lookup, _readlink

    if key_path is not None:
        parts = spike3.parse_partitions(stream)
        games = spike3.partition_for_role(parts, "games")
        if not games or not games["luks"]:
            raise ValueError("The SPIKE 3 image has no games partition.")
        try:
            fs = spike3.open_partition_ext4(stream, games, Spike3KeySource(otp_keyfile=read_key(key_path)))
        except (Spike3KeyError, spike3.Spike3KeyError, ValueError) as exc:
            if isinstance(exc, KeyRequired):
                raise
            raise KeyRequired("That SPIKE 3 key did not unlock the image. Choose the key for this card.") from None
        except (KeyError, NotImplementedError) as exc:
            raise ValueError("This SPIKE 3 image uses an unsupported encrypted partition format.") from exc
        target = _readlink(fs, "/game") or ""
        if target == "pad/padmode/padmode.sh":
            inode = _lookup(fs, "/pad/padmode/game.target")
            if inode is not None:
                node = fs.read_inode(inode)
                if node["size"] <= 256 and node["mode"] & S_IFMT == S_IFREG:
                    target = fs.read_file_bytes(node).decode("utf-8", "replace").strip()
        if target.startswith("/games/"):
            target = target[7:]
        pieces = PurePosixPath(target).parts
        if len(pieces) == 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in (".", "..")
                                    for p in pieces):
            inode = _lookup(fs, "/" + "/".join(pieces))
            if inode is not None and fs.read_inode(inode)["mode"] & S_IFMT == S_IFREG:
                return pieces[0].lower()
        raise ValueError("Choose a SPIKE 3 image with a single primary game executable.")
    raise KeyRequired("Choose your SPIKE 3 OTP key file to verify this image.")
