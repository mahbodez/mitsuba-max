"""The host ↔ worker wire protocol: newline-delimited JSON over stdin/stdout.

Pixels never travel over this pipe — they go through the shared film in `core.film`. What
travels here is small, ordered control traffic: a scene description once per job, and a
progress event once per pass.

Both sides import this module, so a schema change is a single edit and a version bump.
`PROTOCOL_VERSION` is exchanged in `hello`/`ready`; a mismatch refuses the render and
states both numbers rather than failing later in a confusing place.

stdout carries **only** these messages. The worker routes every human-readable line,
including Mitsuba's own logging, to stderr — a stray `print` would corrupt the stream and
produce a decode error that looks nothing like its cause.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any, TypeAlias

__all__ = [
    "PROTOCOL_VERSION",
    "Cancel",
    "Command",
    "Done",
    "ErrorEv",
    "Event",
    "Hello",
    "LogEv",
    "PassEv",
    "ProtocolError",
    "Ready",
    "Render",
    "Shutdown",
    "decode_command",
    "decode_event",
    "read_messages",
    "write_message",
]

PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    """A message that could not be understood. Never a reason to keep rendering."""


# --------------------------------------------------------------------------------------
# host -> worker
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hello:
    protocol: int = PROTOCOL_VERSION
    variant: str = "auto"
    """`auto`, or an explicit `cuda_ad_rgb` / `llvm_ad_rgb` / `scalar_rgb` override."""

    def to_dict(self) -> dict[str, Any]:
        return {"cmd": "hello", "protocol": self.protocol, "variant": self.variant}


@dataclass(frozen=True, slots=True)
class Render:
    job: int
    scene: dict[str, Any]
    """The `mi.load_dict` description produced by `core.emit_dict`."""
    shm: str
    """Path of the film file, already created and sized by the host."""
    width: int
    height: int
    spp_per_pass: int = 16
    passes: int = 32
    seed: int = 0
    scene_root: str = ""
    """Base directory for the relative asset paths inside `scene`."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": "render",
            "job": self.job,
            "scene": self.scene,
            "shm": self.shm,
            "film": {"w": self.width, "h": self.height},
            "spp_per_pass": self.spp_per_pass,
            "passes": self.passes,
            "seed": self.seed,
            "scene_root": self.scene_root,
        }


@dataclass(frozen=True, slots=True)
class Cancel:
    job: int

    def to_dict(self) -> dict[str, Any]:
        return {"cmd": "cancel", "job": self.job}


@dataclass(frozen=True, slots=True)
class Shutdown:
    def to_dict(self) -> dict[str, Any]:
        return {"cmd": "shutdown"}


Command: TypeAlias = Hello | Render | Cancel | Shutdown


def decode_command(d: dict[str, Any]) -> Command:
    match d.get("cmd"):
        case "hello":
            return Hello(protocol=int(d.get("protocol", 0)),
                         variant=str(d.get("variant", "auto")))
        case "render":
            film = d["film"]
            return Render(
                job=int(d["job"]),
                scene=d["scene"],
                shm=str(d["shm"]),
                width=int(film["w"]),
                height=int(film["h"]),
                spp_per_pass=int(d.get("spp_per_pass", 16)),
                passes=int(d.get("passes", 32)),
                seed=int(d.get("seed", 0)),
                scene_root=str(d.get("scene_root", "")),
            )
        case "cancel":
            return Cancel(job=int(d["job"]))
        case "shutdown":
            return Shutdown()
        case other:
            raise ProtocolError(f"unknown command {other!r}")


# --------------------------------------------------------------------------------------
# worker -> host
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ready:
    mitsuba: str
    variant: str
    python: str
    available_variants: tuple[str, ...] = ()
    protocol: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev": "ready",
            "protocol": self.protocol,
            "mitsuba": self.mitsuba,
            "variant": self.variant,
            "python": self.python,
            "available_variants": list(self.available_variants),
        }


@dataclass(frozen=True, slots=True)
class PassEv:
    job: int
    index: int
    spp_done: int
    elapsed_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev": "pass",
            "job": self.job,
            "index": self.index,
            "spp_done": self.spp_done,
            "elapsed_s": self.elapsed_s,
        }


@dataclass(frozen=True, slots=True)
class Done:
    job: int
    spp_done: int
    elapsed_s: float
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev": "done",
            "job": self.job,
            "spp_done": self.spp_done,
            "elapsed_s": self.elapsed_s,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True, slots=True)
class ErrorEv:
    message: str
    job: int | None = None
    traceback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ev": "error",
            "job": self.job,
            "message": self.message,
            "traceback": self.traceback,
        }


@dataclass(frozen=True, slots=True)
class LogEv:
    message: str
    level: str = "info"
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ev": "log", "level": self.level, "message": self.message, **self.fields}


Event: TypeAlias = Ready | PassEv | Done | ErrorEv | LogEv


def decode_event(d: dict[str, Any]) -> Event:
    match d.get("ev"):
        case "ready":
            return Ready(
                mitsuba=str(d["mitsuba"]),
                variant=str(d["variant"]),
                python=str(d["python"]),
                available_variants=tuple(str(v) for v in d.get("available_variants", ())),
                protocol=int(d.get("protocol", 0)),
            )
        case "pass":
            return PassEv(
                job=int(d["job"]),
                index=int(d["index"]),
                spp_done=int(d["spp_done"]),
                elapsed_s=float(d["elapsed_s"]),
            )
        case "done":
            return Done(
                job=int(d["job"]),
                spp_done=int(d["spp_done"]),
                elapsed_s=float(d["elapsed_s"]),
                cancelled=bool(d.get("cancelled", False)),
            )
        case "error":
            job = d.get("job")
            return ErrorEv(
                message=str(d["message"]),
                job=None if job is None else int(job),
                traceback=str(d.get("traceback", "")),
            )
        case "log":
            return LogEv(message=str(d["message"]), level=str(d.get("level", "info")))
        case other:
            raise ProtocolError(f"unknown event {other!r}")


# --------------------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------------------


def write_message(stream: IO[str], msg: Command | Event) -> None:
    """Serialise one message and flush. Flushing is not optional — the pipe is buffered
    and an unflushed `ready` looks exactly like a worker that failed to start."""
    stream.write(json.dumps(msg.to_dict(), separators=(",", ":")) + "\n")
    stream.flush()


def read_messages(stream: IO[str]) -> Iterator[dict[str, Any]]:
    """Yield decoded JSON objects until EOF, skipping blank lines.

    Yields raw dicts rather than typed messages so the caller decides which decoder to
    apply; the two directions have disjoint schemas.
    """
    for line in stream:
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"malformed message: {text[:200]!r}") from exc
        if not isinstance(obj, dict):
            raise ProtocolError(f"expected a JSON object, got {type(obj).__name__}")
        yield obj
