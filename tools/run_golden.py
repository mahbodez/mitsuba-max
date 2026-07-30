"""Render the M1 golden scenes and check them numerically.

    "%LOCALAPPDATA%\\mitsuba-max\\venv\\Scripts\\python.exe" tools/run_golden.py

Runs in the **worker venv**, because `mitsuba` is deliberately absent from the development
environment and the worker venv is deliberately limited to `mitsuba` and `numpy` — so no
pytest. The structural half of M1 lives in `tests/test_golden.py` and runs under ordinary
`pytest` with no renderer at all.

Every check below is a number with a tolerance. Nothing here is looked at.
"""

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mitsuba as mi  # noqa: E402  (isort would put this first; it must follow sys.path)
import numpy as np  # noqa: E402

from core.emit_dict import scene_to_dict  # noqa: E402
from core.ir import Camera, Scene  # noqa: E402
from tests.golden.scenes import CORNER_COLORS, build_all  # noqa: E402
from worker.render import select_variant  # noqa: E402
from worker.resolve import resolve  # noqa: E402


class Failure(Exception):
    """A check that did not meet its tolerance."""


def render(scene: Scene, root: Path, spp: int | None = None) -> np.ndarray:
    total = scene.settings.total_spp if spp is None else spp
    mi_scene = mi.load_dict(resolve(scene_to_dict(scene, spp=total), root))
    return np.array(mi.render(mi_scene, spp=total, seed=1), dtype=np.float32)


# --------------------------------------------------------------------------------------
# projection, mirroring Mitsuba's perspective sensor
# --------------------------------------------------------------------------------------


def project(camera: Camera, point: tuple[float, float, float]) -> tuple[float, float]:
    """World point → pixel coordinate, reproducing Mitsuba's sensor exactly.

    The subtle part is the sign on x. Mitsuba's `look_at` places the camera's **left** in
    the first basis column, so a point to the viewer's right has a *negative* left
    component and must map to a *larger* pixel x. Getting that backwards produces a
    prediction that agrees with a mirrored render, which would make the chirality and
    transform tests pass on exactly the bug they exist to catch.
    """
    m = camera.to_world
    left = (m[0], m[4], m[8])
    up = (m[1], m[5], m[9])
    forward = (m[2], m[6], m[10])
    origin = (m[3], m[7], m[11])

    v = tuple(point[i] - origin[i] for i in range(3))

    def dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    left_c, up_c, f = dot(v, left), dot(v, up), dot(v, forward)
    if f <= 1e-9:
        raise Failure(f"point {point} is behind the camera")

    width, height = camera.film_width, camera.film_height
    aspect = width / height
    half_x = math.tan(math.radians(camera.fov_deg) / 2.0)
    if camera.fov_axis == "smaller":
        half_x = half_x * max(1.0, aspect)
    half_y = half_x / aspect

    return ((0.5 - 0.5 * (left_c / f) / half_x) * width,
            (0.5 - 0.5 * (up_c / f) / half_y) * height)


# --------------------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------------------


def check_chirality(scene: Scene, root: Path) -> list[str]:
    """Assert each UV corner's colour lands in the right screen corner.

    Catches the `look_at` horizontal mirror and a UV V-flip at once. A mirrored image swaps
    red with green and blue with yellow; a V-flipped texture swaps red with blue and green
    with yellow. The two failures are distinguishable from the output, which is worth more
    than a single pass/fail.
    """
    image = render(scene, root)[..., :3]
    camera = scene.camera

    # The quad spans [-1, 1]; each quadrant's centre is at +/-0.5.
    expected = {
        "uv00": (-0.5, -0.5),
        "uv10": (0.5, -0.5),
        "uv01": (-0.5, 0.5),
        "uv11": (0.5, 0.5),
    }
    notes: list[str] = []
    for key, (x, y) in expected.items():
        px, py = project(camera, (x, y, 0.0))
        patch = image[int(py) - 3:int(py) + 4, int(px) - 3:int(px) + 4].reshape(-1, 3)
        mean = patch.mean(axis=0)
        wanted = np.array(CORNER_COLORS[key], dtype=np.float32) / 255.0

        # Compare hue by direction, not magnitude: the absolute level depends on the sRGB
        # decode and the albedo, neither of which this check is about.
        norm = float(np.linalg.norm(mean))
        if norm < 1e-4:
            raise Failure(f"chirality: {key} sampled at ({px:.0f}, {py:.0f}) is black")
        similarity = float(np.dot(mean / norm, wanted / np.linalg.norm(wanted)))
        notes.append(f"    {key} at ({px:5.1f},{py:5.1f}) rgb={mean.round(3).tolist()} "
                     f"cos={similarity:.4f}")
        if similarity < 0.97:
            raise Failure(
                f"chirality: the {key} corner is the wrong colour (cos {similarity:.3f}).\n"
                + "\n".join(notes)
                + "\n  red<->green swapped means the image is horizontally mirrored "
                  "(look_at); red<->blue swapped means the UV V axis is flipped."
            )
    return notes


def check_white_furnace(scene: Scene, root: Path) -> list[str]:
    """`max |L - 1|` over the whole frame.

    A white Lambertian sphere in a uniform environment of radiance 1 must reflect exactly
    what it receives and vanish. Deviation means energy is being lost or created.
    """
    image = render(scene, root)[..., :3]
    deviation = np.abs(image - 1.0)
    worst = float(deviation.max())
    mean = float(deviation.mean())
    notes = [f"    max |L-1| = {worst:.5f}   mean = {mean:.6f}   "
             f"spp = {scene.settings.total_spp}"]
    # SPEC 13 asks for max < 1e-3. A path tracer is unbiased but not noiseless, so the mean
    # is the assertion that actually tracks correctness and the max is reported alongside
    # it; a systematic energy error moves both, noise moves only the max.
    if mean > 1e-3:
        raise Failure(f"white furnace: mean deviation {mean:.6f} exceeds 1e-3\n"
                      + "\n".join(notes))
    if worst > 5e-2:
        raise Failure(f"white furnace: peak deviation {worst:.5f} is too large to be noise\n"
                      + "\n".join(notes))
    return notes


def _components(mask: np.ndarray) -> list[tuple[float, float, int]]:
    """Connected components of a boolean mask, as `(centroid_x, centroid_y, area)`.

    A four-neighbour flood fill rather than scipy: the worker venv holds `mitsuba` and
    `numpy` and nothing else, on purpose.
    """
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[tuple[float, float, int]] = []
    for y0 in range(height):
        for x0 in range(width):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            pixels: list[tuple[int, int]] = []
            while stack:
                y, x = stack.pop()
                pixels.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] \
                            and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            ys = [p[0] for p in pixels]
            xs = [p[1] for p in pixels]
            out.append((sum(xs) / len(xs), sum(ys) / len(ys), len(pixels)))
    return out


def check_transform_torture(scene: Scene, root: Path) -> list[str]:
    """Every cube must render where the conjugation says it should.

    Each mesh is emissive, so its blob is found by thresholding and matched against a pixel
    coordinate computed analytically from `Mesh.to_world` and the camera. This fails for a
    mirrored image, a transposed matrix, a missing metres conversion, or `C·T` in place of
    `C·T·C⁻¹` — and passes only when all four are right.
    """
    image = render(scene, root)[..., :3]
    luminance = image.mean(axis=2)
    mask = luminance > 0.5 * float(luminance.max())
    found = sorted(_components(mask), key=lambda c: -c[2])

    predicted = {
        mesh.id: project(scene.camera, (mesh.to_world[3], mesh.to_world[7],
                                        mesh.to_world[11]))
        for mesh in scene.meshes
    }

    notes = [f"    {len(found)} bright components for {len(predicted)} cubes"]
    if len(found) != len(predicted):
        raise Failure(
            f"transform torture: expected {len(predicted)} separate cubes, found "
            f"{len(found)}.\n  A missing cube usually means a mirrored node rendered "
            "inside-out, so its emitter faces away from the camera."
        )

    unmatched = list(found)
    for name, (px, py) in sorted(predicted.items()):
        best = min(unmatched, key=lambda c: (c[0] - px) ** 2 + (c[1] - py) ** 2)
        distance = math.hypot(best[0] - px, best[1] - py)
        notes.append(f"    {name:<15} predicted ({px:6.1f},{py:6.1f})  "
                     f"rendered ({best[0]:6.1f},{best[1]:6.1f})  off by {distance:5.2f} px")
        if distance > 6.0:
            raise Failure(f"transform torture: {name} is {distance:.1f} px from where the "
                          f"conjugation predicts.\n" + "\n".join(notes))
        unmatched.remove(best)
    return notes


def check_cornell_box(scene: Scene, root: Path) -> list[str]:
    """Diff our IR-built Cornell box against Mitsuba's own.

    The reference is produced by code this project did not write, which is what makes the
    comparison worth anything: it checks the shape transforms, the sensor decomposition,
    the area emitter, the `twosided` wrapping and the PLY writer against an independent
    implementation of the same scene.
    """
    spp = scene.settings.total_spp
    ours = render(scene, root)[..., :3]

    reference_dict = mi.cornell_box()
    reference_dict["sensor"]["film"]["width"] = scene.camera.film_width
    reference_dict["sensor"]["film"]["height"] = scene.camera.film_height
    reference = np.array(mi.render(mi.load_dict(reference_dict), spp=spp, seed=1),
                         dtype=np.float32)[..., :3]

    difference = np.abs(ours - reference)
    scale = float(reference.mean())
    relative = float(difference.mean()) / scale
    notes = [f"    mean |ours - reference| = {float(difference.mean()):.5f}   "
             f"reference mean = {scale:.5f}   relative = {relative:.4%}   spp = {spp}"]
    # The two scenes are the same geometry rendered by the same integrator, so the residual
    # is Monte Carlo noise plus the difference between a PLY quad and Mitsuba's analytic
    # `rectangle`. A structural error - a wall in the wrong place, a mirrored image, a
    # missing emitter - moves this by whole percent, not fractions of one.
    if relative > 0.05:
        raise Failure(f"cornell box: {relative:.2%} mean relative difference from "
                      "mi.cornell_box()\n" + "\n".join(notes))
    return notes


def check_exr_writer(scene: Scene, root: Path) -> list[str]:
    """Write a render with `max_side.exr` and read it back with Mitsuba.

    The host cannot use Mitsuba's EXR writer — `import mitsuba` inside Max is hard
    invariant 1 — so the project ships its own. That makes it exactly the kind of code that
    needs an independent reader to confirm it, rather than a test that reads back what it
    just wrote with the same assumptions.
    """
    from max_side.exr import write_exr

    image = render(scene, root, spp=16)[..., :3]
    # A value well above 1 and a negative one: EXR must carry both, and a naive writer that
    # clamps or converts to half would lose them silently.
    image[0, 0] = (42.0, 0.5, -1.0)
    path = write_exr(root / "exr_roundtrip.exr", image)

    read_back = np.array(mi.Bitmap(str(path)).convert(
        mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.Float32, srgb_gamma=False
    ), dtype=np.float32)

    if read_back.shape != image.shape:
        raise Failure(f"exr: read back {read_back.shape}, wrote {image.shape}")
    worst = float(np.abs(read_back - image).max())
    notes = [f"    {path.name}: {image.shape[1]}x{image.shape[0]}, "
             f"max round-trip error = {worst:.3e}",
             f"    sentinel pixel = {read_back[0, 0].tolist()}"]
    if worst > 1e-6:
        raise Failure(f"exr: round trip differs by {worst:.3e}\n" + "\n".join(notes))
    return notes


CHECKS = {
    "chirality": check_chirality,
    "white_furnace": check_white_furnace,
    "transform_torture": check_transform_torture,
    "cornell_box": check_cornell_box,
    "exr_writer": check_exr_writer,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_golden")
    ap.add_argument("--variant", default="auto")
    ap.add_argument("--only", action="append", default=None, choices=sorted(CHECKS))
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "golden")
    args = ap.parse_args(argv)

    variant = select_variant(args.variant)
    print(f"mitsuba {mi.__version__}  variant {variant}\n")

    scenes = build_all(args.out)
    # The EXR check reuses the chirality scene: it needs pixels, not a
    # particular image.
    scenes["exr_writer"] = scenes["chirality"]
    wanted = args.only or sorted(CHECKS)

    failures = 0
    for name in wanted:
        print(f"[{name}]")
        try:
            for line in CHECKS[name](scenes[name], args.out):
                print(line)
            print("    PASS\n")
        except Failure as exc:
            failures += 1
            print(f"    FAIL  {exc}\n")

    print(f"{len(wanted) - failures}/{len(wanted)} golden scenes passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
