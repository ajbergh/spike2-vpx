"""Download and verify the pinned runtime image."""

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from . import net

GITHUB_REPO = "davidvanderburgh/pinball-asset-decryptor"
_CACHE_NAMESPACE = "Spike2Vpx"
_CHUNK = 1024 * 1024

DOWNLOAD_TIMEOUT = 60


@dataclass(frozen=True)
class Payload:
    key: str
    filename: str
    release_tag: str
    sha256: str
    size: int
    version: str
    what: str


def is_published(payload: Payload) -> bool:
    """False while a payload has no pinned hash yet - the app then builds from
    source as it always did.  This is what lets the mechanism ship before the
    first payload release is cut, rather than in the same breath."""
    return bool(payload.sha256) and payload.size > 0


def asset_url(payload: Payload, repo: str = GITHUB_REPO) -> str:
    """The direct release-asset URL.  No API call - see the module docstring."""
    return "https://github.com/%s/releases/download/%s/%s" % (
        repo, payload.release_tag, payload.filename)


def cache_root() -> str:
    """Where downloads are kept on the host."""
    override = os.environ.get("PAD_PAYLOAD_DIR")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME",
                              os.path.expanduser("~/.cache"))
    return os.path.join(base, _CACHE_NAMESPACE, "payloads")


def cache_path(payload: Payload) -> str:
    """The cached file for this EXACT payload.

    Named with the hash prefix, so a payload that is re-cut lands beside the
    old one instead of on top of it: a stale file can then never masquerade as
    the new one, and rolling back is deleting a file rather than re-downloading
    the previous build."""
    stamp = payload.sha256[:12] if payload.sha256 else "unpinned"
    return os.path.join(cache_root(), "%s-%s" % (stamp, payload.filename))


def file_sha256(path: str, progress: Optional[Callable[[int], None]] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            h.update(block)
            if progress:
                progress(len(block))
    return h.hexdigest()


def verify(path: str, payload: Payload) -> Tuple[bool, str]:
    """``(ok, why not)`` for a file on disk.

    Size first: a truncated download, an antivirus that replaced the file with
    a stub, and a proxy's HTML error page are all wrong SIZES, and saying so
    costs no hashing at all.  Then the hash, which is the actual promise."""
    if not os.path.exists(path):
        return False, "not downloaded yet"
    actual_size = os.path.getsize(path)
    if actual_size != payload.size:
        return False, ("wrong size: %d bytes, expected %d (a blocked download "
                       "often lands as an error page)"
                       % (actual_size, payload.size))
    got = file_sha256(path)
    if got != payload.sha256:
        return False, ("checksum does not match what we built (%s..., expected "
                       "%s...)" % (got[:12], payload.sha256[:12]))
    return True, ""


def download(payload: Payload, dest: Optional[str] = None,
             log: Optional[Callable[[str], None]] = None,
             progress: Optional[Callable[[int, int], None]] = None,
             opener: Optional[Callable] = None,
             repo: str = GITHUB_REPO) -> str:
    """Fetch one payload into the host cache and return its path.

    Writes ``<dest>.part`` and renames only after :func:`verify` passes, so a
    failed or tampered download leaves NOTHING that a later run could mistake
    for the real thing.  Raises ``RuntimeError`` with a sentence a user can act
    on - which is the whole point of this function having its own error text
    rather than letting a urllib exception reach the log."""
    dest = dest or cache_path(payload)
    url = asset_url(payload, repo)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if log:
        log("Downloading %s (%s)…" % (payload.filename, payload.version))
    part = dest + ".part"
    do_open = opener or net.urlopen
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pad-payloads"})
        with do_open(req, timeout=DOWNLOAD_TIMEOUT) as resp, \
                open(part, "wb") as out:
            done = 0
            while True:
                block = resp.read(_CHUNK)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress:
                    progress(done, payload.size)
    except Exception as exc:                                   # noqa: BLE001
        _unlink(part)
        raise RuntimeError(
            "could not download %s: %s\n"
            "If this machine is behind a firewall, %s and "
            "objects.githubusercontent.com need to be reachable on 443 — or "
            "download the file on another machine and use \"Install from "
            "file\"." % (payload.filename, exc, "github.com")) from exc

    ok, why = verify(part, payload)
    if not ok:
        _unlink(part)
        raise RuntimeError(
            "%s downloaded but %s. Antivirus or a proxy may have altered it; "
            "try again, or download it on another machine and use \"Install "
            "from file\"." % (payload.filename, why))
    os.replace(part, dest)
    if log:
        log("%s verified (%s)." % (payload.filename, _human(payload.size)))
    return dest


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit != "bytes" else "%d bytes" % n
        n /= 1024.0
    return str(n)
