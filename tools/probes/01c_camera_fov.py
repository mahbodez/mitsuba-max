"""Probe 01c - Physical camera FOV identity, zoom, shift units.

Builds its own scene. Read-only with respect to anything the user owns.
Run headless:  python tools/maxbatch.py tools/probes/01c_camera_fov.py

Resolves PROBE 04b (is `fov` horizontal?), 04c (zoom_factor semantics) and
gives a first reading on PROBE 05 (shift units).
"""

import math

from pymxs import runtime as rt


def show(label, fn):
    try:
        print("%-34s %r" % (label + ":", fn()))
    except Exception as exc:
        print("%-34s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))


rt.resetMaxFile(rt.name("noPrompt"))
cam = rt.Physical_Camera(pos=rt.Point3(0, 0, 0))

print("--- scale ---")
show("decodeValue 1.0m", lambda: rt.units.decodeValue("1.0m"))

print("--- fov identity (specify_fov off) ---")
cam.specify_fov = False
for f_mm in (18.0, 35.0, 50.0, 85.0):
    cam.focal_length_mm = f_mm
    fw = float(cam.film_width_mm)
    predicted = math.degrees(2.0 * math.atan(fw / (2.0 * f_mm)))
    print("  f=%5.1fmm  film_w=%5.2f  fov=%8.4f  predicted_fov_x=%8.4f  delta=%+8.4f"
          % (f_mm, fw, float(cam.fov), predicted, float(cam.fov) - predicted))

print("--- specify_fov on ---")
cam.specify_fov = True
cam.fov = 60.0
show("fov after set", lambda: float(cam.fov))
show("focal_length_mm after fov set", lambda: float(cam.focal_length_mm))

print("--- zoom_factor ---")
show("zoom_factor default", lambda: float(cam.zoom_factor))
cam.specify_fov = False
cam.focal_length_mm = 50.0
base_fov = float(cam.fov)
cam.zoom_factor = 2.0
print("  base_fov=%.4f  fov_at_zoom2=%.4f  focal=%.4f"
      % (base_fov, float(cam.fov), float(cam.focal_length_mm)))
cam.zoom_factor = 1.0

print("--- shift units ---")
show("horizontal_shift default", lambda: float(cam.horizontal_shift))
cam.horizontal_shift = 1.0
show("h_shift set 1.0 reads", lambda: float(cam.horizontal_shift))
show("film_width_mm", lambda: float(cam.film_width_mm))
print("  NOTE: units still ambiguous. Resolve PROBE 05 with a render-based")
print("  calibration: shift a centred grid by a known amount and measure pixels.")

print("--- render output ---")
for k in ("renderWidth", "renderHeight", "renderPixelAspect"):
    show(k, (lambda k=k: getattr(rt, k)))

print("--- classOf checks ---")
show("classOf(cam)", lambda: str(rt.classOf(cam)))
show("is Physical", lambda: str(rt.classOf(cam)) == "Physical")

print("--- camera basis (chirality) ---")
cam2 = rt.Physical_Camera(pos=rt.Point3(0, -100, 0), target=rt.Point3(0, 0, 0))
show("transform.row1 (local X)", lambda: [float(c) for c in cam2.transform.row1])
show("transform.row2 (local Y)", lambda: [float(c) for c in cam2.transform.row2])
show("transform.row3 (local Z)", lambda: [float(c) for c in cam2.transform.row3])
show("transform.row4 (position)", lambda: [float(c) for c in cam2.transform.row4])
print("  NOTE: a Max camera looks down local -Z. row3 should point AWAY from the target.")

print("--- exposure model ---")
for p in ("exposure_gain_type", "ISO", "exposure_value", "shutter_length_seconds",
          "shutter_type", "white_balance_type", "motion_blur_enabled",
          "vignetting_enabled", "vignetting_amount", "bokeh_shape",
          "bokeh_blades_number", "distortion_type", "distortion_cubic_amount",
          "horizontal_tilt_correction", "vertical_tilt_correction",
          "auto_vertical_tilt_correction", "clip_on", "clip_near", "clip_far",
          "use_dof", "f_number", "focus_distance", "specify_focus", "target_distance",
          "lens_breathing_amount", "film_preset"):
    show(p, (lambda p=p: getattr(cam, p)))

print("PROBE_COMPLETE")
