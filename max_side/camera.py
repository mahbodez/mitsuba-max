"""Max cameras and viewports → IR `Camera`.

The one thing worth reading before editing this file: **`cam.fov` is already correct**.

Probe 01d swept focal length with `lens_breathing_amount = 0` and found
`fov == 2·atan(film_width_mm / (2·focal_length_mm))` exactly, to five decimal places. With
the *default* breathing of 1.0 the identity breaks — by 0.21° at 18 mm rising to 0.41° at
200 mm — because Max lengthens the effective focal length as the focus pulls in. Sweeping
`focus_distance` confirmed the mechanism (50 mm at 50 units behaves as 55.6 mm).

`zoom_factor` behaves the same way: it divides the tangent of the half angle exactly, and
leaves `focal_length_mm` untouched.

So recomputing the FOV from `film_width_mm` and `focal_length_mm` reproduces the *nominal*
lens, not the one Max is rendering with. Read `cam.fov`, emit `fov_axis = "x"`, done.
"""

import math
from dataclasses import dataclass, field

from pymxs import runtime as rt

from core import transform as tf
from core.ir import Camera, Warning_
from core.registry import CAMERAS, camera
from core.units import exposure_scale

__all__ = ["CameraContext", "camera_from_node", "camera_from_viewport", "resolve_camera"]

EXPOSURE_CALIBRATION_K = 1.0
"""The `K` in `scale = (ISO/100) · 2^(−EV) · K`.

Deliberately 1.0 and deliberately *not* fitted. SPEC §7.4 says to fit it against a
reference Arnold render of a known-luminance surface and record the procedure; that has not
been done, and a plausible-looking invented constant is worse than an honest identity
because it looks calibrated. The exposure slider still starts in a sensible place because
the ISO and EV terms carry the photographic relationship; only the absolute offset is
unfitted.
"""


@dataclass
class CameraContext:
    scene_scale_to_meters: float = 1.0
    width: int = 1280
    height: int = 720
    warnings: list[Warning_] = field(default_factory=list)

    def warn(self, node: str, reason: str, category: str = "camera") -> None:
        self.warnings.append(Warning_(node=node, reason=reason, category=category))


def _to_world(node, scale_to_meters: float) -> tuple[float, ...]:
    """Max camera transform → a Mitsuba camera-to-world matrix.

    Two conversions at once, both confirmed by probe 01d on a camera at `[0, -100, 0]`
    aimed at the origin (`row1 = [1,0,0]`, `row2 = [0,0,1]`, `row3 = [0,-1,0]`):

    * `transform.rowN` are the local axes in world space — the **columns** of the
      column-vector matrix used here, not the rows.
    * A Max camera looks down its local **−Z**, while Mitsuba's sensor looks down **+Z**.
      Negating Z alone would flip handedness and mirror the image, so X is negated with it,
      which is exactly the left-handed first basis vector Mitsuba's own `look_at` produces.
    """
    m = node.transform
    x = tf.vector_max_to_mitsuba((float(m.row1.x), float(m.row1.y), float(m.row1.z)))
    y = tf.vector_max_to_mitsuba((float(m.row2.x), float(m.row2.y), float(m.row2.z)))
    z = tf.vector_max_to_mitsuba((float(m.row3.x), float(m.row3.y), float(m.row3.z)))
    origin = tf.point_max_to_mitsuba(
        (float(m.row4.x), float(m.row4.y), float(m.row4.z)), scale_to_meters
    )
    return tf.from_axes((-x[0], -x[1], -x[2]), y, (-z[0], -z[1], -z[2]), origin)


@camera("Physical")
def translate_physical_camera(node, ctx: CameraContext) -> Camera:
    """3ds Max Physical camera → IR `Camera`."""
    name = str(node.name)
    _warn_unsupported(node, ctx, name)

    aperture_radius_m = None
    focus_distance_m = None
    if bool(node.use_dof):
        # Physical aperture diameter is D = f / N, so the radius in metres is
        # (f_mm / 2N) / 1000. Exact.
        focal_mm = float(node.focal_length_mm)
        aperture_radius_m = (focal_mm / (2.0 * float(node.f_number))) / 1000.0
        focus_distance_m = float(node.focus_distance) * ctx.scene_scale_to_meters

    near_clip, far_clip = _clipping(node, ctx)

    aspect = ctx.width / ctx.height if ctx.height else 1.0
    shift = (float(node.horizontal_shift), float(node.vertical_shift))
    offset = (0.0, 0.0)
    if shift != (0.0, 0.0):
        offset = tf.principal_point_offset_from_shift_mm(
            shift, float(node.film_width_mm), aspect
        )
        ctx.warn(name,
                 "lens shift was exported as a principal-point offset of "
                 f"({offset[0]:.4f}, {offset[1]:.4f}); PROBE 05 is open, so the unit and "
                 "sign are unconfirmed — see manual check M3-2")

    return Camera(
        # Read directly. It already accounts for lens breathing, focus distance and zoom.
        to_world=_to_world(node, ctx.scene_scale_to_meters),
        fov_deg=float(node.fov),
        fov_axis="x",
        near_clip=near_clip,
        far_clip=far_clip,
        principal_point_offset=offset,
        film_width=ctx.width,
        film_height=ctx.height,
        aperture_radius_m=aperture_radius_m,
        focus_distance_m=focus_distance_m,
        exposure_scale=exposure_scale(float(node.ISO), float(node.exposure_value),
                                      EXPOSURE_CALIBRATION_K),
    )


def _clipping(node, ctx: CameraContext) -> tuple[float, float]:
    """`clip_on` / `clip_near` / `clip_far`, in metres.

    When `clip_on` is false Max's stored values are stale — probe 01d found
    `clip_near = 0.0` on a default camera, which would be an invalid near plane — so
    Mitsuba's own defaults are used instead of reading them.
    """
    if not bool(node.clip_on):
        return (1e-2, 1e4)
    near = float(node.clip_near) * ctx.scene_scale_to_meters
    far = float(node.clip_far) * ctx.scene_scale_to_meters
    if near <= 0.0 or far <= near:
        ctx.warn(str(node.name),
                 f"clipping planes are degenerate (near {near:g}, far {far:g}); "
                 "the renderer defaults were used")
        return (1e-2, 1e4)
    return (near, far)


def _warn_unsupported(node, ctx: CameraContext, name: str) -> None:
    """One warning per Physical-camera feature that has no Mitsuba counterpart."""
    if float(node.horizontal_tilt_correction) or float(node.vertical_tilt_correction):
        ctx.warn(name, "tilt correction is a Scheimpflug shear of the image plane, not a "
                       "translation, and cannot be expressed as a principal-point offset; "
                       "it was ignored")
    if bool(node.auto_vertical_tilt_correction):
        ctx.warn(name, "automatic vertical tilt correction is not supported and was ignored")
    if int(node.distortion_type) != 0 or float(node.distortion_cubic_amount) != 0.0:
        ctx.warn(name, "lens distortion is not supported and was ignored")
    if node.distortion_texture is not None:
        ctx.warn(name, "a distortion texture is not supported and was ignored")
    if bool(node.vignetting_enabled):
        ctx.warn(name, "vignetting is not supported and was ignored")
    if bool(node.motion_blur_enabled):
        ctx.warn(name, "motion blur is out of scope for v1 and was ignored")
    if int(node.white_balance_type) != 0:
        ctx.warn(name, "white balance is a chromatic adaptation on the output and is out "
                       "of scope for v1; it was ignored")
    if bool(node.use_dof) and (int(node.bokeh_shape) != 0
                               or float(node.bokeh_anisotropy) != 0.0):
        ctx.warn(name, "bokeh shape and anisotropy have no Mitsuba equivalent — the "
                       "aperture is a disc — and were ignored")
    if float(node.lens_breathing_amount) != 0.0:
        # Not a defect, just worth stating: it is folded into `fov`, which surprises anyone
        # who checks the export against the focal length in the UI.
        ctx.warn(name,
                 f"lens breathing ({float(node.lens_breathing_amount):g}) changes the "
                 "effective focal length; the exported FOV comes from cam.fov and already "
                 "includes it, so it will not match 2·atan(film_width / 2·focal_length)",
                 category="info")


def camera_from_viewport(ctx: CameraContext) -> Camera:
    """The active perspective viewport as a camera — what users will actually do most often.

    `rt.viewport.getTM()` returns the **world-to-view** matrix (probe 01d), so it is
    inverted to obtain camera-to-world. `getFOV()` returns the horizontal field of view in
    degrees.
    """
    view_tm = rt.viewport.getTM()
    world_to_view = _matrix_from_max(view_tm)
    camera_to_world_max = tf.inverse(world_to_view)

    # The same −Z / handedness correction as a real camera, applied to the recovered basis.
    m = camera_to_world_max
    x = tf.vector_max_to_mitsuba((m[0], m[4], m[8]))
    y = tf.vector_max_to_mitsuba((m[1], m[5], m[9]))
    z = tf.vector_max_to_mitsuba((m[2], m[6], m[10]))
    origin = tf.point_max_to_mitsuba((m[3], m[7], m[11]), ctx.scene_scale_to_meters)

    return Camera(
        to_world=tf.from_axes((-x[0], -x[1], -x[2]), y, (-z[0], -z[1], -z[2]), origin),
        fov_deg=float(rt.viewport.getFOV()),
        fov_axis="x",
        film_width=ctx.width,
        film_height=ctx.height,
    )


def _matrix_from_max(m) -> tuple[float, ...]:
    """A MAXScript `Matrix3` as a row-major 16-tuple, transposing rows into columns."""
    r1, r2, r3, r4 = m.row1, m.row2, m.row3, m.row4
    return (
        float(r1.x), float(r2.x), float(r3.x), float(r4.x),
        float(r1.y), float(r2.y), float(r3.y), float(r4.y),
        float(r1.z), float(r2.z), float(r3.z), float(r4.z),
        0.0, 0.0, 0.0, 1.0,
    )


def camera_from_node(node, ctx: CameraContext) -> Camera:
    """Dispatch on `classOf`, falling back to the viewport for unsupported camera classes."""
    cls = str(rt.classOf(node))
    handler = CAMERAS.lookup(cls)
    if handler is None:
        ctx.warn(str(node.name),
                 f"camera class {cls} is not supported in v1 (Physical cameras only); "
                 "the active viewport was rendered instead")
        return camera_from_viewport(ctx)
    return handler(node, ctx)


def resolve_camera(ctx: CameraContext, node=None) -> Camera:
    """The camera to render: an explicit node, else the active camera, else the viewport."""
    if node is not None:
        return camera_from_node(node, ctx)
    active = rt.getActiveCamera()
    if active is not None:
        return camera_from_node(active, ctx)
    return camera_from_viewport(ctx)


def aspect_preserving_resolution(width: int, height: int, scale: float) -> tuple[int, int]:
    """Apply the half/quarter resolution toggle, never dropping below 1 pixel."""
    w = max(1, math.floor(width * scale + 0.5))
    h = max(1, math.floor(height * scale + 0.5))
    return w, h
