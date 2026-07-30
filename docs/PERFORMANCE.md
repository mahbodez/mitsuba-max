# Performance

Measured numbers only. Anything not measured is absent rather than estimated.

Machine: Windows 11, 3ds Max 2027.2 (CPython 3.13.9), Mitsuba 3.9.0 on `cuda_ad_rgb`.
Measurements taken headlessly via `tools/maxbatch.py` on 2026-07-30.

---

## Geometry extraction — the bottleneck

`pymxs` marshals every call through the MAXScript VM, so extraction cost is dominated by
call *count*, not by data volume. Probe 06c measured four strategies on a 1.00 M-triangle
sphere (`Sphere segments:1000` → 499 002 vertices, 998 000 faces, 501 501 texture vertices):

| strategy | time | rate |
|---|---|---|
| `meshop.getVerts` bulk fetch, no Python conversion | 0.002 s | — |
| `meshop.getVerts` bulk + per-component attribute access | 1.31 s | 0.38 M verts/s |
| MAXScript loop → binary file → `struct.unpack` (positions) | 1.39 s | 0.36 M verts/s |
| MAXScript loop → binary file (texture vertices) | 1.35 s | 0.37 M tverts/s |
| MAXScript loop → binary file (**faces**: indices, UV indices, smoothing group, material id) | **8.23 s** | 0.12 M faces/s |
| per-element `getVert` loop from Python | — | 0.39 M verts/s |
| per-element `getFace` loop from Python | — | 0.40 M faces/s |

**Conclusions**

- The famous "bulk accessors are 100× faster" advice is about the *fetch*. Once the data
  has to cross into Python, `meshop.getVerts` plus attribute access and a plain `getVert`
  loop are within 5% of each other, because the cost is marshalling `Point3` wrappers.
  0.002 s to fetch and 1.31 s to read is the whole story.
- **Faces are the bottleneck**, at five MAXScript calls each (`getFace`, `getTVFace`,
  `getFaceSmoothGroup`, `getFaceMatID`, plus the loop). There is no bulk face accessor:
  `meshop.getMapVerts` and `meshop.getMapFaces` do not exist in Max 2027 (probe 06).
- The binary-file route is used throughout anyway, for uniformity and because it keeps all
  five per-face reads inside one MAXScript loop rather than five Python round trips.

### Against the SPEC §8.1 budget

SPEC asks for roughly 10 s at 2 M triangles. Extrapolating linearly:

| stage | 1 M tri (measured) | 2 M tri (extrapolated) |
|---|---|---|
| positions | 1.4 s | 2.8 s |
| texture vertices | 1.4 s | 2.7 s |
| faces | 8.2 s | 16.5 s |
| **extraction total** | **11.0 s** | **~22 s** |

Plus `core.meshbuild` — smoothing classes, vertex splitting, PLY writing — in pure Python,
which is **not yet measured on a scene of that size**.

**So the budget is missed by roughly 2×, and the trigger condition in SPEC §8.1 is met.**
The documented remedy is a `SceneSource` backed by USD export. It has deliberately **not**
been built: the spec says the measurement is the trigger, not the licence, and a second
extraction path is a large commitment to make on an extrapolation from one sphere.

What should happen before building it:

1. Measure `core.meshbuild` at 2 M triangles. It may well dominate the 22 s above, in which
   case USD export solves the wrong half of the problem.
2. Measure a *realistic* 2 M-triangle scene — many nodes rather than one — since per-node
   overhead (`snapshotAsMesh`, three file writes) is amortised very differently.

---

## numpy is not available inside 3ds Max

`C:\Program Files\Autodesk\3ds Max 2027\Python\Lib\site-packages` contains exactly
`PySide6`, `pymxs`, `qtmax`, `shiboken6`. `import numpy` raises `ModuleNotFoundError`, and
`sys.path` holds no user site directory.

This is a performance fact as much as an architectural one: the entire mesh path —
smoothing-group classification, vertex splitting, PLY serialisation — runs in pure Python
because it has to. `core.meshbuild` and `core.transform` are stdlib-only for that reason,
not as a style preference.

The pixel path does get numpy, borrowed from the worker venv through
`max_side.numpy_bridge`. Because that venv is created from Max's own `python.exe`, the ABI
matches exactly. The bridge serves `numpy` and blocks `mitsuba` and `drjit` outright.

---

## Rendering

`worker.selftest` on this machine:

| scene | resolution | spp | time |
|---|---|---|---|
| `mi.cornell_box()` | 256×256 | 32 | 0.52 s |
| white furnace | 64×64 | 512 | < 0.1 s |

Golden scene suite (`tools/run_golden.py`), five checks including a 4096-spp furnace and a
512-spp Cornell box diffed against Mitsuba's own: **well under a minute end to end.**

### Variant selection

`mi.variants()` advertises 13 variants on this machine. It reports what was *compiled*, not
what *works*: `llvm_ad_rgb` is advertised and `import mitsuba` prints
`jitc_llvm_init(): LLVM API initialization failed` to stderr, while `cuda_ad_rgb` works.
Only `mi.set_variant` tells the truth, which is why `worker.render.VARIANT_PREFERENCE` ends
in `scalar_rgb` — a two-entry `cuda → llvm` chain would leave a CPU-only machine with the
same LLVM problem unable to render at all.

---

## Cold start

| step | time |
|---|---|
| `3dsmaxbatch.exe` cold start | 30–60 s |
| `python -m venv` from Max's interpreter | ~5 s |
| `pip install mitsuba numpy` | 2–5 min, ~200 MB |
| worker process launch to `ready` | not yet measured |

The last row matters for interactive use — it is paid once per session, not per render,
because the client keeps one worker alive across jobs — and it should be measured before
M4 is called done.

---

## Not yet measured

Listed so their absence is not read as "fine":

- `core.meshbuild` throughput at any size.
- Worker launch to `ready` latency (CUDA context initialisation dominates).
- Export time on a realistic multi-node scene rather than one dense sphere.
- Film mmap write and read cost at 1920×1080 (33 MB per pass).
- Whether the 10 Hz UI poll is visible in Max's frame time on a heavy scene.
