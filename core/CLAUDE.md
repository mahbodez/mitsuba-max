# CLAUDE.md — core/

Pure Python. Runs under plain CPython with no host application and no renderer.

## Import rules

**Allowed:** stdlib. `numpy` only in `film.py` and `tonemap.py`.
**Forbidden:** `pymxs`, `mitsuba`, `drjit`, `PySide6`, `shiboken6`.

> **Corrected by probe 06c (2026-07-30).** This file previously allowed `numpy`
> everywhere. 3ds Max 2027 does **not** ship numpy — its `site-packages` holds exactly
> PySide6, pymxs, qtmax and shiboken6 — so any module on the Max-side import path that
> uses it cannot load. `ir.py`, `transform.py`, `units.py`, `meshbuild.py`, `assets.py`,
> `protocol.py`, `emit_dict.py`, `emit_xml.py` and `registry.py` are therefore
> **stdlib-only**, and the whole extract → IR → emit path runs without numpy.
>
> `film.py` and `tonemap.py` are the exceptions: a per-pixel Python loop over a megapixel
> float buffer is not a repaint budget. They obtain numpy through
> `max_side.numpy_bridge`, which borrows it from the worker venv (same ABI, since that venv
> is built from Max's own `python.exe`) and hard-blocks `mitsuba` and `drjit`.

A test asserts this by importing every module in `core/` in a bare interpreter. If you
need Max data here, the IR is missing a field — add the field, do not add the import.

## Responsibilities

- `ir.py` — the dataclasses and exact JSON round-trip. This is the project's contract.
- `transform.py` — the Z-up→Y-up basis change `C`, the conjugation `C T C⁻¹`,
  determinant sign detection, winding reversal.
- `units.py` — scene scale, candela→W/sr, spot half-angle conversion, RGB luminance split.
- `emit_dict.py` / `emit_xml.py` — two backends over one IR. They must produce
  semantically identical scenes; there is a test that renders both and compares.
- `assets.py` — content-hashed file naming and the manifest.
- `protocol.py`, `film.py` — schemas shared across the process boundary.

## Rules

- `mypy --strict` must pass. No `Any` in public signatures.
- Every conversion function takes and returns plain floats/tuples, never Max or Mitsuba
  objects, and has a docstring stating the source units and the target units.
- Every approximate conversion carries a comment naming what is lost and why.
- No I/O in `transform.py` or `units.py` — they are pure functions and are the most
  heavily unit-tested part of the codebase.
- Any physical conversion gets a test with a hand-computed expected value in the
  docstring. `I_e = I_v / η` with `I_v = 1000 cd`, `η = 250 lm/W` gives `4.0 W/sr` —
  that kind of assertion.

## Testing

Property-based tests belong here. Two that must exist:

1. For random `T_max` and random `p`: `C @ (T_max @ p) == (C @ T_max @ inv(C)) @ (C @ p)`
   within floating tolerance.
2. `Scene.from_json(scene.to_json()) == scene` for every golden fixture.
