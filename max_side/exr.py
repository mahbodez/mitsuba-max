"""A minimal OpenEXR writer: uncompressed, scanline, float32 RGB.

Why write one at all, when Mitsuba ships an excellent EXR implementation? Because
`import mitsuba` inside Max is hard invariant 1, and the renderer lives in another process
that has already finished by the time the user clicks Save. Shipping OpenImageIO or the
`OpenEXR` package into Max's interpreter to save a file the host already holds in memory
would be a lot of native code for very little.

The file written is uncompressed, which makes it larger than a ZIP-compressed EXR and makes
this module about eighty lines instead of a compression implementation. For a save button
pressed a few times a session, that is the right trade.

Deliberately free of `pymxs` so it can be tested without Max.
"""

import struct
from pathlib import Path

import numpy as np
import numpy.typing as npt

__all__ = ["write_exr"]

_MAGIC = 0x01312F76
_VERSION = 2                      # version 2, no flags: single-part scanline
_NO_COMPRESSION = 0
_PIXELTYPE_FLOAT = 2
_INCREASING_Y = 0


def _attribute(name: str, type_name: str, payload: bytes) -> bytes:
    return (name.encode("ascii") + b"\0" + type_name.encode("ascii") + b"\0"
            + struct.pack("<i", len(payload)) + payload)


def _channel_list(names: tuple[str, ...]) -> bytes:
    """`chlist`, whose channels must be in alphabetical order.

    Which is why RGB is stored as B, G, R — not a quirk of this writer but a requirement of
    the format, and the reason readers hand back channels in that order.
    """
    out = b""
    for name in names:
        out += (name.encode("ascii") + b"\0"
                + struct.pack("<i", _PIXELTYPE_FLOAT)   # pixel type
                + struct.pack("<B", 0)                  # pLinear
                + b"\0\0\0"                             # reserved
                + struct.pack("<ii", 1, 1))             # x/y sampling
    return out + b"\0"


def write_exr(path: Path, pixels: npt.NDArray[np.float32]) -> Path:
    """Write `(H, W, 3)` linear float32 RGB to `path`.

    The data is written exactly as given: **no exposure, no gamma, no clamping**. The
    display controls are a view onto the buffer, and baking them into an EXR would destroy
    the only reason anyone chose EXR over PNG.
    """
    array = np.ascontiguousarray(pixels, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) array, got {array.shape}")
    height, width, _ = array.shape

    header = struct.pack("<Ii", _MAGIC, _VERSION)
    header += _attribute("channels", "chlist", _channel_list(("B", "G", "R")))
    header += _attribute("compression", "compression", struct.pack("<B", _NO_COMPRESSION))
    header += _attribute("dataWindow", "box2i",
                         struct.pack("<iiii", 0, 0, width - 1, height - 1))
    header += _attribute("displayWindow", "box2i",
                         struct.pack("<iiii", 0, 0, width - 1, height - 1))
    header += _attribute("lineOrder", "lineOrder", struct.pack("<B", _INCREASING_Y))
    header += _attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    header += _attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0))
    header += _attribute("screenWindowWidth", "float", struct.pack("<f", 1.0))
    header += b"\0"

    row_bytes = width * 4
    chunk_size = 8 + 3 * row_bytes          # int32 y + int32 size + three channel rows
    table_size = height * 8
    first_chunk = len(header) + table_size
    offsets = struct.pack(f"<{height}Q",
                          *(first_chunk + i * chunk_size for i in range(height)))

    body = bytearray()
    for y in range(height):
        body += struct.pack("<ii", y, 3 * row_bytes)
        # Alphabetical channel order: B, G, R.
        body += array[y, :, 2].tobytes()
        body += array[y, :, 1].tobytes()
        body += array[y, :, 0].tobytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(header + offsets + bytes(body))
    tmp.replace(path)
    return path
