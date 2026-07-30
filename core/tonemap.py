"""Linear float32 → display-ready 8-bit RGB.

Tone mapping happens on the host, from the cached float buffer, and never in the renderer.
That single decision is what lets the exposure and gamma sliders re-expose a *finished*
render instantly instead of re-rendering it — which does more for perceived quality than
any renderer optimisation, and is why it is in v1 rather than deferred.

numpy is required here and only here on the display path; see `max_side.numpy_bridge` for
how Max gets it, given that Max ships no numpy at all (probe 06c).
"""

import numpy as np
import numpy.typing as npt

__all__ = ["SRGB_GAMMA", "srgb_encode", "tonemap"]

F32 = npt.NDArray[np.float32]
U8 = npt.NDArray[np.uint8]

SRGB_GAMMA = 2.2
"""The gamma value that selects the true sRGB transfer function. See `tonemap`."""

_SRGB_LINEAR_CUTOFF = 0.0031308


def srgb_encode(linear: F32) -> F32:
    """The sRGB transfer function, piecewise as specified.

    Not a plain `x ** (1/2.2)`: sRGB is linear below 0.0031308 and a shifted power curve
    above it. The difference is confined to the darkest few percent, which is exactly where
    a render's noise and its shadow detail live, so approximating it crushes the part of the
    image people zoom into.
    """
    a = 0.055
    out = np.where(
        linear <= _SRGB_LINEAR_CUTOFF,
        linear * 12.92,
        (1.0 + a) * np.power(np.maximum(linear, 0.0), 1.0 / 2.4) - a,
    )
    return out.astype(np.float32)


def tonemap(pixels: F32, *, exposure: float = 0.0, gamma: float = SRGB_GAMMA) -> U8:
    """Linear RGB(A) float32 → contiguous uint8 RGB, ready for `QImage.Format_RGB888`.

    `exposure` is in **stops**, so +1 doubles the brightness — the unit photographers and
    compositors already think in, and the one the camera's own EV maps onto directly.

    `gamma` at its default selects the real sRGB curve, which is what a display expects.
    Any other value applies a plain `x ** (1/gamma)` instead, because someone reaching for
    1.0 or 1.8 wants a specific power curve and would be surprised to get a piecewise
    approximation of a different one.

    NaNs are mapped to black rather than propagating: a single NaN pixel from a degenerate
    BSDF would otherwise become an undefined uint8 and speckle the preview with noise that
    looks like a renderer bug.
    """
    if pixels.ndim != 3 or pixels.shape[2] < 3:
        raise ValueError(f"expected an (H, W, >=3) array, got {pixels.shape}")

    rgb = np.nan_to_num(pixels[..., :3].astype(np.float32, copy=False),
                        nan=0.0, posinf=0.0, neginf=0.0)
    rgb = rgb * np.float32(2.0 ** exposure)
    np.clip(rgb, 0.0, 1.0, out=rgb)

    if abs(gamma - SRGB_GAMMA) < 1e-9:
        encoded = srgb_encode(rgb)
    else:
        encoded = np.power(rgb, np.float32(1.0 / max(gamma, 1e-6)))

    return np.ascontiguousarray((encoded * 255.0 + 0.5).astype(np.uint8))
