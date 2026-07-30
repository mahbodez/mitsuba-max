# Manual checks

Everything a machine can check is checked by `pytest`, `tools/run_golden.py` and the probe
scripts. This file is the residue: the things that need a human because they involve the
Max UI, a Qt window, or a judgement about whether an image looks right.

Batch mode has no UI, so nothing below can be automated with `3dsmaxbatch`.

**Keep every check under two minutes.** A check that takes longer does not get run, and an
unrun check is worse than no check because it looks like coverage.

Paste the requested output back into the session. "It worked" is not one of the requested
outputs for any check here.

---

## M0 — the vertical slice

### M0-1 · The plugin loads
**Build:** nothing. Open Max on an empty scene.
**Do:** Scripting → Python → Python Listener, then

```python
import sys; sys.path.insert(0, r"D:\VSCode\mitsuba-max")
import max_side
print(max_side.PROJECT_ROOT)
```

**Paste:** the printed path, or the full traceback.

### M0-2 · The environment wizard survives a cold start
**Build:** nothing. Delete `%LOCALAPPDATA%\mitsuba-max\settings.json` first, so this
genuinely tests the first-run path rather than a cached one.
**Do:** `max_side.setup_environment()` in the listener.
**Paste:** the whole console output, including the `mitsuba x.y.z, numpy x.y.z` line. If it
failed, paste the stderr block it printed — that block is the entire point of the flow.
**Expect:** two to five minutes on a normal connection; it downloads about 200 MB.

### M0-3 · Export produces IR and warnings
**Build:** a teapot with `mapCoords` on, a PhysicalMaterial with a non-default base colour
and roughness, one omni light, one Physical camera.
**Do:** `max_side.export_only()`.
**Paste:** the printed summary line, and the contents of the warnings list:

```python
r = max_side.export_only()
for w in r.warnings: print(w.category, w.node, w.reason)
print(r.scene.to_json()[:2000])
```

**Expect:** one shape, one material, one light, a `scene_scale_to_meters` matching your
scene's system units. Warnings are fine — silent substitutions are not, so read them.

### M0-4 · The render window parents to Max
This is the one thing `3dsmaxbatch` fundamentally cannot verify: batch mode returns a valid
`getMAXHWND()` (probe 13) but shows nothing, so parenting is untested until a human looks.

**Do:** click **Render with Mitsuba** from the menu (not the Listener). The menu path used
to discard the window reference and crash Max with "Unknown exception thrown executing
script"; that is fixed by the keep-alive in `max_side.ui`.
**Check:** the window appears **in front of** Max, and clicking the Max viewport does not
send it behind. Minimising Max minimises it too.
**Paste:** a screenshot, plus the window title (it names the Mitsuba variant).

### M0-5 · Progressive refresh actually refreshes
**Do:** with a render running, watch the image area.
**Check:** the image visibly denoises over successive passes rather than appearing once at
the end; the pass counter and elapsed time advance; Max stays responsive throughout — you
can orbit the viewport while it renders.
**Paste:** the final status line, e.g. `done · 512 spp · 4.2 s`.

### M0-6 · Cancel responds within one pass
**Do:** start a render with the default 32 passes and press **Cancel** early.
**Check:** the status line changes to the "takes effect at the end of the current pass"
message immediately, and the render stops within roughly one pass's worth of time — not
instantly, and not at the end of the full budget. This is a property of `mi.render()`, not
a bug.
**Paste:** the status line after it settles.

### M0-7 · A worker crash is visible, and the scene survives
**Do:** start a long render, then kill the worker's `python.exe` from Task Manager
(the one under `%LOCALAPPDATA%\mitsuba-max\venv`, not Max itself).
**Check:** a dialog appears reporting the exit code with the stderr tail in its
**Details** section, Max does not crash, and your unsaved scene is intact.
**Paste:** the text from the Details box.
**Why this is check M0-7 and not an afterthought:** the out-of-process design exists
precisely so a Dr.Jit segfault cannot take an unsaved scene with it. If the crash is
invisible, the benefit is invisible.

### M0-8 · Live exposure and gamma
**Do:** after a render finishes, drag the exposure slider.
**Check:** the image re-exposes **instantly**, with no re-render and no progress bar. The
pass counter does not move.
**Paste:** nothing; just confirm it is instant rather than a second's pause.

### M0-9 · One worker process across re-renders
**Do:** open Task Manager, filter for `python.exe` under
`%LOCALAPPDATA%\mitsuba-max\venv`. Render once, note the PID. Render two more times
without closing Max.
**Check:** still exactly one such process, and its PID is unchanged. VRAM usage on the GPU
does not climb by a full context per click.
**Paste:** the three PIDs (they should be identical).
**Why:** a prior bug started a new worker on every Render and never shut the old one down,
so three clicks meant three CUDA contexts.

---

## M2 — materials

### M2-1 · `twosided` / `normalmap` nesting (resolves PROBE 08)
**Build:** a sphere with a PhysicalMaterial carrying a strong normal map in `bump_map`
(a high-frequency pattern, amount 1.0).
**Do:** render. Then edit `core/emit_dict.material_to_bsdf` to swap the wrapping order —
`normalmap { twosided { principled } }` — and render again.
**Check:** the two images differ, and the shipped order is the one showing surface relief.
**Paste:** both screenshots.
**Why manual:** a silently ignored normal map produces a perfectly plausible smooth sphere.
There is no number that distinguishes "the map is applied" from "the map is flat" without a
reference.

### M2-2 · Roughness and metalness sweep
**Build:** a 5x5 grid of spheres, roughness 0→1 across, metalness 0→1 down, one
PhysicalMaterial each, lit by one area light and an HDRI.
**Do:** render in Mitsuba and in Arnold at matching exposure.
**Paste:** both images side by side.
**Why:** this is the input to the calibration task in SPEC §14. Until it is run, the
roughness and anisotropy rows in `docs/MATERIAL_MAPPING.md` stay marked *uncalibrated*.

### M2-3 · Glossiness inversion
**Build:** two materials, identical except one has `roughness_inv` ticked with a
**mapped** roughness.
**Check:** the exporter bakes an inverted copy of the map (look for `textures/inv_*.png`
under the export root) and the two spheres look like inverses of each other, not identical.
**Paste:** the export root listing and a screenshot.

---

## M3 — lights and camera

### M3-1 · Spot cone angle is a full angle
**Build:** a spot light 100 units above a large flat plane, aimed straight down, falloff
set to 60°, hotspot 58°.
**Do:** render, and measure the diameter of the lit disc in the image.
**Check:** the cone half-angle is 30°, so at 100 units the lit **radius** is
`100 · tan(30°) = 57.7` units — diameter 115.5. If the radius is instead 173 units
(`100 · tan(60°)`), the angles are half angles and `core.units.spot_angles` must stop
halving them.
**Paste:** the measured radius in scene units.

### M3-2 · Lens shift units and sign (resolves PROBE 05)
**Build:** a Physical camera aimed at a grid of boxes centred in frame, `film_width_mm`
36. Render once with `horizontal_shift = 0`, once with `horizontal_shift = 3.6`.
**Do:** measure the horizontal displacement of the grid centre, in pixels, between the two.
**Check:** 3.6 mm on a 36 mm film is one tenth of the film width, so a 1280-wide render
should shift by **128 px** if the property is millimetres. If it shifts by 1280 px it is
already a film fraction; if it shifts the other way the sign is inverted.
**Paste:** the two images and the measured pixel offset.
**Why this cannot be automated:** nothing in the property values distinguishes millimetres
from a normalised fraction (probe 01d), and Max exposes no read-back of the projection.

### M3-3 · Photometric intensity is physically right
**Build:** a photometric free light of exactly **1000 cd**, isotropic, 1 metre above a
white Lambertian plane, no other lights, no environment.
**Do:** render and read the linear pixel value at the point directly beneath the light
(Save EXR, then inspect — do not read the tone-mapped preview).
**Check:** `E = I/r² = 1000 lx` at 1 m. With efficacy 250 lm/W that is 4 W/m² of
irradiance, and a Lambertian surface of albedo `ρ` has radiance `L = ρE/π`. For ρ = 1,
`L = 4/π = 1.273 W/(sr·m²)`.
**Paste:** the measured pixel value and the albedo you used.
**Tolerance:** within a few percent. A factor of 4π, π or 683 means a specific bug — see
`core/units.py` for which.

### M3-4 · Viewport rendering
**Build:** any scene, then delete every camera.
**Do:** orbit the perspective viewport to a distinctive angle and render.
**Check:** the rendered image matches the viewport framing, and is not mirrored — compare
a recognisable asymmetric object.
**Paste:** a viewport screenshot and the render, side by side.

---

## M4 — production usability

### M4-1 · Cold start, guided only by the UI
**Build:** a machine (or user account) with no Python and no prior settings.
**Do:** follow only what the UI tells you, from installing the plugin to a first image.
**Paste:** every point at which you had to guess, read source, or ask.
**Why:** this is the M4 done-condition verbatim. Anything you had to guess is a bug in the
flow, not in your understanding.

### M4-2 · Asset dedup on re-render
**Do:** render, move only the camera, render again.
**Check:** the second export reports `0 assets written, N reused` and completes in a small
fraction of the first.
**Paste:** both summary lines.

### M4-3 · Warnings panel
**Build:** a scene containing one Standard material, one Multi/Sub-Object, and one legacy
Target camera — i.e. three things v1 does not support.
**Check:** the warnings panel lists all three by node name with a stated reason, the render
still completes, and unsupported materials appear as 50% grey rather than black or missing.
**Paste:** the warnings panel contents.

### M4-4 · Render menu action on a numpy-less Max
**Build:** nothing — any scene with one object and a camera.
**Do:** from a freshly started Max, run the "Render with Mitsuba" action.
**Check:** no `ModuleNotFoundError: No module named 'numpy'`. The render window opens.
**Then:** rename `%LOCALAPPDATA%\mitsuba-max\venv` temporarily and run the action again.
**Check:** the failure is `NumpyUnavailable` naming the directories tried and telling you to
run the environment wizard — not a bare import error.
**Paste:** both outcomes. Rename the venv back afterwards.
**Why:** Max ships no numpy (probe 06c), so `core.film` only imports once the bridge has
been armed. This is the check that the arming happens before, not after, that import.

---

## Still unresolved, and what would close it

| Probe | Question | Closed by |
|---|---|---|
| 03c | What integers `intensityType` and `distribution` take on photometric lights | SDK enum headers, or a render-based calibration against a known-candela light |
| 05 | Lens shift units and sign | **M3-2** |
| 08 | `twosided` / `normalmap` nesting order | **M2-1** |
| 13b | Whether `3dsmaxbatch` runs while the Max UI is open | run `uv run python tools/maxbatch.py tools/probes/13_batch_env.py` with Max open and report whether it succeeds or reports a licence error |
