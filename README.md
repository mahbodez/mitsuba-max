# mitsuba-max

Mitsuba 3 rendering inside Autodesk 3ds Max 2027. Python only — no C++ SDK component, no
compiled extension, no `.dlx`.

Select **Mitsuba** from the menu, press Render, and a progressively refining, physically
correct image of your scene appears in a window docked to Max.

## How it works

The renderer does **not** run inside Max. Dr.Jit loads its own native DLLs and LLVM/CUDA
backends, and Max already has TBB, Qt, Arnold's LLVM and OpenImageIO in the same address
space. So the pipeline is:

```
pymxs scene  ──►  IR (plain JSON)  ──►  Mitsuba scene  ──►  separate OS process
                                                                    │
     Qt window in Max  ◄──── mmap'd float32 film ◄──────────────────┘
```

Three packages with one-directional dependencies — `max_side → core ← worker` — that never
share an interpreter. `core` imports neither `pymxs` nor `mitsuba`, which is what makes the
translation layer testable with neither application installed.

## Requirements

- 3ds Max 2027 (embeds CPython 3.13.9 and PySide6 6.8.3 — both provided, install neither).
- A Python environment for the worker containing `mitsuba` and `numpy`. The plugin can
  create one for you from Max's own `python.exe`; see the environment wizard on first run.
- A CUDA GPU is optional. The worker falls back to `llvm_ad_rgb` and tells you which
  variant it picked in the window title.

## Using it in Max

### 1. Install the startup shim

`install/startup_mitsuba_max.py` is the only file Max needs to know about. Copy it into a
Max startup scripts folder:

```
%LOCALAPPDATA%\Autodesk\3dsMax\2027 - 64bit\ENU\scripts\startup\
```

Because the copy no longer sits next to the repository, tell it where the repository is —
set a **user** environment variable and restart Max so it picks the variable up:

```
setx MITSUBA_MAX_ROOT D:\path\to\mitsuba-max
```

Prefer to skip the environment variable? Make a symlink instead of a copy, from an elevated
prompt, and the shim finds the repo one level up by itself:

```
mklink "%LOCALAPPDATA%\Autodesk\3dsMax\2027 - 64bit\ENU\scripts\startup\startup_mitsuba_max.py" D:\path\to\mitsuba-max\install\startup_mitsuba_max.py
```

Restart Max. The listener should print:

```
[mitsuba-max] four actions registered under the 'mitsuba-max' category
[mitsuba-max] loaded from D:\path\to\mitsuba-max
```

If it instead prints `not a plugin root`, `MITSUBA_MAX_ROOT` points somewhere without a
`max_side/` directory. A startup failure never raises — Max always finishes starting, and
the traceback is in the listener.

### 2. Put the actions somewhere you can click them

The shim registers four macroscripts under the category **mitsuba-max**. They are actions,
not a menu, so you place them yourself: **Customize → Customize User Interface → Menus**
(or Toolbars, or Quads, or a keyboard shortcut), category **mitsuba-max**.

| Action | What it does |
| --- | --- |
| Render with Mitsuba | Export, launch the worker, open the render window |
| Export Mitsuba scene | Export IR and assets only — no render |
| Mitsuba environment setup | Create and validate the worker Python environment |
| Reload mitsuba-max | Developer: purge and re-import every plugin module |

Everything is also reachable from the Python listener without touching the UI:

```python
import max_side
max_side.setup_environment()
max_side.render()
max_side.render(selection_only=True)
max_side.export_only()
```

### 3. Run the environment setup once

First run only. **Mitsuba environment setup** creates a venv at
`%LOCALAPPDATA%\mitsuba-max\venv` from Max's own `python.exe` and pip-installs `mitsuba`
and `numpy` into it — about 200 MB, so give it a few minutes on a slow connection. Max's
UI is unresponsive while it downloads.

It prints the versions it ended up with on success, and the subprocess's real stderr on
failure — if PyPI is unreachable through a corporate proxy, you will see pip say so
verbatim. The chosen interpreter is remembered in `%LOCALAPPDATA%\mitsuba-max\settings.json`,
which is per-machine and deliberately not stored in the scene file.

`max_side.setup_environment(r"C:\some\other\python.exe")` builds the venv from a different
base interpreter if you need one. Never install PySide6 into that venv — Max ships its own
build and a second copy breaks it.

### 4. Render

Select **Render with Mitsuba**. The plugin extracts the scene, writes assets, starts the
worker process and opens a render window docked to Max. It refines progressively; the
window title names the variant that was actually picked (`cuda_ad_rgb`, `llvm_ad_rgb` or
`scalar_rgb`).

In the window you get exposure and gamma sliders — applied to the cached float buffer for
display, never baked — zoom by mouse wheel, pan by drag, Cancel, and Save as PNG / EXR /
`scene.xml`. Anything the translator could not represent shows up in the warnings list,
naming the node; nothing is ever silently substituted.

Sampling, resolution scale, integrator and depth settings live in
`%LOCALAPPDATA%\mitsuba-max\settings.json`. The defaults are 16 spp × 32 passes.

If the render window never appears, run **Export Mitsuba scene** on its own: it exercises
the whole Max-reading half without the worker, and prints the shape, triangle and warning
counts.

### 5. If you are editing the plugin

Python caches modules, so a saved edit does nothing until you run **Reload mitsuba-max**
(or `max_side.reload_plugin()`), which purges every project module from `sys.modules` and
re-imports. Only the shim itself needs a Max restart, and it holds no state so it rarely
changes. Exported scenes land in `%TEMP%\mitsuba-max\export` unless `export_root` is set in
the settings file.

## Known limitations

**Cancel latency is one pass.** `mi.render()` cannot be interrupted mid-call, so the sample
budget is split into passes and a pending cancel is checked between them. With the default
32 passes, pressing Cancel stops the render within one pass, not instantly. This is a
property of the renderer, not a bug in the integration.

**v1 scope.** `PhysicalMaterial` with `Bitmaptexture` inputs, photometric and standard
lights, HDRI environment, Physical cameras, and any object with a triangulated snapshot.
No animation, no instancing, no Standard material, no Multi/Sub-Object, no AOVs. Anything
unsupported produces a warning naming the node and a documented placeholder — never a
silent substitution.

## Development

```
uv sync
uv run pytest tests/ -q
uv run ruff check .
uv run mypy core worker
```

The worker environment is separate and is not created by `uv sync`; the plugin builds it at
runtime. `python -m worker.selftest` renders the built-in Cornell box in that environment
and writes an EXR, proving it is sound independently of Max.

`docs/PROBE_RESULTS.md` records confirmed 3ds Max behaviour. Anything not in there is an
assumption — see `CLAUDE.md` for the probe workflow.

## Licence

MIT.
