# Probe results

Confirmed behaviour of the host application, established by running read-only scripts in a
real 3ds Max session. Anything recorded here is fact and must be implemented against
directly — no `getattr` fallback chains, no defensive branches for cases ruled out below.

Anything **not** recorded here is still an assumption. See `SPEC.md` §16 for the index.

Host for every entry below unless stated otherwise: 3ds Max 2027.2 (build 29000),
2026-07-30, run headless via `tools/maxbatch.py`.

---

## Probe 01 — interpreter and version — RESOLVED

Host: 3ds Max 2027, run in the Python listener.

```
python:     3.13.9 (tags/v3.13.9-dirty:8183fa5e3f7, Jun  3 2026, 01:18:47)
            [MSC v.1938 64 bit (AMD64)]
exe:        C:\Program Files\Autodesk\3ds Max 2027\Python\python.exe
maxVersion: #(29000, 70, 0, 29, 2, 0, 20588, 2027, ".2")
```

**Conclusions**

- Syntax floor is CPython 3.13. Max 2026 shipped 3.9; 2027 jumped four versions. Do not
  write 3.9 compatibility shims.
- MSVC v1938 corresponds to VS 2022, consistent with the documented 2025+ toolset.
- `maxVersion()` returns a 9-element array. Index 7 is the release year (2027) and index 3
  is the internal version (29). Use index 7 for user-facing version checks; do not parse
  the string at index 8.
- **`rt.maxVersion()` is not sliceable.** `rt.maxVersion()[:8]` raises
  `IndexError: Error getting MAXScript value`. Convert with `list(...)` first.
- Because `sys.executable` is a real `python.exe` rather than `3dsmax.exe`, it is a
  candidate base interpreter for the worker venv (probe 12, now resolved).

**Downstream compatibility check (verified against PyPI):** Mitsuba 3.9.0 publishes
`cp313-cp313-win_amd64` wheels, and Dr.Jit's build matrix covers cp39 through cp314.

---

## Probe 01b — units, camera, Qt — RESOLVED

Scene had `SystemScale = 1.0`, `SystemType = centimeters`.

```
units.SystemScale:        1.0
units.SystemType:         <Name<centimeters>>
getMasterScale(#meters):  FAILED  AttributeError (no such attribute)
decodeValue "1.0m":       100.0
getRendApertureWidth:     36.0
renderWidth/Height:       1280 / 720
renderPixelAspect:        1.0
PySide6 / shiboken6:      ('6.8.3', 'shiboken6')
windows.getMAXHWND:       133724
GetQMaxMainWindow:        FAILED  AttributeError (no such attribute)
active camera classOf:    'Physical'
```

**Conclusions — units (resolves probe 02)**

- `rt.units.getMasterScale` does **not** exist. Do not call it.
- `scene_scale_to_meters = 1.0 / rt.units.decodeValue("1.0m")` — here `0.01`.
- Unit-name-agnostic; works for inches, feet and generic units. Do **not** build a
  `SystemType`-name lookup table.
- `SystemScale` alone is meaningless without `SystemType` — 1.0 here means one centimetre.

**Conclusions — camera (resolves probe 04)**

- The camera is a **Physical camera**, not a legacy Free/Target camera. v1 targets Physical
  only; warn on anything else.
- `getRendApertureWidth()` returns 36.0 but is the *render* aperture for legacy cameras.
  For a Physical camera use the per-camera `film_width_mm`.

**Conclusions — Qt (resolves probe 10)**

- PySide6 **6.8.3**, `shiboken6` importable under that name.
- `rt.GetQMaxMainWindow()` does **not** exist — MaxPlus-era API. That is not the same as
  `qtmax.GetQMaxMainWindow()`, which *does* exist in Max's `site-packages/qtmax`.
- **Do not** `wrapInstance(int(hwnd), QWidget)`. An `HWND` is not a `QWidget*`; that
  native-crashes Max as "Unknown exception thrown executing script" with no traceback.
  Autodesk's own helper does `QWidget.find(hwnd)` then
  `wrapInstance(getCppPointer(...)[0], QMainWindow)`. `max_side.ui.max_main_window`
  inlines that — do not `import qtmax` to call it: loading `max_side.ui` from the render
  macroscript left `qtmax` partially initialised (`GetQMaxMainWindow` missing).
- Parenting cannot be verified headlessly and stays on the manual checklist (M0-4).

---

## Probe 13 — `3dsmaxbatch.exe` — RESOLVED

`C:\Program Files\Autodesk\3ds Max 2027\3dsmaxbatch.exe` exists and runs a `.py` file with
`pymxs` available. It ran successfully while the user's Max UI was **not** open; the
concurrent-UI case was not exercised and remains untested.

```
sys.version:        3.13.9 ... [MSC v.1938 64 bit (AMD64)]
sys.executable:     C:\Program Files\Autodesk\3ds Max 2027\Python\python.exe
windows.getMAXHWND: 528514        <- non-zero even in batch
objects count:      0
```

**Conclusions**

- Printed output reaches the listener log only with `-listenerLog`. `tools/maxbatch.py`
  always passes it. Never invoke `3dsmaxbatch.exe` bare.
- **The exit code is not a reliable success signal.** A clean run that executed the whole
  script and printed everything returned `0xFFFFFF7E` (-130). `tools/maxbatch.py` therefore
  keys off a `PROBE_COMPLETE` marker line plus the absence of a traceback in the log, and
  reports the exit code as information only. Every probe must print `PROBE_COMPLETE` last.
- **`3dsmaxbatch` inherits `VIRTUAL_ENV` and mis-reports `sys.prefix`.** Launched from an
  activated virtualenv, `sys.prefix` came back as that virtualenv while `sys.executable`
  was still Max's own `python.exe`. `tools/maxbatch.py` now scrubs `VIRTUAL_ENV`,
  `PYTHONHOME`, `PYTHONPATH`, `PYTHONSTARTUP` and `CONDA_PREFIX` from the child
  environment, so probes see what a Start-menu launch would see.
- `getMAXHWND()` returns a valid handle in batch mode, so a hidden main window exists.
  This does **not** mean Qt work can be verified headlessly — nothing is shown — but it
  does mean the handle-fetch itself is not the risky part of the UI bring-up.

---

## Probe 06 / 06b / 06c — mesh extraction — RESOLVED

### What exists

| call | result |
|---|---|
| `rt.snapshotAsMesh(node)` | returns a `TriMesh`, vertices in **world space** (a teapot at `[100,20,5]` had vertex 1 at `[106.06, 23.5, 17.0]`) |
| indices | **1-based**. `getVert(mesh, 0)` and `getVert(mesh, nv+1)` both raise `Mesh vertex index out of range` |
| `rt.meshop.getVerts(mesh, bitarray)` | works; returns an `MXSWrapperBase` array of `Point3`, `len()` equal to the bit count |
| `rt.meshop.getMapVerts` / `getMapFaces` | **do not exist** (`AttributeError`) |
| `rt.meshop.getMapVert(mesh, ch, i)` / `getMapFace(mesh, ch, i)` | exist, per-element |
| `rt.meshop.getMapSupport / getNumMapVerts / getNumMapFaces` | exist |
| `rt.getTVert` / `rt.getTVFace` | exist; raise `Mesh has no TVFaces` on an unmapped mesh |
| `rt.getFace`, `getFaceSmoothGroup`, `getFaceMatID`, `getFaceNormal` | exist, per-face |
| `rt.getNumCPVVerts`, `rt.meshop.getNumMaps`, `rt.meshop.getFaceArea` | exist |
| `rt.getRow` | **does not exist**. Use `matrix.row1 … row4` |

`rt.Teapot()` defaults to `mapCoords = false`, so `getNumTVerts` is 0 while
`meshop.getNumMaps` is 2. Do not read that as "the API is broken"; build probe geometry
with `mapCoords:true`.

### Material IDs and smoothing groups are real on primitives

A default `Box` reports `getFaceMatID(1..6) = [2, 2, 1, 1, 5, 5]` and
`getFaceSmoothGroup(1..6) = [2, 2, 4, 4, 8, 8]`, and `getFace(1) != getTVFace(1)`.
So all three of per-material splitting, smoothing-group normals and vertex splitting are
exercised by the simplest possible test object. SPEC §8.5 is not a hypothetical.

### Throughput — 1.00 M triangles (`Sphere segments:1000`, 499 002 verts, 998 000 faces)

| approach | time | rate |
|---|---|---|
| `meshop.getVerts` bulk + per-component attribute access | 1.31 s | 0.38 M verts/s |
| MAXScript loop → binary file → `struct.unpack` (verts) | 1.39 s | 0.36 M verts/s |
| MAXScript loop → binary file (tverts) | 1.35 s | 0.37 M tverts/s |
| MAXScript loop → binary file (faces + tvfaces + sg + matid) | **8.23 s** | 0.12 M faces/s |

The binary-file route was verified to agree with `getVert` exactly at indices 1, 2, n/2 and
n. `fopen(path, "wb")` truncates.

**Conclusions**

- Bulk vertex fetch and the MAXScript-to-file route are equivalent for positions; **faces
  are the bottleneck**, at five MAXScript calls per face.
- Extrapolating, 2 M triangles costs roughly **20 s** end to end, against the ~10 s target
  in SPEC §8.1. Recorded in `docs/PERFORMANCE.md` rather than fixed speculatively — the
  USD fast path is the documented remedy and should not be built pre-emptively.
- MAXScript source must be assembled with **newlines**, not concatenated. MAXScript has no
  statement terminator, so `local n = getNumVerts m local out = #()` on one line is a parse
  error that reports itself somewhere unrelated. This cost probe 06b three of its four
  measurements.

### numpy is NOT available inside 3ds Max — RESOLVED, and it changes the design

```
import numpy  ->  ModuleNotFoundError: No module named 'numpy'
```

`C:\Program Files\Autodesk\3ds Max 2027\Python\Lib\site-packages` contains exactly
`PySide6`, `pymxs`, `qtmax`, `shiboken6` and their dist-info directories. `sys.path` holds
no user site directory.

**Conclusions**

- The import rules in `core/CLAUDE.md` and `max_side/CLAUDE.md` list numpy as allowed. It
  is not present, so any module on the Max-side import path that uses it cannot load.
- `core.transform` was therefore rewritten **stdlib-only** (16 floats do not need a linear
  algebra library), and `core.meshbuild` is stdlib-only by construction. The whole
  extract → IR → emit path runs with the standard library alone.
- `core.film`'s pixel handling and the UI's tone mapping genuinely do need numpy — a
  per-pixel Python loop over a megapixel float buffer is not a repaint budget. It is
  obtained through `max_side.numpy_bridge`, which serves numpy out of the worker venv's
  site-packages. Because that venv is created from Max's own `python.exe` (probe 12), the
  ABI matches exactly, and the bridge exposes **only** `numpy` — `mitsuba` and `drjit` are
  hard-blocked, so hard invariant 1 is enforced mechanically rather than by convention.

---

## Probe 11 — instancing keys — RESOLVED

```
getHandleByAnim(node):             2787
getHandleByAnim(node.baseObject):  2785
classOf(node.baseObject):          'Sphere'
instance shares baseObject handle: True
copy shares baseObject handle:     False
```

**Conclusion.** `rt.getHandleByAnim(node.baseObject)` is a valid shapegroup cache key for
M5: true instances share it, copies do not.

---

## Probe 03 / 03b — lights — PARTIALLY RESOLVED

### Classes present

`Omnilight`, `freeSpot`, `targetSpot`, `Directionallight`, `Free_Light`, `Target_Light`.

`rt.targetSpot(target=rt.Point3(...))` and `rt.Target_Light(target=rt.Point3(...))` both
fail with `Unable to convert: [0,0,0] to type: <node>`. **`target:` wants a node**, e.g.
`rt.Point(pos=...)`. Passing a Point3 is a silent-looking failure at construction time.

### Standard lights

`Omnilight` exposes `multiplier` (1.0), `rgb`, `on`, `castShadows`, attenuation. `rgb` is a
**0–255 colour**, not 0–1: setting `rt.color(255,128,0)` reads back `[255.0, 128.0, 0.0]`.

`freeSpot` / `targetSpot` / `Directionallight` expose `hotspot` (43.0) and `falloff` (45.0).

### Photometric lights (`Free_Light`, `Target_Light`)

Defaults: `intensity = 1500.0`, `intensityType = 1`, `distribution = 0`, `kelvin = 3600`,
`useKelvin = False`, `hotspot = 30`, `falloff = 60`, `light_Radius = 13`,
`light_Width = 61`, `light_length = 122`, `useMultiplier = False`, `multiplier = 100`.

Properties that do **not** exist and must not be reached for: `targeted`, `shape`,
`shapeType`, `areaLightLength`, `areaLightWidth`, `areaLightRadius`, `coneAngle`,
`spotlightConeAngle`, `resultingIntensity`, `dimmerValue`.

**`intensityType` and `distribution` are plain integers**, and their enum mappings are
**still unresolved**:

- Setting integers 0–4 echoes the value back unchanged; no observable side effect on
  `intensity` or `flux` (`flux` stayed 0.0 throughout).
- Setting them from a `Name` (`#cd`, `#isotropic`, …) does **not** work: every name maps to
  the same integer (4 for `intensityType`, 3 for `distribution`), including nonsense names.
  So `rt.name(...)` assignment is not a valid way to set these and must not be used.

**Consequence for the implementation.** Only the default `intensityType = 1` is treated as
known (candela, matching Max's documented default photometric light of 1500 cd) and only
`distribution = 0` as known (isotropic). Any other value produces a `Warning_` naming the
node and stating that the unit or distribution could not be identified, and the light is
converted as if it were the default. This is **probe 03c, still open** — resolving it needs
either the SDK enum headers or a render-based calibration.

### On/off is `on`, not `enabled` — RESOLVED (probe 03d)

`Free_Light` / `Target_Light` expose both `on` and `enabled`. Defaults on a freshly created
photometric light:

```
on:       True
enabled:  False
```

`enabled` is therefore **not** the power switch. An earlier `translate_light` guard that
skipped when `not node.enabled` silently dropped every photometric light, producing
`EmitError: scene has no lights…` on a scene that clearly had them. Standard lights
(`Omnilight`) happen to default `enabled=True`, which is why the bug only hit the
Create-panel default photometric path.

`translate_light` now keys off `node.on` alone. `Sun_Positioner` (also `superClassOf light`,
no `on` attribute, `enabled=False`) remains unsupported in v1 and is skipped with a
warning naming the class.

### Photometric shape classes — RESOLVED (probe 03g)

Max 2027's Create panel does not stick to `Free_Light` / `Target_Light`. Choosing an
emitter shape switches `classOf` to a dedicated class that still carries the same
photometric properties (`intensity`, `intensityType`, `distribution`, `rgbFilter`, …):

| Shape | Free class | Target class |
|-------|------------|--------------|
| Point | `Free_Light` (`rt.Free_Point` constructs as this) | `Target_Light` |
| Sphere | `Free_Sphere` | `Target_Sphere` |
| Disc | `Free_Disc` | `Target_Disc` |
| Area (rectangle) | `Free_Area` | `Target_Area` |
| Cylinder | `Free_Cylinder` | `Target_Cylinder` |

`max_side.lights.translate_photometric` registers all ten. v1 approximates non-point shapes
as point/spot emitters and warns that `light_Width` / `light_length` / `light_Radius` were
ignored. Light targets (`Targetobject`) are excluded from the light walk.

### Cone angles

Max labels these "Hotspot/Beam" and "Falloff/Field" in degrees and draws the cone
symmetrically about the axis, i.e. they are **full** angles, so
`cutoff_angle = falloff / 2` and `beam_width = hotspot / 2`. Recorded as documented
behaviour; the geometric confirmation needs a render and is manual check **M3-1**.

### Emission axis — RESOLVED and important

A `targetSpot` at `[0, 0, 100]` aimed at the origin reports `transform.row3 = [0, 0, 1]`.
`row3` is the light's local +Z axis, and it points **away** from the target.

**Max lights emit along local −Z.** Mitsuba's `spot` and `directional` emit along local
**+Z**. `max_side.lights` negates the Z axis exactly once when building the IR matrix.

---

## Probe 04b / 04c / 01d — camera — RESOLVED

### `fov` is the horizontal angle, but only with lens breathing off

With `lens_breathing_amount = 0` the identity `fov = 2·atan(w / 2f)` holds **exactly**:

```
f= 18.0  fov=90.00000  predicted=90.00000  delta=+0.00000
f= 35.0  fov=54.43222  predicted=54.43222  delta=+0.00000
f= 50.0  fov=39.59776  predicted=39.59775  delta=+0.00001
f=200.0  fov=10.28553  predicted=10.28553  delta=+0.00000
```

With the **default** `lens_breathing_amount = 1.0`, `fov` is smaller, by 0.21° at 18 mm
rising to 0.41° at 200 mm, and the implied focal length grows with the nominal one
(18 → 18.065, 200 → 208.33). Sweeping `focus_distance` at 50 mm confirms the mechanism:

```
focus_distance=      50  fov=35.90  implied_focal=55.556
focus_distance=     200  fov=38.68  implied_focal=51.282
focus_distance=  100000  fov=39.60  implied_focal=50.003
```

`zoom_factor` divides the tangent of the half-angle exactly:
`fov_eff = 2·atan(tan(fov₁/2) / zoom)`, agreeing to 1e-5 at zoom 0.5, 1, 2 and 4, and it
leaves `focal_length_mm` untouched.

**Conclusion — this resolves 04b and 04c and simplifies the code.** Read `cam.fov`
directly and emit `fov_axis = "x"`. It already accounts for lens breathing, focus distance
and zoom factor. Do **not** recompute from `film_width_mm` and `focal_length_mm`; that
reproduces the *nominal* lens, not the one Max is actually rendering with, and is wrong by
up to half a degree on the default settings.

`film_preset` is `'35mm'` and `film_width_mm` is 36.0.

### Camera basis — RESOLVED

A Physical camera at `[0, -100, 0]` aimed at the origin:

```
transform.row1 = [1, 0, 0]     local X
transform.row2 = [0, 0, 1]     local Y
transform.row3 = [0, -1, 0]    local Z
transform.row4 = [0, -100, 0]  position
```

The target is at `+Y` from the camera and `row3` is `−Y`, so **a Max camera looks down its
local −Z**, and `rowN` are the local axes expressed in world space (rows of a row-vector
matrix, i.e. the *columns* of the column-vector matrix `core.transform` uses — see
`transform.from_axes`).

### Other Physical camera state (defaults)

```
exposure_gain_type 1     ISO 6000       exposure_value 6.0
shutter_length_seconds 0.001            white_balance_type 0    white_balance_kelvin 6500
motion_blur_enabled False               vignetting_enabled False   vignetting_amount 1.0
bokeh_shape 0            bokeh_blades_number 7        bokeh_anisotropy 0.0
distortion_type 0        distortion_cubic_amount 0.0  distortion_texture None
horizontal/vertical_tilt_correction 0.0 auto_vertical_tilt_correction False
clip_on False            clip_near 0.0                clip_far 100000.0
use_dof False            f_number 8.0                 focus_distance 100000.0
specify_focus 0 (an int, not a bool)    targeted True
environment_near 0.0     environment_far 100000.0
```

Properties that do **not** exist: `shutter_type`, `shutter_offset_degrees`,
`bokeh_rotation_degrees`.

`exposure_gain_type` accepts 0–3 and echoes them back; the enum meaning is unresolved and
is not needed, because exposure is applied host-side as a slider default rather than baked
into the render (SPEC §7.4).

### Viewport fallback

`rt.viewport.getType()` → `'view_persp_user'`, `rt.viewport.getTM()` and
`rt.viewport.getFOV()` (45.0) all work with no camera in the scene, and
`rt.getActiveCamera()` returns `None`. `getTM()` is the **world-to-view** matrix and must be
inverted to obtain a camera-to-world transform.

### Probe 05 — lens shift — STILL OPEN

`horizontal_shift` accepts and returns 0.0, 1.0 and 18.0 unchanged with
`film_width_mm = 36`. Nothing in the property values distinguishes millimetres from film
fractions, and the sign convention is unknown. This needs a render of a centred grid with a
known shift, measured in pixels: **manual check M3-2**. Until then the exporter emits the
`mm / film_width` ratio and warns when any shift is non-zero.

---

## Probe 07 — PhysicalMaterial — RESOLVED

120 properties. The full dump is in the probe output; the parts that drive the mapping:

```
base_weight 1.0            base_color (color 127.5 127.5 127.5)
reflectivity 1.0           refl_color (color 255 255 255)
roughness 0.0              roughness_inv False
metalness 0.0              diff_roughness 0.0
anisotropy 0.0             anisoangle 0.25          aniso_mode 0     aniso_channel 0
transparency 0.0           trans_color (255,255,255) trans_depth 0.0  trans_ior 1.52
trans_roughness 0.0        trans_roughness_inv False trans_roughness_lock True
thin_walled False          dispersion 0.0
emission 1.0               emit_color (color 0 0 0)  emit_luminance 1500.0  emit_kelvin 6500
coating 0.0                coat_color (255,255,255)  coat_roughness 0.0  coat_roughness_inv False
coat_ior 1.52              coat_affect_color 0.5     coat_affect_roughness 0.5
coat_anisotropy 0.0        coat_anisoangle 0.25
sheen 0.0                  sheen_color (255,255,255) sheen_roughness 0.3
scattering 0.0             sss_color, sss_depth 10.0, sss_scale 1.0, sss_scatter_color
thin_film 0.0              thin_film_ior 1.3         thin_film_thickness 555.0
bump_map_amt 0.3           material_mode 2           brdf_mode True
brdf_low 0.05  brdf_high 1.0  brdf_curve 5.0
EffectiveLuminance (color 801.106 …)  -- read-only, derived
```

**Conclusions**

- **`roughness_inv` exists and is real.** When true, `roughness` means *glossiness* and
  must be inverted. Same for `coat_roughness_inv` and `trans_roughness_inv`. The names
  guessed in SPEC §9 (`trans_rough_inv`, `coat_rough_inv`) do **not** exist.
- The property is `anisoangle`, not `aniso_angle`.
- Every map slot follows `<param>_map` plus a `<param>_map_on` boolean, both of which
  default to `None` / `True`. A map is active iff the slot is non-`None` **and** `_map_on`.
- `rt.getNumSubTexmaps(mat)` returns 34 and `rt.getSubTexmapSlotName(mat, i)` gives the UI
  name for each; slots 29 and 30 have empty names. Enumerating slots is more robust than
  hardcoding the 34 property names.
- **Max colours are 0–255 floats, not 0–1.** `mat.base_color` defaults to
  `(127.5, 127.5, 127.5)` — mid grey — and setting `rt.color(255,128,64)` reads back
  `r=255.0 g=128.0 b=64.0`. Divide by 255 exactly once, at the boundary.
- `sheen`, `sheen_color` and `sheen_roughness` exist, so `principled`'s `sheen` /
  `sheen_tint` have a genuine source rather than being left at zero.
- `material_mode` is an int (2) whose enum is unresolved; it selects the UI's
  Standard/Advanced presentation and does not change the stored parameters, so it is not
  read.

---

## Probe 09 — bitmaps — RESOLVED

`Bitmaptexture` has 21 properties. It has **no `gamma`** property — SPEC §9 assumed one.

```
bt.filename        'D:\...\probe09_uv.png'      (lowercase `filename`)
bt.bitmap          <BitMap:...>                  (undefined until a file is loaded)
bt.bitmap.gamma    2.2
bt.coords          <StandardUVGen>
global fileInGamma 2.2      displayGamma 2.2      fileOutGamma 2.2
```

`rt.getPropNames(bt.bitmap)` fails — `BitMap` is not a MAXScript wrapper with introspectable
properties — but `bt.bitmap.gamma` reads fine.

`rt.openBitMap(path, gamma:1.0)` **ignores the gamma argument**: both `gamma:1.0` and
`gamma:2.2` return a bitmap reporting 2.2. The gamma cannot be forced at load.

**Conclusions — the `raw` flag**

`bt.bitmap.gamma` reports the file's gamma, which is 2.2 for an ordinary PNG regardless of
whether it holds albedo or roughness. It therefore **cannot** be the sole signal.
`raw` is decided by the **material slot the texture is plugged into** — roughness,
metalness, bump, normal, anisotropy, displacement and cutout are raw; base colour,
reflectivity colour, emission colour, coating colour, transparency colour, SSS colour and
sheen colour are not — with `bt.bitmap.gamma == 1.0` forcing raw when the user has
explicitly overridden it. `max_side.materials` owns that table.

**UV coordinates**

A default `Box` gives tverts `(0,0) (1,0) (0,1) (1,1)` per face: 0–1 range, V increasing
upward, matching what PLY and Mitsuba expect. No V flip is applied; if one is ever needed
the chirality golden scene will show it.

`coords` (a `StandardUVGen`) carries `U_Offset`, `V_Offset`, `U_Tiling`, `V_Tiling`,
`U_Angle`, `V_Angle`, `W_angle`, `mapChannel` (1), `UVTransform` and real-world-scale flags.

**Pixel access, for the inversion bake**

`rt.openBitMap(path)` then `rt.getPixels(bmp, rt.Point2(0, y), width)` returns a list of
`Color` values, 0–255, with **row 0 at the top**. This is the mechanism a glossiness map
uses to become a roughness map, since Mitsuba has no arithmetic texture node.

---

## Probe 12 — worker venv from Max's `python.exe` — RESOLVED

```
"C:\Program Files\Autodesk\3ds Max 2027\Python\python.exe" -m venv %LOCALAPPDATA%\mitsuba-max\venv
```

```
sys.version      3.13.9 ... [MSC v.1938 64 bit (AMD64)]
sys.prefix       C:\Users\...\AppData\Local\mitsuba-max\venv
sys.base_prefix  C:\Program Files\Autodesk\3ds Max 2027\Python
site-packages    ['...\venv', '...\venv\Lib\site-packages']
pip install mitsuba numpy  ->  mitsuba 3.9.0, numpy 2.5.1
```

There is **no `._pth` and no `sitecustomize.py`** in Max's Python directory, and the venv's
`sys.path` contains no Max directories. Isolation is clean.

`python -m worker.selftest` in this venv renders the Cornell box in 0.52 s on
`cuda_ad_rgb` and passes the white-furnace check (mean |L−1| = 0.0029 at 512 spp).

### Variant availability on this machine

```
mi.variants()      13 variants advertised, including llvm_ad_rgb and cuda_ad_rgb
set_variant("cuda_ad_rgb")  ->  OK
import mitsuba              ->  "jitc_llvm_init(): LLVM API initialization failed .." on stderr
```

**Conclusion.** The LLVM backend is advertised but broken on this machine while CUDA works.
A two-entry `cuda → llvm` fallback chain would leave a CPU-only machine with the same LLVM
problem unable to render at all, so `worker.render.VARIANT_PREFERENCE` is
`cuda_ad_rgb → llvm_ad_rgb → scalar_rgb`. `mi.variants()` advertises what was *compiled*,
not what *works*; only `set_variant` tells the truth.

### Stream hygiene

`import mitsuba` writes the `jitc_llvm_init` message to **stderr**, and a plain
`print("CLEAN")` was verified to be the only thing on stdout. The protocol stream is safe
from Mitsuba itself.

**But Max's own interpreter is not.** With `PYTHONPATH` set to anything other than Max's
Python directory, the interpreter — and any venv created from it — prints

```
PYTHONPATH is not set to ""C:\Program Files\Autodesk\3ds Max 2027\Python"" - overriding setting for the current session
```

**on stdout**, before any user code runs. That would be the first thing the host reads from
the worker.

**Consequences, both implemented:**

1. `max_side.client` scrubs `PYTHONPATH` and `PYTHONHOME` from the worker's environment and
   makes `core` importable via a `.pth` file written into the venv's `site-packages`
   instead. Never launch the worker with `PYTHONPATH` set.
2. The host tolerates and *reports* non-JSON lines received before `ready`, surfacing them
   in the diagnostics box rather than treating them as a protocol violation. This is not
   defensive programming for its own sake — it is a measured behaviour of the exact
   interpreter this project runs on.

---

## Still open

| # | Topic | Why it cannot be closed headlessly |
|---|---|---|
| 03c | `intensityType` / `distribution` integer enums on photometric lights | needs SDK enum values or a calibrated render |
| 05 | lens shift units and sign | needs a render of a known grid, measured in pixels — manual check M3-2 |
| 08 | `twosided` / `normalmap` nesting order | needs a visual comparison of a strongly normal-mapped sphere — manual check M2-1 |
| 13b | does `3dsmaxbatch` run while the user has the Max UI open | needs the user to have Max open |

---

## Template for new entries

```
## Probe NN — topic — RESOLVED | OPEN
Host: 3ds Max <version>, <date>

<pasted output>

**Conclusions**
- <what this means for the implementation>
- <what it rules out>
```
