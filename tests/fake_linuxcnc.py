"""
tests/fake_linuxcnc.py — Minimal stand-in for the real `linuxcnc` Python
module.

The real package isn't installed here — it ships with a LinuxCNC
installation, it's not a PyPI dependency — so test_linuxcnc_backend.py
injects this module into sys.modules under the name "linuxcnc" before
importing controller.core.backends.linuxcnc, so that module's own
`import linuxcnc` resolves to this fake instead of failing.

Constant values are arbitrary, distinct sentinels, NOT the real
LinuxCNC values. LinuxCNCBackend only ever compares against these
symbolic names (e.g. `stat.task_state == linuxcnc.STATE_ON`), never a
hardcoded literal — so the exact numbers don't matter for verifying
LinuxCNCBackend's structural correctness. The real `linuxcnc` module
supplies the real values once code actually runs against LinuxCNC (see
Schritt 7 in the task — deliberately out of scope here).
"""

from __future__ import annotations

import itertools

_counter = itertools.count(1)


def _const() -> int:
    return next(_counter)


# --- Task states (stat.task_state) ---
STATE_ESTOP = _const()
STATE_ESTOP_RESET = _const()
STATE_OFF = _const()
STATE_ON = _const()

# --- Interpreter states (stat.interp_state) ---
INTERP_IDLE = _const()
INTERP_READING = _const()
INTERP_PAUSED = _const()
INTERP_WAITING = _const()

# --- Task modes (stat.task_mode / cmd.mode()) ---
MODE_MANUAL = _const()
MODE_AUTO = _const()
MODE_MDI = _const()

# --- cmd.auto() sub-modes ---
AUTO_RUN = _const()
AUTO_PAUSE = _const()
AUTO_RESUME = _const()
AUTO_STEP = _const()

# --- cmd.jog() sub-modes ---
JOG_STOP = _const()
JOG_CONTINUOUS = _const()
JOG_INCREMENT = _const()

# --- Coolant (stat.flood / stat.mist, cmd.flood() / cmd.mist()) ---
FLOOD_OFF = _const()
FLOOD_ON = _const()
MIST_OFF = _const()
MIST_ON = _const()

# --- Spindle (cmd.spindle()) ---
SPINDLE_FORWARD = _const()
SPINDLE_REVERSE = _const()
SPINDLE_OFF = _const()

# --- Spindle brake (cmd.brake()) ---
BRAKE_ENGAGE = _const()
BRAKE_RELEASE = _const()

# --- error_channel.poll() message kinds ---
NML_ERROR = _const()
NML_TEXT = _const()
OPERATOR_ERROR = _const()
OPERATOR_TEXT = _const()


class error(Exception):
    """Stand-in for linuxcnc.error — raised by stat.poll()/error_channel.poll()
    when the NML connection is down."""


def _unused(*_args, **_kwargs):
    raise RuntimeError(
        "fake_linuxcnc: this factory should never be called directly in "
        "tests — pass stat_factory/command_factory/error_factory to "
        "LinuxCNCBackend instead so a MagicMock is injected."
    )


# LinuxCNCBackend only calls these three as constructor defaults, which
# tests always override — see LinuxCNCBackend.__init__.
stat = _unused
command = _unused
error_channel = _unused
