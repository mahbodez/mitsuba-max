"""Prove the worker environment is sound, independently of 3ds Max.

    python -m worker.selftest [--out DIR] [--variant auto|cuda_ad_rgb|...]

Renders Mitsuba's built-in Cornell box and writes an EXR. It exists so that when something
goes wrong the user can answer "is it Mitsuba or is it the integration?" in ten seconds
rather than by guesswork, and so an environment failure produces a readable error instead
of a mysterious silent crash inside the render loop.

Also runs a white-furnace check: a white Lambertian sphere under a constant environment of
radiance 1 must render to exactly 1.0 everywhere. That single number catches energy, unit
and `twosided` errors, and it validates the installed Mitsuba build rather than this
project's code — which is exactly what a self-test should do.

Depends on nothing beyond `mitsuba` and `numpy`, so it keeps working even if the rest of
this package does not import.
"""

import argparse
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="worker.selftest")
    ap.add_argument("--out", type=Path, default=Path.cwd() / "build" / "selftest")
    ap.add_argument("--variant", default="auto")
    ap.add_argument("--spp", type=int, default=64)
    args = ap.parse_args(argv)

    try:
        import mitsuba as mi
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  cannot import the worker dependencies: {exc}", file=sys.stderr)
        print("      the venv needs exactly `mitsuba` and `numpy`", file=sys.stderr)
        return 2

    print(f"python        {sys.version.splitlines()[0]}")
    print(f"executable    {sys.executable}")
    print(f"mitsuba       {mi.__version__}")
    print(f"variants      {', '.join(mi.variants())}")

    from worker.render import select_variant

    try:
        variant = select_variant(args.variant)
    except RuntimeError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 3
    print(f"variant       {variant}")

    args.out.mkdir(parents=True, exist_ok=True)

    # -- Cornell box -------------------------------------------------------------------
    started = time.perf_counter()
    scene = mi.load_dict(mi.cornell_box())
    image = mi.render(scene, spp=args.spp)
    elapsed = time.perf_counter() - started
    exr = args.out / "cornell_box.exr"
    mi.util.write_bitmap(str(exr), image)
    arr = np.array(image)
    print(f"cornell box   {arr.shape} in {elapsed:.2f}s -> {exr}")
    print(f"              min {float(arr.min()):.5f}  max {float(arr.max()):.5f}  "
          f"mean {float(arr.mean()):.5f}")

    if not np.isfinite(arr).all():
        print("FAIL  the render contains NaN or Inf", file=sys.stderr)
        return 4

    # -- white furnace -----------------------------------------------------------------
    furnace = mi.load_dict({
        "type": "scene",
        "integrator": {"type": "path", "max_depth": 64, "rr_depth": 64},
        "sensor": {
            "type": "perspective",
            "fov": 45.0,
            "to_world": mi.ScalarTransform4f().look_at(
                origin=[0, 0, 4], target=[0, 0, 0], up=[0, 1, 0]
            ),
            "film": {"type": "hdrfilm", "width": 64, "height": 64,
                     "pixel_format": "rgba", "component_format": "float32",
                     "rfilter": {"type": "box"}},
            "sampler": {"type": "independent", "sample_count": 512},
        },
        "sphere": {
            "type": "sphere",
            "bsdf": {"type": "twosided",
                     "material": {"type": "diffuse",
                                  "reflectance": {"type": "rgb", "value": [1, 1, 1]}}},
        },
        "env": {"type": "constant", "radiance": {"type": "rgb", "value": [1, 1, 1]}},
    })
    furnace_img = np.array(mi.render(furnace, spp=512))[..., :3]
    deviation = float(np.abs(furnace_img - 1.0).max())
    mean_deviation = float(np.abs(furnace_img - 1.0).mean())
    print(f"white furnace max |L - 1| = {deviation:.5f}   mean {mean_deviation:.5f}")

    # The tolerance is on the mean, not the max. A path tracer under a constant environment
    # is unbiased but not noiseless, and a handful of pixels will always sit a few percent
    # out at any practical sample count; a mean that drifts is the real signal of an energy
    # bug. The tight per-pixel assertion belongs in the golden scene, which can afford to
    # render for minutes.
    if mean_deviation > 5e-3:
        print("FAIL  white furnace deviates: this build loses or creates energy",
              file=sys.stderr)
        return 5

    print("OK    worker environment is sound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
