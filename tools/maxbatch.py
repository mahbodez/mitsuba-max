"""Run a Python script inside headless 3ds Max via 3dsmaxbatch.exe.

    python tools/maxbatch.py tools/probes/01c_camera_fov.py [--timeout 300]

3dsmaxbatch does not echo printed output to stdout by default, so this wrapper always
passes -listenerLog and merges the log back into the console. It also enforces a timeout
and kills the process tree on failure, because an abandoned 3dsmax.exe will hold a licence
seat and block the next run.

See CLAUDE.md for the rules governing batch invocations. In particular: never point this
at the user's working scene.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

SEARCH_GLOBS = (
    r"C:\Program Files\Autodesk\3ds Max *\3dsmaxbatch.exe",
    r"C:\Program Files\Autodesk\3ds Max *\bin\3dsmaxbatch.exe",
)


def find_batch_exe() -> pathlib.Path:
    override = os.environ.get("MITSUBA_MAX_BATCH_EXE")
    if override:
        p = pathlib.Path(override)
        if not p.is_file():
            raise SystemExit(f"MITSUBA_MAX_BATCH_EXE does not exist: {p}")
        return p

    found: list[pathlib.Path] = []
    for pattern in SEARCH_GLOBS:
        root = pathlib.Path(pattern).parent.parent
        stem = pathlib.Path(pattern).name
        sub = pathlib.Path(pattern).parent.name
        if not root.is_dir():
            continue
        for d in sorted(root.glob(pathlib.Path(pattern).parent.name)):
            cand = d / stem if sub.startswith("3ds Max") else d / sub / stem
            if cand.is_file():
                found.append(cand)
    if not found:
        raise SystemExit(
            "3dsmaxbatch.exe not found. Set MITSUBA_MAX_BATCH_EXE to its full path.\n"
            "This is PROBE 13 - confirm the executable exists before relying on it."
        )
    return sorted(found)[-1]


def child_env() -> dict[str, str]:
    """A clean environment for 3dsmaxbatch.

    Confirmed by probe 13: launched from an activated virtualenv, Max's embedded
    interpreter reports `sys.prefix` as *that virtualenv* while `sys.executable` is still
    Max's own python.exe. The probe then runs against a site-packages built for a different
    CPython patch level than the one Max ships, which is a superb way to spend an afternoon
    debugging an import error that does not exist for real users.

    Real users launch Max from the Start menu, so the honest environment to probe in is one
    with these variables absent.
    """
    env = dict(os.environ)
    for var in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "CONDA_PREFIX"):
        env.pop(var, None)
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", type=pathlib.Path)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--scene", type=pathlib.Path, default=None,
                    help="Optional fixture .max under tests/fixtures. Never the user's scene.")
    args = ap.parse_args()

    if not args.script.is_file():
        raise SystemExit(f"script not found: {args.script}")
    if args.scene is not None and "fixtures" not in args.scene.parts:
        raise SystemExit("refusing to load a scene outside tests/fixtures - see CLAUDE.md")

    exe = find_batch_exe()
    log = pathlib.Path(tempfile.mkdtemp(prefix="maxbatch_")) / "listener.log"

    cmd = [str(exe), str(args.script.resolve()), "-v", "5", "-listenerLog", str(log)]
    if args.scene is not None:
        cmd += ["-sceneFile", str(args.scene.resolve())]

    print(f"[maxbatch] {exe.parent.name}")
    print(f"[maxbatch] {' '.join(cmd)}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout,
                              env=child_env())
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/IM", "3dsmax.exe"], capture_output=True)
        subprocess.run(["taskkill", "/F", "/T", "/IM", "3dsmaxbatch.exe"], capture_output=True)
        raise SystemExit(
            f"[maxbatch] TIMEOUT after {args.timeout}s - process tree killed"
        ) from None

    listener = log.read_text(errors="replace") if log.is_file() else "(no listener log)"

    print("--- listener log ---")
    print(listener)
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr)

    # 3dsmaxbatch can exit 0 with a traceback in the log, so the log is the primary signal.
    # It can also exit non-zero having run the script perfectly: probe 13 saw 0xFFFFFF7E
    # (-130) on a clean run, which is 3dsmaxbatch's own shutdown code and says nothing about
    # the script. Both signals are reported; neither is trusted alone.
    signed = proc.returncode - (1 << 32) if proc.returncode >= (1 << 31) else proc.returncode
    tracebacky = "Traceback (most recent call last)" in listener
    marker = "PROBE_COMPLETE" in listener

    if tracebacky:
        print(f"[maxbatch] FAILED  traceback in listener log  exit={signed}")
        return 1
    if not marker:
        print(f"[maxbatch] FAILED  script did not reach its end marker  exit={signed}")
        print("[maxbatch] every probe must print PROBE_COMPLETE as its last line")
        return 1

    print(f"[maxbatch] ok  (exit={signed}, ignored - see comment)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
