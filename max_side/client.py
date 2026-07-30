"""Worker process lifecycle, seen from Max's main thread.

Every method here is non-blocking. Max's main thread must never stall — no
`proc.communicate()`, no blocking reads, no `time.sleep` — so stdout and stderr are drained
by daemon threads into queues and the UI polls `poll_events()` from a `QTimer` at ~10 Hz.

The out-of-process design exists so a Dr.Jit segfault does not take the user's unsaved scene
with it. That benefit is only real if the crash is *visible*, so a dead worker surfaces its
exit code and the tail of its stderr rather than a spinner that never stops.
"""

import contextlib
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from core import film as film_mod
from core import protocol as p
from core.emit_dict import scene_to_dict
from core.ir import Scene

__all__ = ["WorkerClient", "WorkerCrashed", "shared_worker", "shutdown_shared"]

_CREATE_NO_WINDOW = 0x08000000
_STDERR_TAIL = 400

_shared: "WorkerClient | None" = None
"""Session-scoped worker. One process, many jobs — see `shared_worker`."""


class WorkerCrashed(RuntimeError):
    """The worker exited without being asked to. Carries the exit code and stderr tail."""


def shared_worker(interpreter: str, project_root: Path, *,
                  variant: str = "auto") -> "WorkerClient":
    """Return the live worker, starting one if needed.

    `render()` used to construct a fresh `WorkerClient` on every click. Each call left the
    previous process running with its CUDA context still resident, so a few renders filled
    VRAM. The design was always one worker per Max session (see `docs/PERFORMANCE.md`);
    this is that design, not a new policy.
    """
    global _shared
    root = project_root.resolve()
    if _shared is not None:
        same = (
            _shared.interpreter == interpreter
            and _shared.project_root.resolve() == root
            and _shared.variant == variant
        )
        if same and _shared.is_running:
            return _shared
        _shared.shutdown()
        _shared = None

    client = WorkerClient(interpreter=interpreter, project_root=root, variant=variant)
    client.start()
    _shared = client
    return client


def shutdown_shared(*, timeout: float = 5.0) -> None:
    """Tear down the session worker. Safe when none exists. Used by reload, not by the UI."""
    global _shared
    if _shared is None:
        return
    _shared.shutdown(timeout=timeout)
    _shared = None


@dataclass
class WorkerClient:
    """Owns the worker subprocess and the film file it writes into.

    One worker serves many jobs. Restarting it per render would add a CUDA context
    initialisation — several seconds — to every single render, which is exactly the latency
    the progressive display is trying to hide. Prefer `shared_worker` over constructing
    this directly from the Max UI path.
    """

    interpreter: str
    project_root: Path
    variant: str = "auto"

    proc: subprocess.Popen[str] | None = None
    ready: p.Ready | None = None
    next_job: int = 1
    active_job: int | None = None

    _events: Queue[dict[str, Any]] = field(default_factory=Queue)
    _stderr: deque[str] = field(default_factory=lambda: deque(maxlen=_STDERR_TAIL))
    _preamble: list[str] = field(default_factory=list)
    _film: film_mod.FilmReader | None = None
    _film_path: Path | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        """Launch the worker and send `hello`. Returns immediately; wait for `ready`."""
        if self.is_running:
            return

        import os

        env = dict(os.environ)
        # See docs/PROBE_RESULTS.md, probe 12: with PYTHONPATH set to anything other than
        # Max's own Python directory, this interpreter prints a banner ON STDOUT before any
        # user code runs. That banner would be the first thing we read from the protocol
        # stream. `core` reaches the worker through a .pth file instead.
        for var in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            env.pop(var, None)

        self.proc = subprocess.Popen(
            [self.interpreter, "-u", "-m", "worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(self.project_root),
            creationflags=_CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self._send(p.Hello(protocol=p.PROTOCOL_VERSION, variant=self.variant))

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Ask politely, then insist. Safe to call on a worker that is already gone."""
        if self._film is not None:
            self._film.close()
            self._film = None
        if not self.is_running:
            self.proc = None
            return
        with contextlib.suppress(OSError, WorkerCrashed):
            self._send(p.Shutdown())
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.05)   # not on the UI thread: shutdown is called on teardown only
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
        self.ready = None

    # -- pumps -------------------------------------------------------------------------

    def _pump_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                import json
                obj = json.loads(text)
            except ValueError:
                # Not a protocol violation to panic over: probe 12 documents a real case
                # where this interpreter writes a banner to stdout. Keep it for the
                # diagnostics box and carry on looking for JSON.
                self._preamble.append(text)
                continue
            if isinstance(obj, dict):
                self._events.put(obj)

    def _pump_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    def _send(self, message: p.Command) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise WorkerCrashed("the worker is not running")
        p.write_message(self.proc.stdin, message)

    # -- diagnostics -------------------------------------------------------------------

    def stderr_tail(self, lines: int = 40) -> str:
        return "\n".join(list(self._stderr)[-lines:])

    def preamble(self) -> str:
        """Non-JSON text the worker wrote to stdout. Normally empty."""
        return "\n".join(self._preamble)

    def crash_report(self) -> str:
        code = self.proc.poll() if self.proc is not None else None
        parts = [f"The Mitsuba worker exited unexpectedly (exit code {code}).",
                 "",
                 f"interpreter: {self.interpreter}"]
        if self.ready is not None:
            parts.append(f"variant:     {self.ready.variant}")
        tail = self.stderr_tail()
        if tail:
            parts += ["", "--- worker stderr (tail) ---", tail]
        if self._preamble:
            parts += ["", "--- unexpected stdout ---", self.preamble()]
        parts += ["", "Your scene is unaffected: the renderer runs in its own process, "
                      "which is the point of this design."]
        return "\n".join(parts)

    # -- jobs --------------------------------------------------------------------------

    def submit(self, scene: Scene, *, film_path: Path, scene_root: Path,
               seed: int = 0) -> int:
        """Send a render job. Returns its id; progress arrives through `poll_events`.

        The film file is created here, by the host, before the worker is told about it. The
        worker maps and writes it; the host maps and reads it. Creating it host-side means a
        worker that dies during startup leaves a valid, empty film rather than a missing
        path the UI has to special-case.
        """
        if not self.is_running:
            raise WorkerCrashed(self.crash_report())

        # A second Render click must not leave the previous job finishing in the
        # background while a new one queues behind it — that doubles peak VRAM briefly
        # and feeds the wrong pass events into the new window.
        if self.active_job is not None:
            self.cancel(self.active_job)

        job = self.next_job
        self.next_job += 1
        self.active_job = job

        width = scene.camera.film_width
        height = scene.camera.film_height

        if self._film is not None:
            self._film.close()
            self._film = None
        film_mod.FilmWriter.create(film_path, width, height).close()
        self._film_path = film_path

        self._send(p.Render(
            job=job,
            scene=scene_to_dict(scene),
            shm=str(film_path),
            width=width,
            height=height,
            spp_per_pass=scene.settings.spp_per_pass,
            passes=scene.settings.passes,
            seed=seed,
            scene_root=str(scene_root),
        ))
        return job

    def cancel(self, job: int | None = None) -> None:
        """Request cancellation. Takes effect at the next pass boundary, not immediately —
        `mi.render()` cannot be interrupted mid-call."""
        target = self.active_job if job is None else job
        if target is not None and self.is_running:
            self._send(p.Cancel(job=target))

    def poll_events(self) -> list[p.Event]:
        """Every event received since the last call. Never blocks.

        Raises `WorkerCrashed` when the process has died, so the caller's timer callback
        has one place to catch it rather than checking `is_running` everywhere.
        """
        out: list[p.Event] = []
        while True:
            try:
                obj = self._events.get_nowait()
            except Empty:
                break
            try:
                event = p.decode_event(obj)
            except p.ProtocolError:
                continue
            if isinstance(event, p.Ready):
                self.ready = event
                if event.protocol != p.PROTOCOL_VERSION:
                    raise WorkerCrashed(
                        f"protocol mismatch: this build speaks {p.PROTOCOL_VERSION}, "
                        f"the worker speaks {event.protocol}"
                    )
            if isinstance(event, p.Done | p.ErrorEv):
                self.active_job = None
            out.append(event)

        if not out and self.proc is not None and self.proc.poll() is not None:
            raise WorkerCrashed(self.crash_report())
        return out

    # -- film --------------------------------------------------------------------------

    def read_film(self):
        """Latest accumulated frame as `(pixels, header)`, or `None` if unavailable.

        `None` covers both "no film yet" and "the worker is mid-write" — the seqlock
        declines rather than tearing. The correct response to either is to skip this
        repaint and try again in 100 ms, never to stall the event loop.
        """
        if self._film_path is None:
            return None
        if self._film is None:
            try:
                self._film = film_mod.FilmReader.open(self._film_path)
            except (OSError, ValueError):
                return None
        return self._film.read()


def default_interpreter() -> str:
    """Max's own interpreter — the base for the managed venv, not a worker in itself."""
    return sys.executable
