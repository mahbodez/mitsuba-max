"""The shared film buffer and its seqlock.

Both processes touch this file, so the tests exercise the header layout byte for byte and
the reader/writer handshake including its failure mode. A torn read here shows up as one
corrupted frame in a progressive preview, which is easy to dismiss as "the renderer being
noisy" — hence the explicit odd-seq test rather than trusting the protocol by inspection.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from core import film


@pytest.fixture
def film_path(tmp_path: Path) -> Path:
    return tmp_path / "job0.film"


def test_size_matches_the_documented_layout() -> None:
    assert film.film_size_bytes(4, 3) == 64 + 4 * 3 * 4 * 4


def test_header_layout_on_disk(film_path: Path) -> None:
    """The offsets are a cross-process ABI, so they are asserted against raw bytes.

    Reading them back through the same accessors that wrote them would pass even if every
    offset were wrong by four bytes.
    """
    with film.FilmWriter.create(film_path, 5, 7) as w:
        w.write(np.zeros((7, 5, 4), np.float32), passes_done=3, spp_done=48)

    raw = film_path.read_bytes()
    assert raw[0:8] == b"MMXFILM\0"
    assert struct.unpack_from("<I", raw, 8)[0] == film.VERSION
    assert struct.unpack_from("<I", raw, 12)[0] == 2        # seq, even after one write
    assert struct.unpack_from("<I", raw, 16)[0] == 5        # width
    assert struct.unpack_from("<I", raw, 20)[0] == 7        # height
    assert struct.unpack_from("<I", raw, 24)[0] == 4        # channels
    assert struct.unpack_from("<I", raw, 28)[0] == 3        # passes_done
    assert struct.unpack_from("<I", raw, 32)[0] == 48       # spp_done
    assert struct.unpack_from("<I", raw, 36)[0] == film.STATE_RENDERING
    assert raw[40:64] == b"\0" * 24                         # reserved stays zeroed


def test_write_then_read(film_path: Path) -> None:
    pixels = np.arange(4 * 3 * 4, dtype=np.float32).reshape(3, 4, 4)
    with film.FilmWriter.create(film_path, 4, 3) as w:
        w.write(pixels, passes_done=1, spp_done=16)
        with film.FilmReader.open(film_path) as r:
            got = r.read()
            assert got is not None
            data, header = got
            assert np.array_equal(data, pixels)
            assert header.passes_done == 1
            assert header.spp_done == 16
            assert header.width == 4
            assert header.height == 3


def test_reader_snapshot_is_a_copy(film_path: Path) -> None:
    """The UI keeps the buffer for live re-exposure, so it must not alias the mmap.

    If it aliased, moving the exposure slider after the next pass landed would tone-map a
    half-written frame.
    """
    with film.FilmWriter.create(film_path, 2, 2) as w:
        w.write(np.ones((2, 2, 4), np.float32), passes_done=1, spp_done=1)
        with film.FilmReader.open(film_path) as r:
            got = r.read()
            assert got is not None
            snapshot, _ = got
            w.write(np.full((2, 2, 4), 9.0, np.float32), passes_done=2, spp_done=2)
            assert np.all(snapshot == 1.0)


def test_seqlock_blocks_reads_mid_write(film_path: Path) -> None:
    """An odd `seq` means a write is in flight and the reader must decline, not tear."""
    with film.FilmWriter.create(film_path, 2, 2) as w:
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=1, spp_done=1)
        # Simulate the writer having entered its critical section.
        w._set(film._OFF_SEQ, 3)
        with film.FilmReader.open(film_path) as r:
            assert r.read(max_retries=4) is None
        w._set(film._OFF_SEQ, 4)
        with film.FilmReader.open(film_path) as r:
            assert r.read() is not None


def test_seq_increments_by_two_per_write(film_path: Path) -> None:
    with film.FilmWriter.create(film_path, 2, 2) as w:
        assert w.header().seq == 0
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=1, spp_done=1)
        assert w.header().seq == 2
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=2, spp_done=2)
        assert w.header().seq == 4


def test_shape_mismatch_is_rejected(film_path: Path) -> None:
    with (
        film.FilmWriter.create(film_path, 4, 3) as w,
        pytest.raises(ValueError, match="film is"),
    ):
        w.write(np.zeros((3, 3, 4), np.float32), passes_done=1, spp_done=1)


def test_reader_rejects_a_foreign_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-film.bin"
    bogus.write_bytes(b"x" * 128)
    with pytest.raises(ValueError, match="not a film buffer"):
        film.FilmReader.open(bogus)


def test_reader_rejects_a_version_mismatch(film_path: Path) -> None:
    with film.FilmWriter.create(film_path, 2, 2) as w:
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=1, spp_done=1)
    raw = bytearray(film_path.read_bytes())
    struct.pack_into("<I", raw, 8, film.VERSION + 1)
    film_path.write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="this build speaks"):
        film.FilmReader.open(film_path)


def test_degenerate_size_is_rejected(film_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1x1"):
        film.FilmWriter.create(film_path, 0, 10)


def test_state_transitions(film_path: Path) -> None:
    with film.FilmWriter.create(film_path, 2, 2) as w:
        assert w.header().state == film.STATE_IDLE
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=1, spp_done=1)
        assert w.header().state == film.STATE_RENDERING
        w.write(np.zeros((2, 2, 4), np.float32), passes_done=2, spp_done=2,
                state=film.STATE_DONE)
        assert w.header().state == film.STATE_DONE
        w.set_state(film.STATE_ERROR)
        assert w.header().state == film.STATE_ERROR
