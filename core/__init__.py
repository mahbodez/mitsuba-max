"""Renderer- and host-agnostic core of mitsuba-max.

Pure CPython. This package imports neither `pymxs` nor `mitsuba`; see `core/CLAUDE.md`.
It defines the intermediate representation that `max_side` produces and `worker` consumes,
plus everything that can be tested without either application running.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
