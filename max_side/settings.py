"""Persisted configuration, in `%LOCALAPPDATA%\\mitsuba-max\\settings.json`.

Kept out of the Max scene file on purpose: the interpreter path and the chosen variant are
properties of the *machine*, not of the artwork, and a scene emailed to a colleague must
not carry a path that only exists on the sender's disk.

Everything here is plain JSON with defaults, so a corrupt or partial file degrades to
defaults with a warning rather than blocking startup — losing a settings file must never
be the reason someone cannot render.
"""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = ["Settings", "config_dir", "load", "save"]


def config_dir() -> Path:
    """`%LOCALAPPDATA%\\mitsuba-max`, or the home directory if that is somehow unset."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "mitsuba-max"


@dataclass(slots=True)
class Settings:
    interpreter: str = ""
    """Absolute path to the worker's `python.exe`. Empty means "not configured yet"."""

    mitsuba_version: str = ""
    """Cached from the last successful validation, for display and for support requests."""

    variant: str = "auto"
    """`auto` / `cuda_ad_rgb` / `llvm_ad_rgb` / `scalar_rgb`.

    `scalar_rgb` is genuinely useful for debugging determinism, which is why it is an
    exposed choice rather than an internal fallback.
    """

    spp_per_pass: int = 16
    passes: int = 32
    max_depth: int = 8
    rr_depth: int = 5
    integrator: str = "path"

    resolution_scale: float = 1.0
    """1.0 / 0.5 / 0.25 — the half and quarter resolution toggles for interactive work."""

    luminous_efficacy: float = 250.0
    """lm/W. Approximates the spectral integral an RGB renderer cannot evaluate; see
    `core.units`. Exposed because the right value depends on the light's SPD."""

    exposure: float = 0.0
    """Display exposure in stops. Applied host-side to the cached buffer, never baked."""

    gamma: float = 2.2

    export_root: str = ""
    """Where meshes, textures and scene.xml are written. Empty means a temp directory."""

    warn_on_unsupported: bool = True

    extra: dict[str, Any] = field(default_factory=dict)
    """Forward compatibility: keys written by a newer build survive a round trip through an
    older one instead of being silently dropped."""

    def path(self) -> Path:
        return config_dir() / "settings.json"


def load() -> Settings:
    path = config_dir() / "settings.json"
    if not path.is_file():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Deliberately not fatal. A corrupt settings file is annoying; a plugin that will
        # not load because of one is a support ticket.
        return Settings()
    if not isinstance(raw, dict):
        return Settings()

    known = {f.name for f in fields(Settings)} - {"extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    extra = {k: v for k, v in raw.items() if k not in known}
    settings = Settings(**kwargs)
    settings.extra = extra
    return settings


def save(settings: Settings) -> Path:
    path = config_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    data.pop("extra", None)
    data.update(settings.extra)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path
