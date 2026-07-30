# Material, light and camera mapping

Every row is classified **exact**, **approximate** or **unsupported**. A row marked
approximate names what is lost. A row marked unsupported produces a `Warning_` naming the
node — never a silent substitution.

Property names come from probe 07's dump of a default `PhysicalMaterial` (120 properties)
and probes 03/03b/01d for lights and cameras. Where `SPEC.md` guessed a name that does not
exist, the real name is used and the guess is noted.

**Max colours are 0–255 floats, not 0–1.** A default `base_color` reads
`(127.5, 127.5, 127.5)`. The division by 255 happens exactly once, in
`max_side.materials._color`.

---

## PhysicalMaterial → Mitsuba `principled`

Wrapped as `twosided { normalmap { principled } }`. The order is PROBE 08 and is confirmed
by manual check M2-1, not by inspection.

| Max | Mitsuba | Class | Notes |
|---|---|---|---|
| `base_color` × `base_weight` | `base_color` | exact | The product is folded into the constant. A **mapped** `base_color` with `base_weight ≠ 1` warns: a per-texel product needs an arithmetic node Mitsuba does not have. |
| `base_color_map` | `base_color` (bitmap, `raw=False`) | exact | |
| `roughness` | `roughness` | **uncalibrated** | Max's PhysicalMaterial is an Autodesk Standard Surface derivative and Mitsuba's `principled` is Disney-style. The value is passed straight through pending the SPEC §14 fit. Manual check M2-2 produces the data. |
| `roughness_inv` | — | exact | Real, and confirmed present. When set, `roughness` holds **glossiness** and is inverted. A mapped glossiness is baked to an inverted PNG at export, because Mitsuba has no arithmetic texture node. |
| `metalness` | `metallic` | exact | |
| `reflectivity` | `specular` | approximate | `specular = 0.5` corresponds to η = 1.5, i.e. F₀ = 0.04. `specular` and `eta` are mutually exclusive in Mitsuba; only `specular` is ever emitted. |
| `refl_color` | — | unsupported | `principled` has no coloured specular for dielectrics. Not currently warned; see *Known gaps*. |
| `anisotropy` | `anisotropic` | **uncalibrated** | Disney's parameterisation is `aspect = √(1 − 0.9a)`, `α_x = α²/aspect`, `α_y = α²·aspect`. Max's differs and has not been fitted. |
| `anisoangle` | — | unsupported | Warns when non-default. Note the name: `aniso_angle` (as guessed in SPEC §9) does **not** exist. |
| `coating` | `clearcoat` | approximate | |
| `coat_roughness` | `clearcoat_gloss` | approximate | `clearcoat_gloss = 1 − coat_roughness`. `coat_roughness_inv` is honoured. |
| `coat_ior` | — | unsupported | Mitsuba's clearcoat lobe fixes η at 1.5. Warns when non-default. |
| `coat_color`, `coat_affect_*`, `coat_anisotropy` | — | unsupported | |
| `sheen` | `sheen` | approximate | |
| `sheen_color` | `sheen_tint` | approximate | `sheen_tint` blends white↔base colour, so a coloured sheen is approximated by its distance from white. |
| `sheen_roughness` | — | unsupported | |
| `emission`, `emit_color`, `emit_luminance` | area emitter on the shape | approximate | cd/m² → W/(sr·m²) via `core.units.luminance_to_radiance`. Approximate only in the luminous-efficacy term. |
| `emit_kelvin` | — | unsupported | Warns when non-default; `emit_color` is used as authored. |
| `emit_color_map` | — | unsupported | Warns; the constant colour is used. |
| `bump_map` | `normalmap` | approximate | `bump_map_amt` is not applied — Mitsuba's `normalmap` has no strength control. Warns when ≠ 1. |
| `diff_roughness` | — | unsupported | Oren–Nayar has no `principled` equivalent. Warns when non-zero. |
| `scattering`, `sss_*` | — | unsupported | Warns when `scattering ≠ 0`. |
| `thin_film`, `thin_film_*` | — | unsupported | Warns when non-zero. |
| `dispersion` | — | unsupported | Warns on the transmissive path. |
| `displacement_map` | — | unsupported | Warns. |
| `cutout_map` | — | unsupported | Warns; the surface renders fully opaque. |
| `brdf_mode` | — | unsupported | Warns when the legacy mode is selected. |
| `material_mode` | — | n/a | Selects the UI's Standard/Advanced presentation only; it does not change the stored parameters, so it is not read. |

### Transmission → `roughdielectric` + `homogeneous`

`principled`'s `spec_trans` is a single scalar and cannot express `trans_depth`, which is a
Beer–Lambert absorption *distance*. So a material with `transparency > 0` and
`thin_walled` off takes a different structural path entirely.

| Max | Mitsuba | Class | Notes |
|---|---|---|---|
| `transparency > 0` | `roughdielectric` | approximate | A partial `transparency` warns: `roughdielectric` is fully transmissive. |
| `trans_ior` | `int_ior` | exact | |
| `trans_roughness` (+ `trans_roughness_inv`, `trans_roughness_lock`) | `alpha` | approximate | `alpha = roughness²`, clamped to ≥ 1e-4. The lock is honoured. |
| `trans_color`, `trans_depth` | interior `homogeneous`, `sigma_t` | exact given the model | `σ_t = −ln(trans_color)/d`, with `d` converted to metres. Albedo 0 (pure absorption): `trans_depth` carries no scattering information, so inventing an albedo would be a guess. `trans_depth = 0` warns rather than silently producing clear glass. |

A dielectric is **never** wrapped in `twosided`. A transmissive BSDF has to know which side
of the interface a ray is on, and `twosided` destroys exactly that.

---

## Textures

`raw` is decided by the **material slot**, not by the file's gamma. Probe 09 established
why: `bt.bitmap.gamma` reports 2.2 for an ordinary PNG whether it holds albedo or
roughness, so a gamma-only rule decodes every roughness map as sRGB — wrong everywhere,
with no obvious visual tell. An explicit gamma of **1.0** forces raw on top of the slot
rule, because that is the artist saying the file is linear data.

| Slots | `raw` |
|---|---|
| roughness, metalness, bump, coat bump, anisotropy, anisotropy angle, coat roughness, transparency roughness, sheen roughness, displacement, cutout, base weight, reflectivity, transparency, scattering, SSS scale, emission weight, coating weight, sheen weight, thin film, IOR | **True** |
| base colour, reflectivity colour, transparency colour, emission colour, coating colour, SSS colour, sheen colour | False |

`bt.coords.U_Tiling` / `V_Tiling` / `U_Offset` / `V_Offset` become `to_uv`. UV rotation
(`U_Angle`, `V_Angle`, `W_angle`), mirroring, and any map channel other than 1 are
unsupported and warn.

**V is flipped on export.** Max puts V = 0 at the bottom of the image, as every
OpenGL-descended tool does; Mitsuba's `bitmap` samples `t = 0` from the image's **first**
row. Measured by the chirality golden scene, not assumed — see `core/meshbuild.py`.

---

## Geometry

| Max | Mitsuba | Class | Notes |
|---|---|---|---|
| `snapshotAsMesh` result | binary PLY | exact | World space in v1. |
| smoothing groups | per-vertex normals | exact | Two faces share a smooth normal iff their masks share a bit; group 0 is always a hard edge. Merging is transitive. Averaging is weighted by the incident angle at the vertex. |
| separate position / UV indices | one unified index buffer | exact | Split on `(position, uv, smoothing class)`. |
| per-face material ids | one shape per id | exact geometry, **approximate materials** | Mitsuba has no per-face material. The geometry splits correctly, but v1 resolves every group of a node to the same `Material` and warns; Multi/Sub-Object is post-v1. |
| negative-determinant transform | reversed winding **and** negated normals | exact | Both, not either. Measured against Mitsuba 3.9: a `ply` shape carrying explicit `nx ny nz` uses those normals and ignores the winding, so reversing the triples alone leaves a mirrored node shaded — and emitting — inside-out. `Mesh.flip_normals` is deliberately left **False**; setting it on top inverts the emitting side even on an unmirrored shape. |

---

## Lights

**Max lights emit along local −Z**; Mitsuba's `spot` and `directional` emit along local
**+Z**. Confirmed by probe 03b: a `targetSpot` at `[0,0,100]` aimed at the origin reports
`transform.row3 = [0,0,1]`. The flip happens once, in `max_side.lights._emission_frame`,
together with an X negation so the basis stays right-handed.

| Max | Mitsuba | Class | Notes |
|---|---|---|---|
| `Free_Light`, `Target_Light` — `intensity` | `point.intensity` | approximate | `I_e = I_v / η`, η default 250 lm/W. Approximate only in η, which stands in for a spectral integral an RGB renderer cannot evaluate. **This is the supported path**: the value is real candela. |
| `intensityType` | — | **unresolved** | A plain integer whose enum probe 03b could not identify (assigning a `Name` maps every name, including nonsense, to the same integer). Only the default `1` is treated as known — candela, matching Max's documented 1500 cd default. Anything else converts as candela **and warns**. Probe 03c. |
| `distribution` | — | **unresolved** | Same. Only `0` (isotropic) is treated as known. |
| `webFile` | — | unsupported | Photometric webs warn and fall back to isotropic. |
| `useMultiplier` / `multiplier` | scales `intensity` | exact | A percentage dimmer, default 100. |
| `rgbFilter` | RGB split | exact | `magnitude · c / Y`, preserving luminance. |
| `useKelvin` / `kelvin` | — | unsupported | Warns; the filter colour is used as authored. |
| `Omnilight` — `multiplier` | `point.intensity` | **convention** | A unitless multiplier, not a photometric quantity. One multiplier unit is mapped to 1000 cd. This is the one number in the light path with no physical justification; it is why photometric lights are the supported path. |
| `freeSpot` / `targetSpot` — `hotspot`, `falloff` | `beam_width`, `cutoff_angle` | approximate | **Full** angles halved. Max's penumbra falls off linearly in the angle, Mitsuba's is a smooth cubic in the cosine: the cone edges coincide, the gradient between them does not. Manual check M3-1 confirms the full-angle reading. |
| `coneShape` (rectangular) | — | unsupported | Warns; a circular cone is used. |
| `overShoot` | — | unsupported | Warns. |
| `Directionallight` — `multiplier` | `directional.irradiance` | approximate | Same multiplier convention. Max's directional light is a bounded cylinder and Mitsuba's is unbounded; always warns. |
| near / far attenuation, non-inverse-square decay | — | unsupported | Warns. Mitsuba's emitters are strictly 1/r². |
| unsupported light classes | skipped | — | Warns and skips. There is no honest placeholder for a light: a guessed stand-in changes the whole image, whereas a missing one is obvious and correctly attributed. |

---

## Camera — 3ds Max Physical camera

**`cam.fov` is read directly.** Probe 01d swept focal length with
`lens_breathing_amount = 0` and found `fov == 2·atan(w / 2f)` exactly, to five decimals.
With the *default* breathing of 1.0 the identity breaks — 0.21° at 18 mm rising to 0.41° at
200 mm — because Max lengthens the effective focal length as the focus pulls in.
`zoom_factor` behaves the same way and leaves `focal_length_mm` untouched. So recomputing
from `film_width_mm` and `focal_length_mm` reproduces the *nominal* lens, not the one Max
is rendering with.

| Max | Mitsuba | Class | Notes |
|---|---|---|---|
| `fov` | `fov`, `fov_axis="x"` | exact | Already includes lens breathing, focus distance and zoom factor. |
| `transform` | sensor `to_world` | exact | Emitted as a `look_at` triple. Max cameras look down local −Z; X and Z are both negated so handedness is preserved. |
| `use_dof`, `f_number`, `focal_length_mm` | `thinlens.aperture_radius` | exact | `D = f/N`, so radius = `(f_mm / 2N) / 1000` metres. |
| `focus_distance` | `thinlens.focus_distance` | exact | Converted to metres. |
| `bokeh_shape`, `bokeh_blades_number`, `bokeh_anisotropy` | — | unsupported | Mitsuba's aperture is a disc. Warns when DoF is on and any is non-default. |
| `horizontal_shift`, `vertical_shift` | `principal_point_offset_x/y` | **unverified** | The ratio `mm / film_width` is exact once the unit is known; PROBE 05 — the unit and the sign — is manual check M3-2. Always warns when non-zero. |
| `*_tilt_correction` | — | unsupported | A Scheimpflug shear of the image plane, not a translation; it cannot be a principal-point offset. Warns. |
| `distortion_*`, `vignetting_*` | — | unsupported | Warns. |
| `clip_on`, `clip_near`, `clip_far` | `near_clip`, `far_clip` | exact | When `clip_on` is false Max's stored values are stale — probe 01d found `clip_near = 0.0` on a default camera — so the renderer defaults are used instead. |
| `ISO`, `exposure_value` | host exposure slider default | **uncalibrated** | `scale = (ISO/100)·2^(−EV)·K`. `K` is deliberately **1.0** and deliberately not fitted: SPEC §7.4 says to fit it against a reference Arnold render of a known-luminance surface, and that has not been done. A plausible invented constant is worse than an honest identity because it looks calibrated. Exposure is never baked into the render. |
| `white_balance_*` | — | unsupported | A chromatic adaptation on the output; out of scope for v1. Warns. |
| `motion_blur_enabled` | — | unsupported | Warns. |
| legacy Free/Target cameras | viewport fallback | — | Warns naming the node and renders the active viewport instead. |

---

## Known gaps in this table

Things the code does that this document does not yet classify, listed so they are not
mistaken for completeness:

- `refl_color` is read but not emitted, and does not currently warn.
- The roughness and anisotropy rows are marked *uncalibrated* rather than approximate. The
  M2 done-condition requires calibration renders (manual check M2-2) before they can be
  reclassified, and the fitted curve checked in as data alongside the script that produced
  it.
- `EffectiveLuminance` is a derived read-only property on PhysicalMaterial; it is not read,
  and it is not clear whether it should supersede `emit_luminance × emission`.
