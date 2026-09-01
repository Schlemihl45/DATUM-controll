"""
sim/gcode/motion_planner.py — Converts GCodeCommands into typed motion segments.

Segment types:
  LinearSegment  — G0/G1 straight-line moves
  ArcSegment     — G2/G3 planar arcs (same Z start/end)
  HelixSegment   — G2/G3 with Z delta (helical interpolation)

The planner maintains a ModalState (current motion mode, absolute/relative,
feed rate, and position) across commands so that partial blocks (e.g. just
"X10" after a prior "G1") correctly inherit the active motion code.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from controller.sim.gcode.parser import GCodeCommand

# ── Segment types ────────────────────────────────────────────────────────────

@dataclass
class LinearSegment:
    """G0 (rapid) or G1 (feed) straight-line move."""
    line_index: int
    start:      np.ndarray
    end:        np.ndarray
    feed_rate:  float
    is_rapid:   bool          # True = G0


@dataclass
class ArcSegment:
    """G2/G3 planar arc with constant Z."""
    line_index: int
    start:      np.ndarray
    end:        np.ndarray
    center:     np.ndarray
    radius:     float
    clockwise:  bool          # True = G2
    feed_rate:  float


@dataclass
class HelixSegment:
    """G2/G3 arc with Z travel — helical interpolation."""
    line_index: int
    start:      np.ndarray
    end:        np.ndarray
    center:     np.ndarray
    radius:     float
    pitch:      float         # Z travel per full revolution
    clockwise:  bool
    feed_rate:  float


# Union type alias for type checkers
MotionSegment = LinearSegment | ArcSegment | HelixSegment

# ── Modal state ──────────────────────────────────────────────────────────────

class ModalState:
    """Persistent interpreter state carried between G-code blocks.

    Mirrors a real controller's modal groups: active motion code (G0/1/2/3),
    distance mode (G90/G91), programmed feed rate, and current position.
    """

    def __init__(self):
        self.motion:   int         = 0            # Active motion: 0=rapid,1=linear,2=CW arc,3=CCW arc
        self.absolute: bool        = True          # G90=True, G91=False
        self.feed_rate: float      = 0.0           # mm/min
        self.position: np.ndarray  = np.zeros(3)   # current XYZ in machine coords

    def resolve_target(self, parameters: dict[str, float]) -> np.ndarray:
        """Compute the target XYZ from the command parameters and current mode."""
        target = self.position.copy()
        for i, axis in enumerate("XYZ"):
            if axis in parameters:
                if self.absolute:
                    target[i] = parameters[axis]
                else:
                    target[i] = self.position[i] + parameters[axis]
        return target

# ── Public planner entry point ───────────────────────────────────────────────

def plan(commands: list[GCodeCommand]) -> list[MotionSegment]:
    """Convert a list of parsed G-code commands into motion segments.

    Processes G-code modally: later commands inherit the active motion code,
    feed rate, and position from earlier blocks. Non-motion commands (spindle,
    coolant, M-codes) are silently skipped.

    Returns a flat list of typed motion segments ready for PathBuffer.
    """
    modal = ModalState()
    segments: list[MotionSegment] = []

    for cmd in commands:
        # Update modal state from this block's G/M codes
        for g in cmd.g_codes:
            if g in (0, 1, 2, 3):  modal.motion   = g
            elif g == 90:           modal.absolute  = True
            elif g == 91:           modal.absolute  = False

        if "F" in cmd.parameters:
            modal.feed_rate = cmd.parameters["F"]

        # Skip blocks with no axis or arc offset words
        has_motion = any(k in cmd.parameters for k in "XYZ")
        has_arc    = any(k in cmd.parameters for k in ("I", "J", "K"))
        if not has_motion and not has_arc:
            continue

        prev   = modal.position.copy()
        target = modal.resolve_target(cmd.parameters)

        seg = _build_segment(cmd, modal, prev, target)
        if seg is not None:
            segments.append(seg)

        modal.position = target

    return segments


def _build_segment(
    cmd: GCodeCommand, modal: ModalState, prev: np.ndarray, target: np.ndarray
) -> MotionSegment | None:
    """Dispatch to the correct segment builder based on active motion code."""
    if modal.motion in (0, 1):
        return LinearSegment(
            line_index=cmd.line_index,
            start=prev,
            end=target,
            feed_rate=modal.feed_rate,
            is_rapid=(modal.motion == 0),
        )
    if modal.motion in (2, 3):
        return _build_arc_or_helix(cmd, modal, prev, target)
    return None


def _build_arc_or_helix(
    cmd: GCodeCommand, modal: ModalState, prev: np.ndarray, target: np.ndarray
) -> ArcSegment | HelixSegment | None:
    """Build an ArcSegment (flat) or HelixSegment (with Z travel).

    I/J/K offsets specify the arc center relative to the current position.
    """
    params = cmd.parameters

    i = params.get("I", 0.0)
    j = params.get("J", 0.0)
    k = params.get("K", 0.0)

    # Center is prev + offset vector
    center    = prev.copy()
    center[0] += i
    center[1] += j
    center[2] += k

    # Radius is the XY distance from prev to center
    r_vec  = prev - center
    radius = float(np.linalg.norm(r_vec[:2]))

    clockwise = (modal.motion == 2)
    z_delta   = float(target[2] - prev[2])

    if abs(z_delta) < 1e-9:
        # Pure planar arc
        return ArcSegment(
            line_index=cmd.line_index,
            start=prev, end=target,
            center=center, radius=radius,
            clockwise=clockwise,
            feed_rate=modal.feed_rate,
        )
    else:
        # Helical arc — compute pitch from angle swept
        a_start = np.arctan2(prev[1] - center[1],   prev[0] - center[0])
        a_end   = np.arctan2(target[1] - center[1], target[0] - center[0])

        if clockwise:
            if a_end >= a_start:
                a_end -= 2 * np.pi
        else:
            if a_end <= a_start:
                a_end += 2 * np.pi

        total_angle  = abs(a_end - a_start)
        revolutions  = total_angle / (2 * np.pi)
        pitch        = z_delta / revolutions if revolutions > 1e-9 else 0.0

        return HelixSegment(
            line_index=cmd.line_index,
            start=prev, end=target,
            center=center, radius=radius,
            clockwise=clockwise,
            feed_rate=modal.feed_rate,
            pitch=pitch,
        )
