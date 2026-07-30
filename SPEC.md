# mitsuba-max — implementation specification

Target host: Autodesk 3ds Max 2027 (Windows). Target renderer: Mitsuba 3 (latest PyPI).
Language: Python only. License: MIT. Package name: `mitsuba-max`.

Items tagged **`[PROBE]`** are unverified assumptions. Resolve them via the probe
workflow in `CLAUDE.md` before writing dependent code.

---

## 1. Goal and non-goals

**Goal.** A 3ds Max artist selects "Mitsuba" from a menu, presses Render, and sees a
progressively refining, physically-correct image of their scene in a window inside Max,
with materials and lights that visually match what they authored.

**In scope for v1.0**
- PhysicalMaterial translation with Bitmap texture inputs.
- Photometric and standard lights; HDRI environment.
- Target and free cameras, including lens shift.
- Editable-Poly / Editable-Mesh / any object with a triangulated snapshot.
- Progressive rendering with cancel, exposure and gamma controls, EXR/PNG save.
- Scene export to `.xml` for reproducibility.

**Explicitly out of scope for v1.0**
- Animation, sequences, motion blur.
- Instancing (deferred to M5; the interfaces anticipate it).
- Standard material, Multi/Sub-Object, Arnold Standard Surface, procedural map baking.
- Sun/sky systems, atmospherics, volumetrics, hair, particles, displacement.
- Render elements / AOVs, ActiveShade, Render Setup integration.
- Anything requiring the C++ SDK.

When a scene contains an unsupported feature, the exporter must **not** guess. It emits a
structured warning naming the node and the reason, substitutes a documented placeholder
(50% gray `diffuse` for materials, skip for lights), and surfaces the warning list in the
UI. Silent substitution is a defect.

---

## 2. Environment

### 2.1 Host side
Runs in Max 2027's embedded interpreter. **Confirmed by probe 01** (see
`docs/PROBE_RESULTS.md`):

- `sys.version` → `3.13.9 [MSC v.1938 64 bit (AMD64)]`
- `sys.executable` → `C:\Program Files\Autodesk\3ds Max 2027\Python\python.exe`
- `maxVersion()` → build 29000, release year 2027, update `.2`

Syntax floor is therefore 3.13. PySide6 is provided by Max — never pip-install it; its
exact version is still unconfirmed (probe 10).

### 2.2 Worker side
A dedicated virtual environment containing exactly `mitsuba`, `numpy`, and nothing else.

Mitsuba 3.9.0 publishes `cp313` Windows wheels and Dr.Jit builds cp39–cp314, so **Max's
own `python.exe` is a valid base interpreter for the venv**. A venv created from it runs
in a separate process with its own `site-packages`; the DLL conflicts that motivate
out-of-process rendering are in-process only, so isolation is preserved. This removes the
most common onboarding blocker — the user needing to install Python at all.

Wizard preference order: (1) an existing configured interpreter; (2) a managed venv
created from Max's `sys.executable`; (3) a user-browsed interpreter. Never pass
`--system-site-packages`. **`[PROBE 12]`** confirm Max's `python.exe` can create a working
venv — check for a custom `._pth` or `sitecustomize` that could leak Max's paths in.

The plugin must handle environment setup as a first-class UI flow, because this is the
most common failure point in DCC renderer integrations:

1. Look for a configured interpreter path in user settings.
2. If absent, offer: (a) create a managed venv at
   `%LOCALAPPDATA%\mitsuba-max\venv` using a user-selected base interpreter, then
   `pip install mitsuba numpy`; or (b) browse to an existing interpreter.
3. Validate by running, as a subprocess:
   `python -c "import mitsuba, sys; print(mitsuba.__version__); print(sys.version)"`
4. On failure, show the subprocess's **actual stderr text** in a copyable text box. Never
   show "Failed to initialise Mitsuba" with the real error swallowed. Corporate networks
   block PyPI and users need to see that.
5. Cache the validated path and the reported Mitsuba version in settings.

### 2.3 Variant selection
Default `cuda_ad_rgb`. The worker probes availability at startup:

```python
import mitsuba as mi
try:
    mi.set_variant("cuda_ad_rgb")
except Exception:
    mi.set_variant("llvm_ad_rgb")
```

Report the variant actually selected back to the host in the `ready` message and display
it in the UI title bar. A user must never be confused about whether they are on GPU.
Expose an explicit override in settings (`auto` / `cuda_ad_rgb` / `llvm_ad_rgb` /
`scalar_rgb`); `scalar_rgb` is useful for debugging determinism.

---

## 3. Architecture

Three packages with strictly one-directional dependencies:

```
max_side/  ->  core/  <-  worker/
```

`max_side` and `worker` both depend on `core`; they never depend on each other, and
`core` depends on nothing. They run in different processes with different interpreters.

```
mitsuba_max/
  max_side/            # Max's interpreter. pymxs + PySide6 allowed. No mitsuba.
    __init__.py        # startup registration, menu item
    source.py          # SceneSource protocol; PymxsSource implementation
    mesh.py            # bulk vertex extraction, smoothing groups, vertex splitting
    materials.py       # PhysicalMaterial -> IR (reads Max, emits IR only)
    lights.py          # light nodes -> IR
    camera.py          # camera / viewport -> IR
    ui.py              # PySide6 render window
    client.py          # worker lifecycle, protocol, film mmap reader
    settings.py        # persisted config
    devreload.py       # module purge/reimport for development
  core/                # plain CPython. No pymxs, no mitsuba.
    ir.py              # dataclasses + JSON round-trip
    registry.py        # decorator-based translator registry
    transform.py       # coordinate conversion, winding, determinant handling
    units.py           # scene scale, photometric conversion
    emit_dict.py       # IR -> mi.load_dict dict
    emit_xml.py        # IR -> Mitsuba XML
    assets.py          # content-hashed asset naming and dedup
    protocol.py        # message schemas shared by client and worker
    film.py            # mmap film header layout, seqlock read/write
  worker/              # worker venv. mitsuba + numpy. No pymxs, no PySide6.
    __main__.py        # protocol loop
    render.py          # progressive accumulation
    selftest.py
  tools/probes/        # read-only scripts the user runs in Max
  tests/
  docs/
```

---

## 4. Intermediate representation

`core/ir.py`. Frozen dataclasses, fully JSON-serialisable, no Max or Mitsuba types.
This is the contract that makes the project testable without either application.

```python
@dataclass(frozen=True)
class Scene:
    meshes: tuple[Mesh, ...]
    materials: tuple[Material, ...]      # referenced by id
    lights: tuple[Light, ...]
    camera: Camera
    environment: Environment | None
    settings: RenderSettings
    scene_scale_to_meters: float          # from Max system units
    warnings: tuple[Warning_, ...]

@dataclass(frozen=True)
class Mesh:
    id: str
    name: str                             # Max node name, for diagnostics
    material_id: str
    positions_path: str                   # relative path to written .ply
    to_world: tuple[float, ...]           # 16 floats, row-major, already Y-up
    flip_normals: bool

@dataclass(frozen=True)
class Material:
    id: str
    name: str
    kind: Literal["principled", "rough_dielectric", "diffuse_placeholder"]
    params: dict[str, ParamValue]         # float | rgb | TextureRef
    normal_map: TextureRef | None
    two_sided: bool                       # always True in v1
    emission: Emission | None

@dataclass(frozen=True)
class TextureRef:
    path: str
    raw: bool                             # True for non-colour data
    uv_scale: tuple[float, float]
    uv_offset: tuple[float, float]
    invert: bool

@dataclass(frozen=True)
class Light:
    id: str
    name: str
    kind: Literal["point", "spot", "directional", "area"]
    to_world: tuple[float, ...]
    radiance_rgb: tuple[float, float, float]   # already radiometric, W-based
    cutoff_angle_deg: float | None             # half-angle, spot only
    beam_width_deg: float | None               # half-angle, spot only
    photometric_source: PhotometricInfo | None # provenance for the conversion

@dataclass(frozen=True)
class Camera:
    to_world: tuple[float, ...]
    fov_deg: float
    fov_axis: Literal["x", "y", "diagonal", "smaller", "larger"]
    near_clip: float
    far_clip: float
    principal_point_offset: tuple[float, float]
    film_width: int
    film_height: int
```

`Scene.to_json()` / `Scene.from_json()` must round-trip exactly. Golden test fixtures are
checked-in `Scene` JSON, which is what lets the entire translation and emission path be
tested with no Max and no GPU.

---

## 5. Coordinate conversion — `core/transform.py`

Max is right-handed, Z-up. Mitsuba imposes no global up-axis but its `envmap`
parameterisation and all convention assume Y-up. Convert exactly once, at the boundary.

Basis change matrix:

```
C = [[1, 0,  0, 0],
     [0, 0,  1, 0],
     [0, -1, 0, 0],
     [0, 0,  0, 1]]        # (x, y, z)_max -> (x, z, -y)_mitsuba
```

`det(C) = +1`, so handedness, triangle winding and normal orientation are preserved.

**Object-space geometry.** If vertices are converted with `C`, the node transform must be
conjugated, not left-multiplied:

```
T_mitsuba = C @ T_max @ inverse(C)
```

Using `C @ T_max` produces a scene that looks correct until an object is both off-origin
and rotated. There must be a unit test asserting that a point transformed via
`C @ (T_max @ p)` equals `(C @ T_max @ inv(C)) @ (C @ p)` for random `T_max` and `p`.

**World-space geometry (v1 default).** Vertices are baked in world space, `to_world` is
`C` alone. Simpler and correct; instancing is what forces the object-space path later.

**Negative determinant.** If `det(T_max[:3,:3]) < 0` the node is mirrored and winding
flips. Set `Mesh.flip_normals = True` and reverse each index triple. Independently, all
opaque BSDFs are wrapped in `twosided` — do both, they fix different failure modes.

**Camera chirality.** Mitsuba's `look_at` builds its basis as
`dir = normalize(target - origin)`, `left = normalize(cross(up, dir))`,
`new_up = cross(dir, left)`, with matrix columns `[left, new_up, dir]`. The first basis
vector points to the camera's **left**. Copying Max's camera basis columns directly
yields a horizontally mirrored image. Prefer emitting a `look_at` (origin, target, up)
rather than a raw matrix, and verify with the chirality golden scene.

---

## 6. Units — `core/units.py`

Max internal coordinates are in system units. **Confirmed by probe 01b** on a scene with
`SystemScale = 1.0`, `SystemType = centimeters`: `rt.units.getMasterScale` does not exist,
but `rt.units.decodeValue("1.0m")` returns `100.0`.

Use that, not a name-to-metres lookup table:

```python
scene_scale_to_meters = 1.0 / rt.units.decodeValue("1.0m")
```

`decodeValue` parses a string carrying an explicit unit, so this is correct for
centimetres, inches, feet or any custom system unit, and is independent of `DisplayType`.
A name-based mapping over `SystemType` would need updating for every unit Max supports and
would silently break on generic units. Assert `0 < scene_scale_to_meters < 1e6` and fail
loudly otherwise.

Multiply all positions, translations, focus distances and medium extinction lengths by
this factor at export.

This is not cosmetic. Irradiance from a point emitter goes as `E = I / r²`, so a scene
authored in centimetres rather than metres is wrong by a factor of 10⁴. Record the factor
in `Scene` and display it in the UI.

**Photometric to radiometric.** Max photometric lights carry luminous intensity `I_v` in
candela. Mitsuba's `point` emitter wants radiant intensity `I_e` in W/sr. Exactly:

```
I_v = K_m * ∫ V(λ) I_e,λ(λ) dλ,     K_m = 683 lm/W
```

An RGB variant cannot evaluate the integral, so use the luminous efficacy of radiation
`η = 683 * ∫ V(λ) s(λ) dλ` for the light's normalised SPD, and set `I_e = I_v / η`.
Typical white sources fall in 200–350 lm/W. Expose `η` as a user setting named
"luminous efficacy (lm/W)" with a default of 250 and a tooltip stating it is an
approximation of the spectral integral.

Distribute across RGB while preserving luminance: for light colour `c` with
`Y = 0.2126 R + 0.7152 G + 0.0722 B`, emit `I_e * c / Y`. Guard `Y > 1e-6`.

Related identities the implementation should use and document:
- Isotropic point emitter: `Φ = 4π I`.
- Lambertian area emitter of flux `Φ` over area `A`: `L = Φ / (π A)`.

**Spot cones.** Max stores hotspot and falloff as **full** cone angles; Mitsuba's `spot`
takes `cutoff_angle` and `beam_width` as **half** angles. So `cutoff_angle = falloff / 2`
and `beam_width = hotspot / 2`. **`[PROBE 03]`** confirm Max's property names and that
they are full angles. The falloff profiles differ (Mitsuba uses a smooth cubic in the
cosine), so the penumbra will not match exactly — document this as approximate.

---

## 7. Camera

**Confirmed by probe 01b:** the camera class is `Physical`, not a legacy Free/Target
camera. `rt.getRendApertureWidth()` returns 36.0 but that is the *render* aperture used by
legacy cameras — for a Physical camera the authoritative value is `film_width_mm`.
Render output is 1280x720, pixel aspect 1.0.

### 7.1 Field of view

```
fov_x = 2 * atan(film_width_mm / (2 * focal_length_mm))
```

`specify_fov` is a boolean; when true, the `fov` property overrides the focal length.
**`[PROBE 04b]`** confirm numerically whether `fov` is the horizontal angle by printing
`film_width_mm`, `focal_length_mm`, `specify_fov` and `fov` together and checking the
identity above. Do not assume.

Emit `fov_axis = "x"`. Do not recompute through the aspect ratio unless you also replicate
Max's pixel aspect handling. `zoom_factor` also exists — **`[PROBE 04c]`** determine
whether it multiplies the effective focal length.

### 7.2 Depth of field

The Physical camera exposes `use_dof`, `f_number` and `focus_distance`, which map onto
Mitsuba's `thinlens` sensor. Physical aperture diameter is `D = f / N`, so:

```
aperture_radius_m = (focal_length_mm / (2 * f_number)) / 1000
focus_distance_m  = focus_distance * scene_scale_to_meters
```

Emit `thinlens` when `use_dof` is true, `perspective` otherwise. `bokeh_blades_number`,
`bokeh_shape`, `bokeh_anisotropy` and the other bokeh controls have no Mitsuba equivalent
(the aperture is a disc) — warn if any is non-default.

### 7.3 Shift, tilt, distortion

`horizontal_shift` and `vertical_shift` map onto Mitsuba's `principal_point_offset_x` and
`principal_point_offset_y`. **`[PROBE 05]`** the units and sign are unknown; calibrate by
setting a known shift, rendering a centred grid, and measuring the displacement in pixels.
Mitsuba's offsets are in normalised film coordinates, Max's are probably millimetres.

`horizontal_tilt_correction`, `vertical_tilt_correction` and
`auto_vertical_tilt_correction` are **not** expressible as a principal-point offset — a
tilt is a Scheimpflug-style shear of the image plane, not a translation. Mark unsupported
and warn when non-zero.

`distortion_type`, `distortion_cubic_amount`, `distortion_texture` and
`vignetting_enabled`/`vignetting_amount` are unsupported. Warn.

### 7.4 Exposure

The Physical camera carries a real photographic exposure model: `exposure_gain_type`,
`ISO`, `exposure_value`, `shutter_length_seconds`, and white balance. Mitsuba's film has
no exposure control, and this project tone-maps on the host (SS12), so use these values to
set the **default position of the host exposure slider** rather than baking them into the
render:

```
exposure_scale = (ISO / 100) * 2 ** (-exposure_value) * K
```

`K` is a calibration constant depending on the metering convention; fit it once against a
reference Arnold render of a known-luminance surface and record the value and the fit
procedure in `docs/MATERIAL_MAPPING.md`. Do not invent a value.

White balance is a chromatic adaptation on the output and is out of scope for v1 — warn if
`white_balance_type` is non-default. `motion_blur_enabled` is out of scope; warn if on.

### 7.5 Film and viewport

Film resolution comes from `rt.renderWidth` / `rt.renderHeight` (confirmed present), with
a UI override and a half/quarter resolution toggle for interactive work. `clip_on`,
`clip_near`, `clip_far` map to `near_clip` / `far_clip`; when `clip_on` is false, use
Mitsuba's defaults rather than Max's stale values.

Also support rendering from the active perspective viewport when no camera is selected —
this is what users will actually do most of the time.

Legacy Free and Target cameras are **out of scope for v1**. Detect them by class and emit
a warning naming the node.

## 8. Geometry extraction — `max_side/mesh.py`

### 8.1 Performance
`pymxs` marshals every call through the MAXScript VM. Per-element loops are two orders of
magnitude slower than bulk accessors. Always fetch arrays:

```python
n = rt.getNumVerts(mesh)
allv = rt.execute("#{1..%d}" % n)          # bitarray literal
verts = rt.meshop.getVerts(mesh, allv)
```

**`[PROBE 06]`** confirm the exact signatures and return types of `meshop.getVerts`,
`meshop.getMapVerts`, `getNumFaces`, `getFace`, `getTVFace`, `getFaceSmoothGroup`, and
whether returned arrays are 1-based. Write probe `06_mesh_bulk.py` that prints types,
lengths and the first three elements of each.

Benchmark on a ≥2M-triangle scene as part of M1 and record the number in
`docs/PERFORMANCE.md`. If extraction exceeds ~10 s for 2M triangles, that is the trigger
for revisiting the USD fast path — but do not pre-emptively build it.

### 8.2 Modifier stack
`rt.snapshotAsMesh(node)` returns a fully evaluated `TriMesh` in **world space**. That is
what v1 wants. Note in code comments that this is also what kills instancing, so the M5
path will need `inverse(node.objectTransform)` applied to recover object space.

### 8.3 Normals from smoothing groups
Max stores smoothing groups, not vertex normals. Algorithm:

1. Compute per-face geometric normals.
2. For each vertex, partition incident faces into classes, where two faces are in the same
   class iff their smoothing-group bitmasks share at least one bit. Faces with smoothing
   group 0 are always their own class (hard edge).
3. Within each class, average the face normals weighted by **incident angle at the vertex**
   (or by face area). Unweighted averaging visibly distorts normals on irregular
   tessellation — this is not optional.
4. Emit one output vertex per (vertex, class) pair.

### 8.4 Vertex splitting
Max keeps separate index arrays for positions (`getFace`) and UVs (`getTVFace`). PLY and
Mitsuba need a single unified index buffer. Build a dict keyed on
`(vert_idx, tvert_idx, normal_class)` mapping to a new index.

### 8.5 Material IDs
**Mitsuba has no per-face material.** Even though Multi/Sub-Object is out of scope for
v1, faces still carry material IDs. Group faces by material ID and emit one `Mesh` per
group from the start — retrofitting this means rewriting the mesh writer. In v1 all
groups of a node resolve to the same `Material`, but the plumbing must exist.

### 8.6 Writing
Binary little-endian PLY with `x y z nx ny nz s t` and `uchar int` face lists. Files are
named by content hash (`meshes/<sha1-16>.ply`) so re-exports do not rewrite unchanged
geometry. Same for textures. `core/assets.py` owns the hashing and the manifest.

---

## 9. Materials — `max_side/materials.py` + `core/emit_dict.py`

v1 supports `PhysicalMaterial` with `Bitmaptexture` inputs only. Everything else gets a
50% gray `diffuse` placeholder plus a warning naming the node and the material class.

**`[PROBE 07]`** dump the full property list of a PhysicalMaterial with
`rt.getPropNames(mat)` and print every value and type. Do not write the mapping table
from memory; write it from the probe output.

Target structure, outermost first:

```
twosided { normalmap { principled { ... } } }
```

**`[PROBE 08]`** verify this nesting order actually applies the normal map — it is easy to
produce a silently ignored normal map, and the order is worth confirming empirically with
a strongly-normal-mapped test sphere.

Mapping, to be recorded in `docs/MATERIAL_MAPPING.md` with an exact / approximate /
unsupported classification per row:

| Max | Mitsuba `principled` | Notes |
|---|---|---|
| `base_color` × `base_weight` | `base_color` | exact |
| `roughness` | `roughness` | **check `roughness_inv`** — Max may store glossiness |
| `metalness` | `metallic` | exact |
| `reflectivity` | `specular` | approximate; `specular = 0.5` ⇒ η = 1.5 ⇒ F₀ = 0.04 |
| `anisotropy`, `aniso_angle` | `anisotropic` | approximate — see below |
| `coating`, `coat_roughness` | `clearcoat`, `clearcoat_gloss` | approximate; `clearcoat_gloss ≈ 1 − coat_roughness` |
| `transparency`, `trans_color`, `trans_depth` | **not** `spec_trans` | see below |
| `emission`, `emit_color`, `emit_luminance` | area emitter on the shape | cd/m² → W/(sr·m²) via `units.py` |
| `scattering`, `sss_*` | unsupported | warn |
| `thin_film_*` | unsupported | warn |

`specular` and `eta` are mutually exclusive in Mitsuba — pick `specular` and never emit
both.

**Anisotropy.** Disney's parameterisation is
`aspect = sqrt(1 − 0.9 a)`, `α_x = α² / aspect`, `α_y = α² · aspect`.
Max's parameterisation differs; calibrate against a probe render rather than trusting a
closed-form conversion, and mark the row approximate until calibrated.

**Transmission.** `principled`'s `spec_trans` cannot express Max's `trans_depth`, which is
a Beer–Lambert distance. When `transparency > 0` and the material is not thin-walled,
emit `roughdielectric` with an interior `homogeneous` medium instead, using per-channel

```
σ_t = −ln(trans_color) / trans_depth
```

and albedo 0 (pure absorption). Guard against `trans_color` channels at 0 or 1. Note that
`σ_t` is per unit length and must be expressed in the same units as the geometry — apply
`scene_scale_to_meters`.

**Textures.** Emit `bitmap` textures with `raw = True` for all non-colour data (roughness,
metalness, bump, normal, anisotropy). Getting this wrong applies an sRGB decode and makes
roughness wrong everywhere with no obvious visual tell. **`[PROBE 09]`** read the Max
bitmap's gamma override to decide, and confirm the property name. Also confirm the V
direction: a V-flip is frequently needed and the UV checker golden scene must catch it.

---

## 10. Worker protocol — `core/protocol.py`

Transport: newline-delimited JSON over the worker's stdin/stdout. Pixels never travel
over the pipe.

Launch:

```python
proc = subprocess.Popen(
    [venv_python, "-u", "-m", "worker"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8",
    creationflags=subprocess.CREATE_NO_WINDOW,
)
```

Host → worker:
- `{"cmd": "hello", "protocol": 1}`
- `{"cmd": "render", "job": <int>, "scene": <dict>, "film": {"w":.., "h":..}, "shm": "<path>", "spp_per_pass": 16, "passes": 32, "seed": 0, "integrator": {...}}`
- `{"cmd": "cancel", "job": <int>}`
- `{"cmd": "shutdown"}`

Worker → host:
- `{"ev": "ready", "protocol": 1, "mitsuba": "3.x.y", "variant": "cuda_ad_rgb", "python": "..."}`
- `{"ev": "pass", "job": .., "index": k, "spp_done": .., "elapsed_s": ..}`
- `{"ev": "done", "job": .., "spp_done": .., "elapsed_s": ..}`
- `{"ev": "error", "job": .., "message": "...", "traceback": "..."}`
- `{"ev": "log", "level": "warn", "message": "..."}`

**Cancellation.** `mi.render()` cannot be interrupted mid-call. Cancellation is therefore
implemented by splitting the sample budget into passes and checking for a pending `cancel`
between them. With 32 passes the worst-case cancel latency is one pass, not the whole
render. **State this limitation in the README** so it is not filed as a bug.

Progressive accumulation uses a running mean with a distinct seed per pass:

```
L̂_k = L̂_{k−1} + (L_k − L̂_{k−1}) / k
```

---

## 11. Film buffer — `core/film.py`

A file of `64 + W*H*4*4` bytes, `mmap`ed by both processes. 64-byte header:

| offset | type | field |
|---|---|---|
| 0 | 8 bytes | magic `MMXFILM\0` |
| 8 | uint32 | version |
| 12 | uint32 | seq |
| 16 | uint32 | width |
| 20 | uint32 | height |
| 24 | uint32 | channels (4) |
| 28 | uint32 | passes_done |
| 32 | uint32 | spp_done |
| 36 | uint32 | state (0 idle, 1 rendering, 2 done, 3 error) |
| 40–63 | — | reserved, zeroed |

Pixels are float32 RGBA in linear space, row-major from offset 64.

Concurrency uses a **seqlock**, not a mutex. Writer: increment `seq` to an odd value,
write pixels, increment to even. Reader: read `seq`; if odd, retry; read pixels; read
`seq` again; if changed, retry. This gives torn-read-free progressive display with no
cross-process locking primitives.

---

## 12. UI — `max_side/ui.py`

Parent a PySide6 window to Max rather than creating a floating orphan:

```python
import shiboken6
from PySide6 import QtWidgets
from pymxs import runtime as rt
max_win = shiboken6.wrapInstance(int(rt.windows.getMAXHWND()), QtWidgets.QWidget)
```

**Confirmed by probe 01b:** PySide6 6.8.3, `shiboken6` importable under that name,
`rt.windows.getMAXHWND()` returns a valid integer handle. `rt.GetQMaxMainWindow()` does
**not** exist and must not be used — it was a MaxPlus-era API. The `wrapInstance` call
itself still needs a live check, which `3dsmaxbatch` cannot provide (no UI in batch mode),
so it stays on the manual checklist.

Poll the worker on a `QTimer` at ~10 Hz. Never block.

Tone-mapping happens on the host from the cached float32 buffer, in numpy:
linear → exposure multiply → sRGB transfer function → uint8 →
`QImage(..., QImage.Format_RGB888)`. Exposure and gamma are live controls that re-tonemap
the **cached** buffer without re-rendering. This single feature does more for perceived
quality than any renderer optimisation, so it is in v1, not deferred.

Window contents: image view with zoom/pan, progress bar with pass counter and elapsed
time, variant and scene-scale readouts, exposure and gamma sliders, Cancel, Save EXR,
Save PNG, Save scene.xml, and a collapsible warnings panel listing every substitution the
exporter made.

---

## 13. Milestones

### M0 — vertical slice
One teapot, one PhysicalMaterial, one omni light, one target camera. Extract → IR →
dict → PLY → worker renders → image in the Qt window. No registry, no generality.
**Done when:** the user pastes a screenshot of a rendered teapot inside Max.

### M1 — correctness harness
Four golden scenes, driven from checked-in IR JSON fixtures, no Max required:

1. **Chirality** — asymmetric glyph plus a numbered UV checker. Catches the `look_at`
   mirroring and the UV V-flip simultaneously.
2. **White furnace** — `constant` environment of radiance 1, white Lambertian sphere. A
   correct renderer produces exactly 1.0 everywhere and the sphere becomes invisible. Any
   deviation is an energy, unit, or `twosided` bug, reduced to a single number. Assert
   `max|image − 1.0| < 1e-3`.
3. **Transform torture** — nested groups, negative scale on one axis, non-uniform scale,
   off-origin rotation. Verifies the `C T C⁻¹` conjugation and the winding flip.
4. **Cornell box** — hand-built Max replica exported and diffed against `mi.cornell_box()`.

Plus the geometry-extraction benchmark from §8.1.
**Done when:** all four pass in CI with numeric tolerances, not eyeballs.

### M2 — registry and material coverage
Decorator-based registry (`@material`, `@light`, `@camera`) dispatching on Max class.
Full PhysicalMaterial coverage per §9, bitmap textures with correct `raw` flags,
`twosided` / `normalmap` wrapping, placeholder-plus-warning for everything else.
**Done when:** `docs/MATERIAL_MAPPING.md` classifies every row and the anisotropy and
roughness rows are backed by calibration renders.

### M3 — lights and camera
Photometric conversion with user-exposed efficacy, spot half-angle conversion, HDRI
environment, lens shift, clipping planes, viewport-camera rendering.
**Done when:** a photometric light of known candela produces the expected illuminance on a
reference plane, checked numerically.

### M4 — production usability
Cancel, progressive passes, content-hashed asset dedup, scene.xml export, variant
selection UI, settings rollout for integrator / spp / max depth, the environment-setup
wizard from §2.2, and the warnings panel.
**Done when:** a cold-start user with no Python environment can go from install to first
render guided only by the UI.

### M5 — instancing
Object-space geometry, shapegroup caching keyed on base-object handle
(`rt.getHandleByAnim(node.baseObject)` **`[PROBE 11]`**), `C T C⁻¹` transform path
activated. Constraint to encode: **Mitsuba instances cannot carry emitters**, so an
instanced emissive object must fall back to unique meshes with a warning.

### Post-v1, in priority order
Standard material and Multi/Sub-Object; Arnold Standard Surface; a `SceneSource`
implementation backed by USD export for large-scene geometry throughput; render elements;
ActiveShade.

---

## 14. Calibration task (M2)

Build a grid of spheres sweeping roughness × metalness, render in Arnold and in Mitsuba,
and fit a remap `α_mitsuba = f(r_max)`. Mitsuba's `principled` is a Disney-style BSDF and
Max's PhysicalMaterial is an Autodesk Standard Surface derivative — they will never match
exactly, and roughness in particular is parameterised differently. The fitted curve is a
genuinely valuable artifact; check it in as data plus the script that produced it, and
document the residual error.

---

## 15. Error handling policy

- Worker crash: surface the exit code and the tail of stderr in a copyable text box. The
  out-of-process design exists precisely so a Dr.Jit segfault does not take the user's
  unsaved scene with it — make that benefit visible.
- Unsupported scene feature: warn with node name and reason, substitute a documented
  placeholder, continue. Never abort the whole export for one bad node.
- Environment failure: show the real subprocess stderr, never a sanitised message.
- Protocol violation or version mismatch: refuse to render, state both versions.

## 16. Probe index

Maintain this table in `docs/PROBE_RESULTS.md`, appending the confirmed answer, the date,
and the Max build for each.

| # | Topic | Blocks |
|---|---|---|
| 01 | Python version, executable, `maxVersion()` | **RESOLVED** — 3.13.9, Max 2027.2 |
| 02 | scene scale to metres | **RESOLVED** — `1/decodeValue("1.0m")` |
| 03 | Spot hotspot/falloff property names, full vs half angle | §6 |
| 04 | camera aperture | **RESOLVED** — Physical camera, use `film_width_mm` |
| 04b | is `fov` the horizontal angle? | §7.1 |
| 04c | `zoom_factor` semantics | §7.1 |
| 05 | Lens shift property names, sign, units | §7 |
| 06 | `meshop` bulk accessor signatures and 1-basedness | §8 |
| 07 | Full PhysicalMaterial property dump | §9 |
| 08 | `twosided`/`normalmap` nesting order | §9 |
| 09 | Bitmap gamma override property; UV V direction | §9 |
| 10 | Qt bridge | **RESOLVED** — PySide6 6.8.3, `getMAXHWND` ok, no `GetQMaxMainWindow` |
| 11 | `getHandleByAnim` on base objects | M5 |
| 12 | venv creation from Max's `python.exe` | §2.2 |
| 13 | `3dsmaxbatch.exe` present, licence behaviour | agent autonomy |
