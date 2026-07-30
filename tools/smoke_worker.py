"""Drive the real worker over the real protocol, end to end, without 3ds Max.

    uv run python tools/smoke_worker.py

Launches `python -m worker` in the configured worker environment exactly as
`max_side.client` does — same argument list, same scrubbed environment, same
newline-delimited JSON — renders a golden fixture, and reads the result out of the shared
film. Everything except `pymxs` and Qt is exercised.

This is the check that would have caught the stdout-banner problem in probe 12 before it
reached a user: the host reads the protocol stream for real, so anything else written to
stdout shows up here as a decode failure rather than as a mysterious hang.
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import protocol as p  # noqa: E402
from core.ir import Scene  # noqa: E402
from max_side.client import WorkerClient, WorkerCrashed  # noqa: E402
from max_side.env_setup import managed_venv_python, write_project_pth  # noqa: E402
from tests.golden.scenes import build_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke_worker")
    ap.add_argument("--scene", default="cornell_box")
    ap.add_argument("--interpreter", default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "smoke")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args(argv)

    interpreter = args.interpreter or str(managed_venv_python())
    if not Path(interpreter).is_file():
        print(f"no worker interpreter at {interpreter}", file=sys.stderr)
        print("run max_side.setup_environment() in Max, or pass --interpreter",
              file=sys.stderr)
        return 2

    write_project_pth(interpreter, ROOT)
    scenes = build_all(args.out)
    scene: Scene = scenes[args.scene]

    client = WorkerClient(interpreter=interpreter, project_root=ROOT)
    print(f"launching {interpreter}")
    client.start()

    job = None
    passes_seen = 0
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            for event in client.poll_events():
                match event:
                    case p.Ready():
                        print(f"ready: mitsuba {event.mitsuba}  variant {event.variant}")
                        print(f"       {event.python}")
                        film = args.out / "smoke.film"
                        job = client.submit(scene, film_path=film, scene_root=args.out)
                        print(f"submitted job {job}: {scene.camera.film_width}x"
                              f"{scene.camera.film_height}, "
                              f"{scene.settings.passes} passes of "
                              f"{scene.settings.spp_per_pass} spp")
                    case p.PassEv():
                        passes_seen += 1
                        got = client.read_film()
                        shown = "film busy"
                        if got is not None:
                            pixels, header = got
                            shown = (f"film {header.width}x{header.height} "
                                     f"mean {float(pixels[..., :3].mean()):.5f}")
                        print(f"  pass {event.index}  {event.spp_done} spp  "
                              f"{event.elapsed_s:.2f}s  {shown}")
                    case p.Done():
                        print(f"done: {event.spp_done} spp in {event.elapsed_s:.2f}s "
                              f"(cancelled={event.cancelled})")
                        got = client.read_film()
                        if got is None:
                            print("FAIL  the film could not be read after `done`",
                                  file=sys.stderr)
                            return 1
                        pixels, header = got
                        print(f"final film: {header.width}x{header.height}, "
                              f"passes_done={header.passes_done}, state={header.state}, "
                              f"mean {float(pixels[..., :3].mean()):.5f}, "
                              f"max {float(pixels[..., :3].max()):.5f}")
                        if passes_seen != scene.settings.passes:
                            print(f"FAIL  saw {passes_seen} pass events, expected "
                                  f"{scene.settings.passes}", file=sys.stderr)
                            return 1
                        if float(pixels[..., :3].max()) <= 0.0:
                            print("FAIL  the film is black", file=sys.stderr)
                            return 1
                        preamble = client.preamble()
                        if preamble:
                            print("FAIL  the worker wrote non-JSON to stdout:",
                                  file=sys.stderr)
                            print(preamble, file=sys.stderr)
                            return 1
                        print("\nOK  worker protocol, shared film and render all sound")
                        return 0
                    case p.ErrorEv():
                        print(f"FAIL  worker error: {event.message}", file=sys.stderr)
                        print(event.traceback, file=sys.stderr)
                        return 1
            time.sleep(0.05)
    except WorkerCrashed as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        client.shutdown()

    print(f"FAIL  timed out after {args.timeout}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
