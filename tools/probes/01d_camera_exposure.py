"""Probe 01d - why the Physical camera's `fov` misses 2*atan(w/2f), plus the camera basis.

Probe 01c found `fov` consistently *below* the horizontal FOV predicted from
`film_width_mm` and `focal_length_mm`, by 0.21 degrees at 18 mm rising to 0.40 degrees at
85 mm. A constant offset would suggest a different film width; a growing one does not. The
prime suspect is `lens_breathing_amount`, which changes the effective focal length with
focus distance, so this probe sweeps it and re-checks the identity.

It also fixes probe 01c's crash: `Physical_Camera(target:...)` wants a node, not a Point3.

    python tools/maxbatch.py tools/probes/01d_camera_exposure.py
"""

import math

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-44s %r" % ("  " + label + ":", v))
        return v
    except Exception as exc:
        print("%-44s FAILED  %s: %s" % ("  " + label + ":", type(exc).__name__, exc))
        return None


def predicted_fov_x(film_w_mm, focal_mm):
    return math.degrees(2.0 * math.atan(film_w_mm / (2.0 * focal_mm)))


rt.resetMaxFile(rt.name("noPrompt"))
cam = rt.Physical_Camera(pos=rt.Point3(0, -100, 0))

print("=== lens breathing hypothesis ===")
show("lens_breathing_amount default", lambda: float(cam.lens_breathing_amount))
show("target_distance default", lambda: float(cam.target_distance))
show("focus_distance default", lambda: float(cam.focus_distance))
show("specify_focus default", lambda: bool(cam.specify_focus))

cam.specify_fov = False
for breathing in (0.0, 1.0):
    try:
        cam.lens_breathing_amount = breathing
    except Exception as exc:
        print("  cannot set lens_breathing_amount=%s: %s" % (breathing, exc))
        continue
    print("  --- lens_breathing_amount = %.1f ---" % breathing)
    for f_mm in (18.0, 35.0, 50.0, 85.0, 200.0):
        cam.focal_length_mm = f_mm
        fw = float(cam.film_width_mm)
        pred = predicted_fov_x(fw, f_mm)
        got = float(cam.fov)
        # Solve back for the focal length Max must be using.
        implied_f = fw / (2.0 * math.tan(math.radians(got) / 2.0))
        print("    f=%6.1f  fov=%9.5f  predicted=%9.5f  delta=%+8.5f  implied_f=%9.5f"
              % (f_mm, got, pred, got - pred, implied_f))

print("=== focus distance dependence ===")
cam.lens_breathing_amount = 1.0
cam.focal_length_mm = 50.0
cam.specify_focus = True
for fd in (50.0, 200.0, 1000.0, 100000.0):
    try:
        cam.focus_distance = fd
        fw = float(cam.film_width_mm)
        got = float(cam.fov)
        implied_f = fw / (2.0 * math.tan(math.radians(got) / 2.0))
        print("  focus_distance=%10.1f  fov=%9.5f  implied_focal=%9.5f" % (fd, got, implied_f))
    except Exception as exc:
        print("  focus_distance=%10.1f FAILED %s: %s" % (fd, type(exc).__name__, exc))

cam.lens_breathing_amount = 0.0
cam.specify_focus = False

print("=== film presets ===")
show("film_preset", lambda: str(cam.film_preset))
show("film_width_mm", lambda: float(cam.film_width_mm))

print("=== zoom_factor exactness ===")
cam.focal_length_mm = 50.0
base = float(cam.fov)
for z in (0.5, 1.0, 2.0, 4.0):
    cam.zoom_factor = z
    got = float(cam.fov)
    pred = math.degrees(2.0 * math.atan(math.tan(math.radians(base) / 2.0) / z))
    print("  zoom=%4.1f  fov=%9.5f  predicted_from_tan=%9.5f  delta=%+8.5f  focal=%8.4f"
          % (z, got, pred, got - pred, float(cam.focal_length_mm)))
cam.zoom_factor = 1.0

print("=== camera basis / chirality ===")
tgt = rt.Point(pos=rt.Point3(0, 0, 0))
cam2 = rt.Physical_Camera(pos=rt.Point3(0, -100, 0), target=tgt)
for row in ("row1", "row2", "row3", "row4"):
    show("transform.%s" % row,
         (lambda r=row: [round(float(c), 6) for c in getattr(cam2.transform, r)]))
print("  Camera at y=-100 aimed at the origin: the view direction is (0,+1,0) in Max.")
print("  row3 is the local +Z axis. A Max camera looks down local -Z, so expect (0,-1,0).")

print("=== lens shift ===")
for shift in (0.0, 1.0, 18.0):
    cam.horizontal_shift = shift
    print("  horizontal_shift=%6.2f reads %r  (film_width_mm=%.2f)"
          % (shift, float(cam.horizontal_shift), float(cam.film_width_mm)))
cam.horizontal_shift = 0.0
print("  PROBE 05 stays open: only a render can tell mm from film-fractions. See")
print("  docs/MANUAL_CHECKS.md, check M3-2.")

print("=== exposure model ===")
for p in ("exposure_gain_type", "ISO", "exposure_value", "shutter_length_seconds",
          "shutter_type", "shutter_offset_degrees", "white_balance_type",
          "white_balance_kelvin", "white_balance_custom", "motion_blur_enabled",
          "vignetting_enabled", "vignetting_amount", "bokeh_shape", "bokeh_blades_number",
          "bokeh_rotation_degrees", "bokeh_anisotropy", "distortion_type",
          "distortion_cubic_amount", "distortion_texture",
          "horizontal_tilt_correction", "vertical_tilt_correction",
          "auto_vertical_tilt_correction", "clip_on", "clip_near", "clip_far",
          "use_dof", "f_number", "focus_distance", "specify_focus", "targeted",
          "environment_near", "environment_far"):
    show(p, (lambda p=p: getattr(cam, p)))

print("=== exposure_gain_type sweep ===")
for i in range(0, 4):
    try:
        cam.exposure_gain_type = i
        print("  set %d -> reads %r  ISO=%r EV=%r"
              % (i, cam.exposure_gain_type, cam.ISO, cam.exposure_value))
    except Exception as exc:
        print("  set %d FAILED %s: %s" % (i, type(exc).__name__, exc))

print("=== viewport fallback ===")
show("getActiveCamera", lambda: rt.getActiveCamera())
show("viewport.getType", lambda: str(rt.viewport.getType()))
show("viewport.getTM present", lambda: [round(float(c), 4)
                                        for c in rt.viewport.getTM().row4])
show("viewport.getFOV", lambda: float(rt.viewport.getFOV()))

print("PROBE_COMPLETE")
