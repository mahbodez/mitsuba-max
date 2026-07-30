"""The worker protocol loop.

Reads newline-delimited JSON commands on stdin, writes events on stdout, renders in
between. Three rules govern everything here:

1. **stdout carries only protocol messages.** Any stray `print` corrupts the stream and
   surfaces as a decode error on the host that looks nothing like its cause. Mitsuba's own
   logging is redirected to stderr at startup for the same reason — it writes to stdout by
   default and `jitc_llvm_init(): LLVM API initialization failed` on a machine without a
   working LLVM backend would otherwise be the first "message" the host ever received.
2. **Every exception is caught at the loop boundary** and reported as an `error` event with
   the full traceback. The worker stays alive and ready for the next job; only `shutdown`
   ends it. A render that fails because one texture is missing must not require restarting
   the process.
3. **stdin is drained on a background thread.** Windows has no `select` on pipes, so a
   blocking read would make cancellation impossible — which is the one thing this whole
   pass-splitting design exists to provide.
"""

import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from core import protocol as p
from worker.render import render_progressive, select_variant

__all__ = ["main"]


def _log(message: str) -> None:
    """Human-readable output goes to stderr, always. See rule 1."""
    print(message, file=sys.stderr, flush=True)


def _reader_thread(stream: Any, sink: "queue.Queue[dict[str, Any] | None]") -> None:
    try:
        for obj in p.read_messages(stream):
            sink.put(obj)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        _log(f"stdin reader stopped: {exc}")
    finally:
        sink.put(None)   # EOF sentinel: the host went away


class Worker:
    """Owns the command queue and the current job.

    Pending cancellations are tracked as a set of job ids rather than a single flag,
    because a `cancel` can arrive for a job that has already finished — the host is polling
    at 10 Hz and does not know precisely when the last pass landed. Cancelling an unknown
    job is a no-op, not an error.
    """

    def __init__(self) -> None:
        self.incoming: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.cancelled: set[int] = set()
        self.current_job: int | None = None
        self.running = True

    # -- outgoing ----------------------------------------------------------------------

    def emit(self, event: p.Event) -> None:
        p.write_message(sys.stdout, event)

    # -- incoming ----------------------------------------------------------------------

    def drain(self) -> None:
        """Consume everything queued without blocking. Called between render passes."""
        while True:
            try:
                obj = self.incoming.get_nowait()
            except queue.Empty:
                return
            if obj is None:
                self.running = False
                if self.current_job is not None:
                    self.cancelled.add(self.current_job)
                return
            self._handle_out_of_band(obj)

    def _handle_out_of_band(self, obj: dict[str, Any]) -> None:
        """Only `cancel` and `shutdown` are meaningful mid-render; anything else waits.

        A `render` that arrives while another is running is not dropped — it is pushed back
        so the main loop picks it up next. Dropping it would leave the host waiting forever
        for a `done` that is never coming.
        """
        try:
            cmd = p.decode_command(obj)
        except p.ProtocolError as exc:
            self.emit(p.ErrorEv(message=str(exc)))
            return
        match cmd:
            case p.Cancel(job=job):
                self.cancelled.add(job)
            case p.Shutdown():
                self.running = False
                if self.current_job is not None:
                    self.cancelled.add(self.current_job)
            case _:
                self.incoming.put(obj)

    def should_cancel(self) -> bool:
        self.drain()
        return self.current_job in self.cancelled or not self.running

    # -- jobs --------------------------------------------------------------------------

    def do_render(self, cmd: p.Render) -> None:
        self.current_job = cmd.job
        try:
            result = render_progressive(
                cmd.scene,
                film_path=cmd.shm,
                width=cmd.width,
                height=cmd.height,
                spp_per_pass=cmd.spp_per_pass,
                passes=cmd.passes,
                seed=cmd.seed,
                scene_root=Path(cmd.scene_root) if cmd.scene_root else None,
                should_cancel=self.should_cancel,
                on_pass=lambda k, spp, elapsed: self.emit(
                    p.PassEv(job=cmd.job, index=k, spp_done=spp, elapsed_s=elapsed)
                ),
            )
        except Exception as exc:  # noqa: BLE001 - rule 2
            self.emit(p.ErrorEv(job=cmd.job, message=f"{type(exc).__name__}: {exc}",
                                traceback=traceback.format_exc()))
            return
        finally:
            self.cancelled.discard(cmd.job)
            self.current_job = None

        self.emit(p.Done(job=cmd.job, spp_done=result.spp_done,
                         elapsed_s=result.elapsed_s, cancelled=result.cancelled))


def _silence_mitsuba_stdout() -> None:
    """Point Mitsuba's logger at stderr and turn it down to warnings.

    Done before any other Mitsuba call. `mi.Thread.thread().logger()` is the documented
    handle in Mitsuba 3.x and was confirmed present on 3.9.0 before this was written.
    """
    import mitsuba as mi

    mi.set_log_level(mi.LogLevel.Warn)
    try:
        logger = mi.Thread.thread().logger()
        logger.clear_appenders()
        logger.add_appender(mi.StreamAppender(sys.stderr))
    except Exception as exc:  # noqa: BLE001
        # Not fatal on its own: set_log_level already suppresses the chatty levels. But say
        # so, because if a warning later lands on stdout this is the reason.
        _log(f"could not redirect the Mitsuba logger to stderr: {exc}")


def main() -> int:
    worker = Worker()
    threading.Thread(target=_reader_thread, args=(sys.stdin, worker.incoming),
                     daemon=True).start()

    # Wait for `hello` before touching Mitsuba, so a protocol mismatch is reported by a
    # process that is still cheap to start rather than after a 4-second CUDA context init.
    variant_request = "auto"
    while True:
        obj = worker.incoming.get()
        if obj is None:
            return 0
        try:
            cmd = p.decode_command(obj)
        except p.ProtocolError as exc:
            worker.emit(p.ErrorEv(message=str(exc)))
            continue
        if isinstance(cmd, p.Shutdown):
            return 0
        if isinstance(cmd, p.Hello):
            if cmd.protocol != p.PROTOCOL_VERSION:
                worker.emit(p.ErrorEv(
                    message=f"protocol mismatch: host speaks {cmd.protocol}, "
                            f"worker speaks {p.PROTOCOL_VERSION}"
                ))
                return 2
            variant_request = cmd.variant
            break
        worker.emit(p.ErrorEv(message="expected `hello` as the first command"))

    try:
        _silence_mitsuba_stdout()
        import mitsuba as mi

        variant = select_variant(variant_request)
        worker.emit(p.Ready(
            mitsuba=mi.__version__,
            variant=variant,
            python=sys.version.replace("\n", " "),
            available_variants=tuple(mi.variants()),
        ))
    except Exception as exc:  # noqa: BLE001
        worker.emit(p.ErrorEv(message=f"{type(exc).__name__}: {exc}",
                              traceback=traceback.format_exc()))
        return 3

    while worker.running:
        obj = worker.incoming.get()
        if obj is None:
            break
        try:
            cmd = p.decode_command(obj)
        except p.ProtocolError as exc:
            worker.emit(p.ErrorEv(message=str(exc)))
            continue
        match cmd:
            case p.Render():
                worker.do_render(cmd)
            case p.Cancel(job=job):
                worker.cancelled.add(job)
            case p.Shutdown():
                break
            case p.Hello():
                worker.emit(p.ErrorEv(message="`hello` received twice"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
