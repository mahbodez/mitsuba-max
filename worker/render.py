"""Progressive rendering: split the budget into passes, accumulate, publish, check cancel.

`mi.render()` cannot be interrupted once it is running. That single fact shapes this whole
module: the only way to offer a responsive Cancel is to render the sample budget in slices
and look for a pending cancellation between them. With the default 32 passes, worst-case
cancel latency is one pass — stated in the README so it is not filed as a bug.

Splitting also buys progressive display for free, which is what makes the integration feel
interactive rather than like a batch job with a progress bar.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mitsuba as mi
import numpy as np
import numpy.typing as npt

from core import film as film_mod
from worker.resolve import resolve

__all__ = ["RenderResult", "render_progressive", "select_variant"]

F32 = npt.NDArray[np.float32]

VARIANT_PREFERENCE = ("cuda_ad_rgb", "llvm_ad_rgb", "scalar_rgb")
"""Tried in order when the host asks for `auto`.

`scalar_rgb` is last and is a real fallback, not a formality: this project's own
development machine has a working CUDA backend and a *broken* LLVM one
(`jitc_llvm_init(): LLVM API initialization failed`), so a two-entry chain would leave a
CPU-only machine with the same LLVM problem unable to render at all.
"""


def select_variant(requested: str = "auto") -> str:
    """Call `mi.set_variant` exactly once and return the variant that actually took.

    Reported back in the `ready` message and shown in the window title. A user must never
    be uncertain about whether they are on the GPU — "it feels slow today" is not a
    diagnosis anyone can act on.
    """
    candidates = VARIANT_PREFERENCE if requested == "auto" else (requested,)
    errors: list[str] = []
    for variant in candidates:
        try:
            mi.set_variant(variant)
            return variant
        except Exception as exc:  # noqa: BLE001 - the message is the useful part
            errors.append(f"{variant}: {exc}")
    raise RuntimeError(
        "no usable Mitsuba variant. Tried:\n  " + "\n  ".join(errors)
    )


class RenderResult:
    __slots__ = ("cancelled", "elapsed_s", "image", "passes_done", "spp_done")

    def __init__(self, image: F32, passes_done: int, spp_done: int, elapsed_s: float,
                 cancelled: bool) -> None:
        self.image = image
        self.passes_done = passes_done
        self.spp_done = spp_done
        self.elapsed_s = elapsed_s
        self.cancelled = cancelled


def render_progressive(
    scene_dict: dict[str, Any],
    *,
    film_path: str,
    width: int,
    height: int,
    spp_per_pass: int,
    passes: int,
    seed: int = 0,
    scene_root: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_pass: Callable[[int, int, float], None] | None = None,
) -> RenderResult:
    """Render `scene_dict`, publishing an accumulated frame into the mmap film per pass.

    The accumulator is the running mean

        L̂_k = L̂_{k−1} + (L_k − L̂_{k−1}) / k

    rather than a sum divided at the end. Same arithmetic, but the buffer holds a
    displayable image at every point, so the host can repaint after any pass without
    knowing how many are still to come.

    Each pass gets a distinct seed. Reusing one seed would render the same sample sequence
    every pass and converge to a biased image that looks noiseless and is wrong — a failure
    mode that is essentially invisible without a reference.
    """
    scene = mi.load_dict(resolve(scene_dict, scene_root))

    accumulator: F32 = np.zeros((height, width, film_mod.CHANNELS), dtype=np.float32)
    started = time.perf_counter()
    done_passes = 0
    cancelled = False

    with film_mod.FilmWriter.create(film_path, width, height) as writer:
        writer.set_state(film_mod.STATE_RENDERING)
        for k in range(1, passes + 1):
            if should_cancel is not None and should_cancel():
                cancelled = True
                break

            image = np.array(mi.render(scene, spp=spp_per_pass, seed=seed + k),
                             dtype=np.float32)
            if image.shape[:2] != (height, width):
                raise ValueError(
                    f"renderer produced {image.shape[:2]}, film is {(height, width)}"
                )
            image = _to_rgba(image)

            accumulator += (image - accumulator) / float(k)
            done_passes = k
            spp_done = k * spp_per_pass
            writer.write(accumulator, passes_done=k, spp_done=spp_done)
            if on_pass is not None:
                on_pass(k, spp_done, time.perf_counter() - started)

        writer.set_state(film_mod.STATE_DONE)

    return RenderResult(
        image=accumulator,
        passes_done=done_passes,
        spp_done=done_passes * spp_per_pass,
        elapsed_s=time.perf_counter() - started,
        cancelled=cancelled,
    )


def _to_rgba(image: F32) -> F32:
    """Normalise a rendered frame to four channels.

    `hdrfilm` with `pixel_format="rgba"` already gives four, but an integrator or film
    override can produce three, and silently indexing a 3-channel array as 4 raises
    somewhere much less informative than here.
    """
    channels = image.shape[2] if image.ndim == 3 else 1
    if channels == film_mod.CHANNELS:
        return image
    out = np.zeros((image.shape[0], image.shape[1], film_mod.CHANNELS), dtype=np.float32)
    if channels == 3:
        out[..., :3] = image
        out[..., 3] = 1.0
        return out
    if channels == 1:
        out[..., :3] = image[..., :1]
        out[..., 3] = 1.0
        return out
    raise ValueError(f"cannot map a {channels}-channel frame onto RGBA")
