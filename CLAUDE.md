# CLAUDE.md — mitsuba-max

Mitsuba 3 rendering integration for Autodesk 3ds Max 2027, written entirely in Python.
Read `SPEC.md` before writing code. Read the scoped `CLAUDE.md` in whichever package
you are editing — the three packages have mutually exclusive import rules.

## What this is

A tier-B DCC integration: extract the Max scene via `pymxs`, translate it to a
renderer-agnostic intermediate representation, emit a Mitsuba 3 scene, render it in a
**separate OS process**, and stream the result back into a PySide6 window docked to Max.

## Hard invariants — violating any of these is a defect, not a style choice

1. **Never `import mitsuba` or `import drjit` inside 3ds Max's process.** Dr.Jit loads
   its own native DLLs and LLVM/CUDA backends; Max already has TBB, Qt, Arnold's LLVM
   and OpenImageIO in the same address space. The renderer lives in `worker/` and is
   reached only over the IPC protocol.
2. **Never install PySide6 into the worker venv.** Autodesk explicitly warns that a
   PySide6 in `site-packages` conflicts with Max's custom build. The worker is headless
   and needs no Qt.
3. **`core/` imports neither `pymxs` nor `mitsuba`.** It is pure Python, runs under plain
   CPython, and is the only package with meaningful unit tests. If you find yourself
   wanting `pymxs` in `core/`, the abstraction is wrong — fix the IR instead.
4. **Never hardcode a Python version or an install path.** No `Python39`, no
   `C:\Program Files\Autodesk\3ds Max 2027`. Probe at runtime via `sys.version_info`,
   `sys.executable`, and `pymxs.runtime.maxVersion()`.
5. **Never block Max's main thread.** No `subprocess.communicate()`, no `time.sleep`, no
   blocking socket reads on the UI thread. Poll via `QTimer`.
6. **Never guess a `pymxs` API signature.** See the probe workflow below.
7. **Do not add a C++ component, a compiled extension, or a `.dlx`.** If a task seems to
   require one, stop and say so in your response instead of building it.

## Running 3ds Max yourself — `3dsmaxbatch.exe`

`3dsmaxbatch.exe`, in the Max install root, runs a `.py` file headlessly with `pymxs`
available. This means you can close the probe loop yourself instead of waiting on the
user. Use `tools/maxbatch.py`, which wraps it:

```
uv run python tools/maxbatch.py tools/probes/01c_camera_fov.py
```

The wrapper passes `-v 5` and `-listenerLog` to a temp file, merges the listener log with
stdout/stderr, enforces a timeout, and kills the process tree on failure. Never invoke
`3dsmaxbatch.exe` directly — the bare invocation silently swallows printed output unless
`-listenerLog` is set, which will cost you an hour before you notice.

### Rules for batch invocations — these are not negotiable

1. **Never open the user's scene.** Every batch script either builds its scene
   programmatically or loads an explicit fixture from `tests/fixtures/*.max`. Do not pass
   an arbitrary `-sceneFile`, and never touch anything under the user's documents folder.
2. **One at a time, always with a timeout.** Cold start is 30–60 s. Concurrent invocations
   contend for the licence and produce confusing failures. Default timeout 300 s, then
   kill the tree.
3. **It consumes a licence seat.** If a run fails with a licensing error, stop and tell the
   user rather than retrying in a loop — retrying can lock a seat.
4. **Batch mode has no UI.** Anything involving Qt, the viewport, ActiveShade or a
   modal dialog cannot be verified this way and stays on the manual checklist.
5. **Scripts remain read-only unless they are explicitly fixture-builders.** A probe never
   saves a file, never modifies a loaded scene, and never writes outside `build/probe/`.
6. **Check the exit code and the log.** `3dsmaxbatch` can exit 0 with a Python traceback
   sitting in the listener log. Treat any traceback in the log as a failure.

Verify `3dsmaxbatch.exe` exists and behaves before relying on it — that is **`[PROBE 13]`**,
and it includes checking whether it runs while the user has the Max UI open.

### The probe discipline still applies

Being able to run Max does not license guessing. The rule is unchanged: anything tagged
**`[PROBE]`** in `SPEC.md` is an assumption, and you resolve it by running a script and
recording the answer in `docs/PROBE_RESULTS.md` — you just no longer need the user in the
loop to do it.

Do **not** write speculative code with `try/except AttributeError` fallback chains. A
`getattr(mat, "roughness", None) or getattr(mat, "Roughness", None)` chain hides the fact
that nobody checked, and silently picks the wrong branch on the next Max release. Run a
probe, learn the answer, write one line.

Probe scripts live in `tools/probes/NN_topic.py`, print labelled lines, and isolate each
query in its own `try` so one failure cannot mask the rest — the first combined probe in
this project aborted after three lines for exactly that reason.

## Verification you *can* do yourself

- `pytest tests/` — the whole `core` package, headless, no Max, no Mitsuba.
- `python -m worker.selftest` — renders the built-in Cornell box in the worker venv and
  writes an EXR. Proves the worker environment is sound.
- The four correctness scenes in `tests/golden/` run end-to-end from a **hand-authored
  IR JSON fixture**, bypassing Max entirely. Every translation bug that does not involve
  reading Max is catchable this way. Keep that property: IR fixtures are checked in as
  JSON, and `core` can round-trip IR to and from JSON.

## Verification only the user can do

Batch mode has no UI, so anything visual or interactive still needs a human. Maintain
`docs/MANUAL_CHECKS.md` as a numbered checklist covering exactly that residue: the Qt
window parenting to Max, the render window actually appearing, progressive refresh looking
right, cancel responding, and any judgement call about whether an image looks correct.

Each entry states what to build, what to click, and what to paste back. Keep them under two
minutes — a check that takes longer will not get run, and an unrun check is worse than no
check because it looks like coverage.

## Development loop inside Max

Python modules stay cached after import, so edits do not take effect on re-run. The
plugin must ship a developer reload entry point that purges every `mitsuba_max.*` module
from `sys.modules` and re-imports the top level. Structure the Max-side code so that the
startup shim is trivial and all real logic lives in reloadable modules — never define
long-lived state in the shim.

## Commands

```
uv sync                      # dev environment for core/ + tests
uv run pytest tests/ -q
uv run ruff check .
uv run mypy core worker
```

The worker venv is separate and created at runtime by the plugin, not by `uv sync`.

## Version control

Git, `main`, no remote yet. `.gitattributes` normalises to LF in the repository and checks
out native, so do not "fix" line endings in a diff — a commit that touches every line of a
file because of CRLF is a review-killer, and the attributes file is what prevents it.

What stays out of the repository, and why the reason matters more than the list:

- `build/`, `__pycache__/`, the tool caches — derived, regenerated on demand.
- `tests/golden/assets/` — the PLY and PNG files are regenerated deterministically by
  `tests/golden/regenerate.py`. **The fixtures are the JSON**, and those are checked in.
  If a golden test ever needs a binary asset committed to pass, the fixture stopped being
  the source of truth and that is the bug to fix.
- `.venv/` and the worker venv — one is `uv sync`'s, the other lives in
  `%LOCALAPPDATA%\mitsuba-max\venv` and is built at runtime. Neither is ever in-tree.
- `graphify-out/` — a local knowledge-graph cache, rebuilt with `graphify update .`.

`uv.lock` **is** committed. See the note in `.gitignore`.

Nothing under `%LOCALAPPDATA%\mitsuba-max\` is repository state: `settings.json` holds an
interpreter path that exists only on one machine, which is exactly why it is not in the
scene file either.

Commit only when the user asks. Run `pytest`, `ruff` and `mypy` before you do — the
definition of done below is not a separate ceremony, it is what a commit is expected to
have satisfied.

## Style

- Python 3.13 syntax floor throughout. Confirmed: Max 2027 embeds CPython 3.13.9
  (MSVC v1938 / VS 2022). Modern syntax is fine everywhere — `match`, `X | Y`
  annotations, `dataclass(slots=True)`.
- Type-annotate everything in `core/`. `mypy --strict` must pass there.
- Dataclasses over dicts for the IR. `float` everywhere, never `Decimal`.
- No wildcard imports, especially not `from pymxs.runtime import *`.
- Docstrings on every translator stating the Max source parameter, the Mitsuba target
  parameter, and whether the mapping is exact or approximate.

## Definition of done for any milestone

1. `pytest` green, `mypy --strict core` clean.
2. New golden IR fixtures checked in for anything the milestone translates.
3. `docs/MANUAL_CHECKS.md` updated with the checks the user must run.
4. `docs/MATERIAL_MAPPING.md` updated if any parameter mapping changed, including the
   exact/approximate/unsupported classification.
5. No `[PROBE]` tag left unresolved for code that shipped in the milestone.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
