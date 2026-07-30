"""Unit tests for the session-scoped worker handle.

These do not launch a real worker — they only check that `shared_worker` reuses a live
client and that `shutdown_shared` is safe when nothing is running. Spawning Mitsuba is
`tools/smoke_worker.py`'s job.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import max_side.client as client_mod


def setup_function() -> None:
    client_mod.shutdown_shared(timeout=0.1)


def teardown_function() -> None:
    client_mod.shutdown_shared(timeout=0.1)


def test_shutdown_shared_is_idempotent() -> None:
    client_mod.shutdown_shared()
    client_mod.shutdown_shared()


def test_shared_worker_reuses_a_live_client(tmp_path: Path) -> None:
    first = MagicMock()
    first.interpreter = "python"
    first.project_root = tmp_path
    first.variant = "auto"
    first.is_running = True

    with patch.object(client_mod, "WorkerClient", return_value=first) as ctor:
        a = client_mod.shared_worker("python", tmp_path, variant="auto")
        b = client_mod.shared_worker("python", tmp_path, variant="auto")

    assert a is first
    assert b is first
    ctor.assert_called_once()
    first.start.assert_called_once()
    first.shutdown.assert_not_called()


def test_shared_worker_restarts_when_the_previous_died(tmp_path: Path) -> None:
    dead = MagicMock()
    dead.interpreter = "python"
    dead.project_root = tmp_path
    dead.variant = "auto"
    dead.is_running = False

    alive = MagicMock()
    alive.interpreter = "python"
    alive.project_root = tmp_path
    alive.variant = "auto"
    alive.is_running = True

    with patch.object(client_mod, "WorkerClient", side_effect=[dead, alive]):
        first = client_mod.shared_worker("python", tmp_path)
        assert first is dead
        dead.is_running = False
        second = client_mod.shared_worker("python", tmp_path)

    assert second is alive
    dead.shutdown.assert_called_once()
