# Getting started

## Kickoff prompt for Claude Code

Paste this as the first message in a fresh session at the repo root:

> Read `CLAUDE.md` and `SPEC.md` in full before doing anything.
>
> This session covers **M0 only** — the vertical slice. Do not start M1.
>
> I cannot give you access to 3ds Max. Follow the probe workflow: before writing any code
> that touches `pymxs`, write the probe scripts for the `[PROBE]` items that M0 depends on
> (01, 06, 07, 10 at minimum), put them in `tools/probes/`, and stop. I will run them in
> Max and paste the output. Then record the answers in `docs/PROBE_RESULTS.md` and
> implement against the confirmed behaviour with no defensive fallbacks.
>
> Start by scaffolding the repo (`pyproject.toml`, package skeletons, the scoped
> `CLAUDE.md` files are already present, `tests/`, `ruff` and `mypy` config), then write
> `core/ir.py`, `core/transform.py` and their tests — none of that needs Max — and then
> write the probes.

Keeping M0 scoped to one milestone per session matters. An agent handed the whole spec
will write plausible `pymxs` code for all eleven probe items and you will spend longer
unpicking the guesses than you would have spent building it.

## Your first task

Run this in Max's scripting listener (Scripting → Python → Python Listener) and paste the
output back into the session. It resolves probe 01, which blocks everything else:

```python
import sys, pymxs
from pymxs import runtime as rt
print("python:", sys.version)
print("exe:", sys.executable)
print("maxVersion:", rt.maxVersion())
print("systemScale:", rt.units.SystemScale, rt.units.SystemType)
print("aperture:", rt.getRendApertureWidth())
try:
    import shiboken6, PySide6
    print("pyside6:", PySide6.__version__)
    print("maxhwnd ok:", bool(rt.windows.getMAXHWND()))
except Exception as e:
    print("qt probe failed:", e)
```

## Manual check log

Maintain `docs/MANUAL_CHECKS.md` as a numbered list that grows with each milestone. Each
entry states what to build in Max, what to click, and what to paste back. Keep every check
under two minutes — longer ones do not get run, and an unrun check is worse than no check
because it looks like coverage.

Seed entries for M0:

1. Create a teapot, assign a PhysicalMaterial with a non-default base colour and
   roughness, add one omni light and one target camera. Run the export. Paste the
   generated `scene.xml` and the warnings list.
2. Render. Paste a screenshot and the pass/elapsed readout.
3. Kill the worker process from Task Manager mid-render. Confirm the UI reports the exit
   code and stderr tail rather than hanging or crashing Max.
