"""Read a thumbnail from the backglass beside a locally prepared table."""

import base64
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
import subprocess
from xml.parsers import expat


MAX_XML = 128 * 1024 * 1024
MAX_ENCODED_IMAGE = 16 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n"
THUMBNAIL_SCRIPT = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$source = [IO.MemoryStream]::new([Convert]::FromBase64String([Console]::In.ReadToEnd()))
try {
    $image = [Drawing.Image]::FromStream($source)
    try {
        if ($image.Width -gt 8192 -or $image.Height -gt 8192 -or
            [long]$image.Width * $image.Height -gt 32000000) { exit 1 }
        $scale = [Math]::Min(1.0, [Math]::Min(440.0 / $image.Width, 300.0 / $image.Height))
        $width = [Math]::Max(1, [int][Math]::Floor($image.Width * $scale))
        $height = [Math]::Max(1, [int][Math]::Floor($image.Height * $scale))
        $thumb = [Drawing.Bitmap]::new($width, $height)
        try {
            $graphics = [Drawing.Graphics]::FromImage($thumb)
            try {
                $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $graphics.DrawImage($image, 0, 0, $width, $height)
            } finally { $graphics.Dispose() }
            $output = [IO.MemoryStream]::new()
            try {
                $thumb.Save($output, [Drawing.Imaging.ImageFormat]::Png)
                [Console]::Out.Write([Convert]::ToBase64String($output.ToArray()))
            } finally { $output.Dispose() }
        } finally { $thumb.Dispose() }
    } finally { $image.Dispose() }
} finally { $source.Dispose() }
"""


def preview_key(table):
    if table:
        file = Path(table).with_suffix(".directb2s")
        try:
            info = file.stat()
            if S_ISREG(info.st_mode):
                return file, info.st_mtime_ns, info.st_size
        except (OSError, ValueError):
            pass
    return None


def read_image(path):
    """Read only DirectB2SData/Images/BackglassImage, leaving bulbs and DMD art alone."""
    class Found(Exception):
        pass

    elements = []
    value = ""

    def start(tag, attributes):
        nonlocal value
        elements.append(tag)
        if len(elements) > 128:
            raise ValueError("Backglass XML is nested too deeply.")
        if elements == ["DirectB2SData", "Images", "BackglassImage"]:
            value = attributes.get("Value", "")
            raise Found

    def reject_dtd(*_):
        raise ValueError("Backglass previews do not use a DTD.")

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda _: elements.pop()
    parser.StartDoctypeDeclHandler = reject_dtd
    try:
        with path.open("rb") as stream:
            left = MAX_XML
            while left:
                chunk = stream.read(min(left, 256 * 1024))
                parser.Parse(chunk, not chunk)
                if not chunk:
                    break
                left -= len(chunk)
    except Found:
        if len(value) <= MAX_ENCODED_IMAGE:
            return base64.b64decode("".join(value.split()), validate=True)
    return b""


@lru_cache(maxsize=12)
def thumbnail(key):
    path, _, _ = key
    try:
        image = read_image(path)
        if image.startswith(PNG) and len(image) >= 24:
            width = int.from_bytes(image[16:20])
            height = int.from_bytes(image[20:24])
            if 0 < width <= 440 and 0 < height <= 300 and len(image) <= 600_000:
                return image
        elif not image.startswith((b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
            return b""
        command = base64.b64encode(THUMBNAIL_SCRIPT.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                                input=base64.b64encode(image), capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0 and len(result.stdout) <= 800_000:
            return base64.b64decode(result.stdout, validate=True)
    except (OSError, ValueError, expat.ExpatError, subprocess.SubprocessError):
        pass
    return b""
