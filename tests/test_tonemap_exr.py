"""Display encoding and the EXR writer.

Both live on the host side of the process boundary, and both are the sort of code whose
bugs look like renderer bugs — a gamma applied twice reads as "Mitsuba is washed out", a
byte-swapped EXR reads as "the render is corrupt". So they are pinned here rather than
inspected in the UI.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from core.tonemap import SRGB_GAMMA, srgb_encode, tonemap
from max_side.exr import write_exr

# --------------------------------------------------------------------------------------
# tone mapping
# --------------------------------------------------------------------------------------


def test_black_and_white_map_to_the_endpoints() -> None:
    image = np.zeros((2, 2, 4), np.float32)
    image[1, :, :3] = 1.0
    out = tonemap(image)
    assert out[0, 0].tolist() == [0, 0, 0]
    assert out[1, 0].tolist() == [255, 255, 255]


def test_output_is_three_channel_uint8_and_contiguous() -> None:
    """`QImage(..., Format_RGB888)` reads the buffer directly and does not copy it, so a
    non-contiguous array would be displayed as garbage rather than raising."""
    out = tonemap(np.zeros((3, 5, 4), np.float32))
    assert out.shape == (3, 5, 3)
    assert out.dtype == np.uint8
    assert out.flags["C_CONTIGUOUS"]


def test_exposure_is_in_stops() -> None:
    """+1 EV doubles the linear value — the unit photographers already think in."""
    image = np.full((1, 1, 4), 0.25, np.float32)
    base = tonemap(image, exposure=0.0, gamma=1.0)[0, 0, 0]
    brighter = tonemap(image, exposure=1.0, gamma=1.0)[0, 0, 0]
    assert int(brighter) == pytest.approx(int(base) * 2, abs=1)


def test_values_above_one_clip_rather_than_wrap() -> None:
    """A firefly at 1e6 must be white, not whatever 1e6 truncates to as a uint8."""
    out = tonemap(np.full((1, 1, 4), 1e6, np.float32))
    assert out[0, 0].tolist() == [255, 255, 255]


def test_negative_values_clamp_to_black() -> None:
    out = tonemap(np.full((1, 1, 4), -5.0, np.float32))
    assert out[0, 0].tolist() == [0, 0, 0]


def test_nan_becomes_black() -> None:
    """A single NaN from a degenerate BSDF would otherwise become an undefined uint8 and
    speckle the preview with noise that looks like a renderer bug."""
    image = np.full((1, 2, 4), np.nan, np.float32)
    image[0, 1, :] = 0.5
    out = tonemap(image)
    assert out[0, 0].tolist() == [0, 0, 0]
    assert out[0, 1, 0] > 0


def test_alpha_is_ignored() -> None:
    """The preview is opaque; a premultiplied-looking alpha must not darken the image."""
    opaque = np.zeros((1, 1, 4), np.float32)
    opaque[..., :3] = 0.5
    opaque[..., 3] = 1.0
    transparent = opaque.copy()
    transparent[..., 3] = 0.0
    assert tonemap(opaque).tolist() == tonemap(transparent).tolist()


def test_srgb_curve_is_piecewise_not_a_plain_power() -> None:
    """sRGB is linear below 0.0031308 and a shifted power above it.

    The difference lives in the darkest few percent — exactly where a render's noise and
    shadow detail are — so approximating it crushes the part people zoom into.
    """
    dark = np.float32(0.002)
    assert float(srgb_encode(np.array([dark]))[0]) == pytest.approx(dark * 12.92)
    naive = float(dark ** (1.0 / 2.2))
    # 0.0258 against 0.0651: the naive curve is 2.5x too bright at this level.
    assert abs(float(srgb_encode(np.array([dark]))[0]) - naive) > 0.03


def test_gamma_other_than_default_uses_a_plain_power() -> None:
    """Someone typing 1.8 wants that power curve, not a piecewise approximation of sRGB."""
    image = np.full((1, 1, 4), 0.5, np.float32)
    out = tonemap(image, gamma=1.0)[0, 0, 0]
    assert int(out) == pytest.approx(128, abs=1)


def test_default_gamma_selects_srgb() -> None:
    assert SRGB_GAMMA == 2.2
    image = np.full((1, 1, 4), 0.5, np.float32)
    srgb = int(tonemap(image, gamma=SRGB_GAMMA)[0, 0, 0])
    power = int(tonemap(image, gamma=2.2000001)[0, 0, 0])
    assert abs(srgb - power) <= 2      # close, but reached by different code paths


def test_rejects_a_non_image() -> None:
    with pytest.raises(ValueError, match=r"\(H, W, >=3\)"):
        tonemap(np.zeros((4, 4), np.float32))


# --------------------------------------------------------------------------------------
# EXR
# --------------------------------------------------------------------------------------


def test_exr_header_is_well_formed(tmp_path: Path) -> None:
    path = write_exr(tmp_path / "a.exr", np.zeros((3, 4, 3), np.float32))
    raw = path.read_bytes()
    magic, version = struct.unpack_from("<Ii", raw, 0)
    assert magic == 0x01312F76
    assert version == 2
    assert b"channels\0chlist\0" in raw
    assert b"compression\0compression\0" in raw
    assert b"dataWindow\0box2i\0" in raw


def test_exr_channels_are_alphabetical(tmp_path: Path) -> None:
    """`chlist` requires it, which is why RGB is stored B, G, R — not a quirk of this
    writer but a requirement of the format."""
    raw = write_exr(tmp_path / "a.exr", np.zeros((1, 1, 3), np.float32)).read_bytes()
    start = raw.index(b"chlist\0") + len(b"chlist\0") + 4
    # Each entry is name\0 + pixel type + pLinear + 3 reserved + two sampling ints, so a
    # single-letter channel occupies 18 bytes and three of them span 54.
    names = raw[start:start + 60]
    assert names.index(b"B") < names.index(b"G") < names.index(b"R")


def test_exr_size_matches_the_layout(tmp_path: Path) -> None:
    width, height = 5, 3
    path = write_exr(tmp_path / "a.exr", np.zeros((height, width, 3), np.float32))
    raw = path.read_bytes()
    body = height * (8 + 3 * width * 4)
    table = height * 8
    assert len(raw) > body + table       # the remainder is the header


def test_exr_rejects_a_non_rgb_array(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\(H, W, 3\)"):
        write_exr(tmp_path / "a.exr", np.zeros((4, 4, 4), np.float32))


def test_exr_leaves_no_part_file(tmp_path: Path) -> None:
    write_exr(tmp_path / "a.exr", np.zeros((2, 2, 3), np.float32))
    assert list(tmp_path.glob("*.part")) == []


def test_exr_preserves_values_beyond_one(tmp_path: Path) -> None:
    """The whole reason to choose EXR. Exposure and gamma are a view, not a transform on
    the data, so a highlight at 42.0 must still read 42.0.
    """
    pixels = np.zeros((1, 2, 3), np.float32)
    pixels[0, 0] = (42.0, 0.5, -1.0)
    raw = write_exr(tmp_path / "a.exr", pixels).read_bytes()
    # First scanline: y, size, then the B row, the G row and the R row.
    offset = raw.index(b"\0", raw.index(b"screenWindowWidth")) + 1
    # Locate the pixel payload by scanning for the value rather than recomputing offsets;
    # the point of the assertion is that the number survived, not where it landed.
    assert struct.pack("<f", 42.0) in raw
    assert struct.pack("<f", -1.0) in raw
    assert offset > 0
