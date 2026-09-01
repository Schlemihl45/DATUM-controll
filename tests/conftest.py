"""
Shared pytest fixtures. Requires the QT_QPA_PLATFORM=offscreen env var
in headless environments (CI, containers without a display) — set it
before running pytest, e.g.:

    QT_QPA_PLATFORM=offscreen pytest
"""

from __future__ import annotations

import pytest

from controller.core.backends.simulated import SimulatedBackend
from controller.core.machine.controller import MachineController


@pytest.fixture
def backend() -> SimulatedBackend:
    return SimulatedBackend()


@pytest.fixture
def controller(backend: SimulatedBackend) -> MachineController:
    ctrl = MachineController(backend=backend)
    # Tests call ctrl.poll_once() instead of starting the real 50ms
    # QTimer — deterministic and doesn't depend on wall-clock timing.
    return ctrl


@pytest.fixture
def machine_on(controller: MachineController, backend: SimulatedBackend) -> MachineController:
    """Controller + backend brought to a ready-to-run state (ON + homed)."""
    backend.estop_reset()
    backend.set_machine_on()
    backend.home_all()
    controller.poll_once()
    return controller
