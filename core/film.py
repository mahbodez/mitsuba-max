"""The shared film buffer: a memory-mapped float32 RGBA image with a seqlock.

Pixels never travel over the JSON pipe. The worker writes the accumulated image into a
file both processes `mmap`, and the host reads it whenever the UI wants to repaint. At
1920x1080 that is 33 MB per pass; sending it as JSON would be absurd.

Layout — 64-byte header then row-major float32 RGBA from offset 64:

    off  type    field
      0  8s      magic b"MMXFILM\\0"
      8  uint32  version
     12  uint32  seq
     16  uint32  width
     20  uint32  height
     24  uint32  channels (always 4)
     28  uint32  passes_done
     32  uint32  spp_done
     36  uint32  state
     40  --      reserved, zeroed

Synchronisation is a **seqlock**, not a mutex: the writer bumps `seq` to an odd value,
writes, then bumps it to an even value; the reader samples `seq`, reads, and samples again,
retrying if it saw an odd value or a changed one. This gives torn-read-free progressive
display with no cross-process locking primitives, no handle inheritance, and no way for a
crashed worker to leave a lock held — which matters, because surviving a Dr.Jit segfault is
the entire reason the worker is a separate process.

A torn read is not a correctness disaster here (it would show one frame of mixed passes),
but a held mutex after a crash would be, so the trade is firmly in the seqlock's favour.
"""

import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, TypeAlias

import numpy as np
import numpy.typing as npt

__all__ = [
    "CHANNELS",
    "HEADER_SIZE",
    "MAGIC",
    "VERSION",
    "FilmHeader",
    "FilmReader",
    "FilmWriter",
    "State",
    "film_size_bytes",
]

F32: TypeAlias = npt.NDArray[np.float32]

MAGIC = b"MMXFILM\0"
VERSION = 1
HEADER_SIZE = 64
CHANNELS = 4

_OFF_VERSION = 8
_OFF_SEQ = 12
_OFF_WIDTH = 16
_OFF_HEIGHT = 20
_OFF_CHANNELS = 24
_OFF_PASSES = 28
_OFF_SPP = 32
_OFF_STATE = 36

State: TypeAlias = Literal[0, 1, 2, 3]
STATE_IDLE: State = 0
STATE_RENDERING: State = 1
STATE_DONE: State = 2
STATE_ERROR: State = 3


def film_size_bytes(width: int, height: int) -> int:
    return HEADER_SIZE + width * height * CHANNELS * 4


@dataclass(frozen=True, slots=True)
class FilmHeader:
    version: int
    seq: int
    width: int
    height: int
    channels: int
    passes_done: int
    spp_done: int
    state: int


class _FilmBase:
    """Shared mmap plumbing. Not used directly — see `FilmWriter` / `FilmReader`."""

    __slots__ = ("_fh", "_map", "_u32", "path")

    def __init__(self, path: Path, mm: mmap.mmap, fh: int) -> None:
        self.path = path
        self._map = mm
        self._fh = fh
        # A uint32 view over the header lets single fields be read and written as one
        # machine word, which is what makes the seqlock's `seq` update atomic in practice.
        self._u32: npt.NDArray[np.uint32] = np.frombuffer(
            mm, dtype=np.uint32, count=HEADER_SIZE // 4
        )

    # -- header ------------------------------------------------------------------------

    def _get(self, offset: int) -> int:
        return int(self._u32[offset // 4])

    def header(self) -> FilmHeader:
        return FilmHeader(
            version=self._get(_OFF_VERSION),
            seq=self._get(_OFF_SEQ),
            width=self._get(_OFF_WIDTH),
            height=self._get(_OFF_HEIGHT),
            channels=self._get(_OFF_CHANNELS),
            passes_done=self._get(_OFF_PASSES),
            spp_done=self._get(_OFF_SPP),
            state=self._get(_OFF_STATE),
        )

    @property
    def width(self) -> int:
        return self._get(_OFF_WIDTH)

    @property
    def height(self) -> int:
        return self._get(_OFF_HEIGHT)

    def _pixels_view(self) -> F32:
        h, w = self.height, self.width
        arr: F32 = np.frombuffer(self._map, dtype=np.float32, count=w * h * CHANNELS,
                                 offset=HEADER_SIZE)
        return arr.reshape(h, w, CHANNELS)

    # -- lifecycle ---------------------------------------------------------------------

    def close(self) -> None:
        # numpy views keep the mmap exported; drop them before unmapping or the close
        # raises BufferError.
        self._u32 = np.empty(0, dtype=np.uint32)
        try:
            self._map.close()
        finally:
            os.close(self._fh)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()


class FilmWriter(_FilmBase):
    """Worker side. Creates the file and publishes accumulated passes into it."""

    __slots__ = ()

    @classmethod
    def create(cls, path: str | os.PathLike[str], width: int, height: int) -> "FilmWriter":
        if width < 1 or height < 1:
            raise ValueError(f"film must be at least 1x1, got {width}x{height}")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        size = film_size_bytes(width, height)
        fh = os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        try:
            os.ftruncate(fh, size)
            mm = mmap.mmap(fh, size, access=mmap.ACCESS_WRITE)
        except BaseException:
            os.close(fh)
            raise
        mm[0:HEADER_SIZE] = b"\0" * HEADER_SIZE
        mm[0:8] = MAGIC
        self = cls(p, mm, fh)
        self._set(_OFF_VERSION, VERSION)
        self._set(_OFF_WIDTH, width)
        self._set(_OFF_HEIGHT, height)
        self._set(_OFF_CHANNELS, CHANNELS)
        self._set(_OFF_STATE, STATE_IDLE)
        return self

    def _set(self, offset: int, value: int) -> None:
        self._u32[offset // 4] = np.uint32(value)

    def set_state(self, state: State) -> None:
        self._set(_OFF_STATE, state)

    def write(self, pixels: F32, *, passes_done: int, spp_done: int,
              state: State = STATE_RENDERING) -> None:
        """Publish one accumulated frame under the seqlock.

        `pixels` is (H, W, 4) float32 in **linear** space — no tone mapping, no clamping.
        Exposure and gamma are host-side controls applied to this buffer at display time,
        which is what lets the user re-expose a finished render without re-rendering it.
        """
        h, w = self.height, self.width
        if pixels.shape != (h, w, CHANNELS):
            raise ValueError(
                f"pixel array is {pixels.shape}, film is {(h, w, CHANNELS)}"
            )
        seq = self._get(_OFF_SEQ)
        self._set(_OFF_SEQ, seq + 1)          # odd: a write is in progress
        view = self._pixels_view()
        np.copyto(view, pixels.astype(np.float32, copy=False))
        self._set(_OFF_PASSES, passes_done)
        self._set(_OFF_SPP, spp_done)
        self._set(_OFF_STATE, state)
        self._set(_OFF_SEQ, seq + 2)          # even: consistent again


class FilmReader(_FilmBase):
    """Host side. Opens an existing film read-only and snapshots it for display."""

    __slots__ = ()

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> "FilmReader":
        p = Path(path)
        size = p.stat().st_size
        if size < HEADER_SIZE:
            raise ValueError(f"{p} is too small to be a film buffer ({size} bytes)")
        fh = os.open(p, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            mm = mmap.mmap(fh, size, access=mmap.ACCESS_READ)
        except BaseException:
            os.close(fh)
            raise
        magic = mm[0:8]
        if magic != MAGIC:
            mm.close()
            os.close(fh)
            raise ValueError(f"{p} is not a film buffer (magic {magic!r})")
        (version,) = struct.unpack_from("<I", mm, _OFF_VERSION)
        if version != VERSION:
            mm.close()
            os.close(fh)
            raise ValueError(f"film version {version}, this build speaks {VERSION}")
        return cls(p, mm, fh)

    def read(self, *, max_retries: int = 16) -> tuple[F32, FilmHeader] | None:
        """Snapshot the film, or `None` if the writer kept it busy for `max_retries` tries.

        Returning `None` rather than blocking is deliberate: this runs on Max's main thread
        from a `QTimer`, and the correct response to "the worker is mid-write" is to skip
        this repaint and try again in 100 ms, not to stall the host's event loop.
        """
        for _ in range(max_retries):
            before = self._get(_OFF_SEQ)
            if before % 2 == 1:
                continue
            snapshot = np.array(self._pixels_view(), dtype=np.float32, copy=True)
            hdr = self.header()
            after = self._get(_OFF_SEQ)
            if after == before:
                return snapshot, hdr
        return None
