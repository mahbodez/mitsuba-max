# CLAUDE.md — worker/

Runs in its own virtual environment as a separate OS process. Headless.

## Import rules

**Allowed:** stdlib, `mitsuba`, `numpy`, `core`.
**Forbidden:** `pymxs`, `PySide6`, `shiboken6`. The worker has no UI and must never
install Qt — Autodesk warns that a PySide6 in `site-packages` conflicts with Max's build.

## Variant

Try `cuda_ad_rgb`, fall back to `llvm_ad_rgb`, honour an explicit override from settings.
Report the variant actually selected in the `ready` message — a user must never be
uncertain about whether they are on GPU. `mi.set_variant` must be called exactly once,
before any other Mitsuba use.

## Rendering

`mi.render()` cannot be interrupted. Split the sample budget into passes, check for a
pending `cancel` between passes, and accumulate a running mean with a distinct seed per
pass:

```
L̂_k = L̂_{k−1} + (L_k − L̂_{k−1}) / k
```

After each pass, write the accumulated float32 RGBA into the mmap film under the seqlock
from `core/film.py` (increment `seq` to odd, write, increment to even), then emit a
`pass` event on stdout.

## Protocol discipline

- stdout carries **only** newline-delimited JSON. Any incidental print corrupts the
  stream — route all human-readable output to stderr.
- Mitsuba's own logging goes to stderr, never stdout. Set the log level accordingly.
- Every exception is caught at the loop boundary and reported as an `error` event with the
  full traceback string. The worker stays alive and ready for the next job; only
  `shutdown` ends it.
- Unknown command or protocol version mismatch: reply with an `error` naming both
  versions and do not attempt the render.

## Self-test

`python -m worker.selftest` renders `mi.cornell_box()` and writes an EXR. It exists so the
user can prove the environment is sound independently of Max, and so environment failures
produce a clear error instead of a mysterious silent crash. Keep it dependency-free
beyond `mitsuba`.
