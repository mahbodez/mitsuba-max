# CLAUDE.md — max_side/

Runs inside 3ds Max 2027's embedded Python interpreter. You cannot execute anything here
yourself; the user runs it and pastes output back.

## Import rules

**Allowed:** stdlib, `pymxs`, `PySide6`, `shiboken6`, `core`.
**Via the bridge only:** `numpy`.
**Forbidden:** `mitsuba`, `drjit` — under any circumstance, including inside a function
body or a `try` block. Dr.Jit's native DLLs conflict with Max's loaded libraries.
`numpy_bridge.install_blocker()` runs on package import and enforces this mechanically:
`import mitsuba` anywhere in Max's process raises with an explanation.

> **Corrected by probe 06c (2026-07-30).** This file previously listed `numpy` as plainly
> allowed. Max 2027 does not ship it. `numpy_bridge.ensure_numpy()` serves it out of the
> worker venv's `site-packages` — ABI-identical, because that venv is created from Max's own
> `python.exe` — exposing `numpy` and nothing else from that directory. Import it that way
> or not at all; a bare `import numpy` at module scope will fail on a machine where the
> environment wizard has not been run.

`ensure_numpy()` must be called **before** importing any module that reaches `core.film`,
`core.tonemap` or `max_side.exr` — that means before `max_side.client` and `max_side.ui`,
both of which import them at module scope. `max_side.render()` does this, after the
environment wizard has had its chance to run; a new entry point that imports the client or
the UI has to do the same, and function-level imports in `__init__.py` are what make the
ordering expressible at all. Getting it wrong produces a bare `ModuleNotFoundError` from a
file three packages down instead of the wizard instructions.

Do not pip-install PySide6 anywhere. Max provides its own custom build and a second copy
in `site-packages` breaks it.

## Syntax floor

CPython 3.13.9 — confirmed by probe 01 against a real Max 2027 session. Use modern syntax
freely. Do not add `from __future__ import annotations` compatibility shims or
`Optional[...]` where `X | None` reads better; there is no older interpreter to support.

## Never guess pymxs

Every property name, return type, index base and unit is a `[PROBE]` until confirmed.
Write `tools/probes/NN_topic.py`, ask the user to run it, record the answer in
`docs/PROBE_RESULTS.md`, then implement against the confirmed behaviour with **no
defensive fallbacks**. A `getattr(mat, "roughness", None) or getattr(mat, "Roughness", None)`
chain hides ignorance and silently picks the wrong branch on the next Max release.

Probe scripts are read-only: no scene modification, no file writes, no dialogs. Print
labelled lines and nothing else.

## Performance

`pymxs` marshals every call through the MAXScript VM. Per-element loops are ~100× slower
than bulk accessors. Always use `meshop.getVerts` with a bitarray over `getVert` in a
loop. If you write a `for` loop that calls into `rt` per vertex or per face, that is a
defect.

## Threading

Max's main thread must never block. No `proc.communicate()`, no blocking reads, no
`time.sleep`. Worker polling is a `QTimer` at ~10 Hz. All Qt widget access happens on the
main thread.

## Module reloading

Python caches modules, so edits do not take effect on re-run. `devreload.py` purges every
`mitsuba_max.*` entry from `sys.modules` and re-imports. Keep the startup shim trivial and
stateless so reload actually works — never hold long-lived state in `__init__.py`.

## Separation

This package reads Max and produces IR. It does not know what Mitsuba is. If a Mitsuba
plugin name or parameter string appears in a file here, it belongs in `core/emit_dict.py`
instead.
