from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class MachineState(Enum):
    """
    Represents the linux-cnc task_state values.
    Documentation: linuxcnc.STATE_ESTOP, STATE_ON, etc.
    """
    ESTOP = auto()
    ESTOP_RESET = auto()
    OFF = auto()
    ON = auto()

class ProgramState(Enum):
    """
    State of the running cnc program.
    """
    IDLE = auto()
    RUNNING = auto()
    ERROR = auto()
    PAUSED = auto()

class JobStatus(Enum):
    """
    Status of a job in the job queue
    """
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()

class ErrorSeverity(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()
# ---------------------------------------------------------------------------
# Load Values
# ---------------------------------------------------------------------------
@dataclass
class Load:
    """
    Generic load reading — both percentage (relative to rated
    capacity) and raw torque, wherever the source (VFD, servo drive)
    provides both. Which field is relevant is decided per use case,
    not here.
    """
    percent: float = 0.0       # 0-100, bezogen auf Nennlast
    torque_nm: float = 0.0     # 0.0, falls die Quelle kein Drehmoment liefert

@dataclass
class AxisLoads:
    """Per-Achse-Lastmomentaufnahme, ein Load je Linearachse."""
    x: Load = field(default_factory=Load)
    y: Load = field(default_factory=Load)
    z: Load = field(default_factory=Load)
# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """
    Current machine position in mm

    x, y, z: linear axis
    a, b, c: rotation axis (optional, 0.0 if not required)

    Gets called by MachineController.get_position()
    Always in machine coordinates, workpiece coordinates are seperated.
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """For JSON serialization in the API"""
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "a": self.a,
            "b": self.b,
            "c": self.c,
        }

# ---------------------------------------------------------------------------
# FeedData
# ---------------------------------------------------------------------------
@dataclass
class FeedData:
    """
    Current feedrate and spindle data
    Updated every 50ms by the controller
    """
    feed_actual: float = 0.0
    spindle_rpm: float = 0.0
    feed_override: float = 1.0
    spindle_load: Load = field(default_factory=Load)

    def feed_override_percent(self) -> int:
        return round(self.feed_override * 100)

# ---------------------------------------------------------------------------
# Machine Error
# ---------------------------------------------------------------------------
@dataclass
class MachineError:
    """
    Structured Error messages from backend or controller

    """
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    source: str="Machine"

# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    """
    Represents a tool from the tool bib

    """
    # LinuxCNC tool.tbl
    number: int  # Tool-Nummer (T1, T2...)
    pocket: int = 0  # P-Nummer
    diameter: float = 0.0  # D
    offset_x: float = 0.0  # X-Offset
    offset_y: float = 0.0  # Y-Offset
    offset_z: float = 0.0  # Z-Offset (Lenght correction)
    offset_a: float = 0.0  # A-Offset
    offset_b: float = 0.0  # B-Offset
    offset_c: float = 0.0  # C-Offset

    # custom
    description: str = ""
    material: str = ""
    corner_type: str = ""
    corner_radius: float = 0.0
    corner_chamfer: float = 0.0
    cutting_length: float = 0.0
    flute_count: int = 0
    life_used: float = 0.0
    life_max: float = 0.0

    @property
    def needs_replacement(self) -> bool:
        if self.life_max == 0:
            return False
        return self.life_used >= self.life_max

    @property
    def life_percentage(self) -> float:
        if self.life_max == 0:
            return 0.0
        return min(self.life_used / self.life_max, 1.0)

# ---------------------------------------------------------------------------
# Operation - a single gcode file/setup
# ---------------------------------------------------------------------------
@dataclass
class Operation:
    """
    Smallest executable unit of a workpiece.

    Every setup is an operation:
        Op 1: Mill side A
        Op 2: Mill side B
        Op 3: Chamfer corners
    """
    id: int
    workpiece_id: int
    name: str
    gcode_path: str = ""
    tools: list[int] = field(default_factory=list)
    clamping_description: str = ""
    zero_point_notes: str = ""
    notes: str = ""
    estimated_time: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_ready(self) -> bool:
        return bool(self.gcode_path) and len(self.tools) >0

    @property
    def gcode_filename(self) -> str:
        from pathlib import Path
        return Path(self.gcode_path).name if self.gcode_path else ""

    def add_tool(self, number: int) -> None:
        if number not in self.tools:
            self.tools.append(number)

    def remove_tool(self, number: int) -> None:
        self.tools = [t for t in self.tools if t != number]

# ---------------------------------------------------------------------------
# Workpiece - the complete physical part with all operations
# ---------------------------------------------------------------------------
@dataclass
class Workpiece:
    """
    A workpiece: base data + operations

    Progressive disclosure in the ui:
        1 operation -> UI shows only Workpiece + Gcode
        n operations -> UI shows complete operations list

    When created an operation is created automatically.
    """
    name: str
    id: int | None = None
    material: str = ""
    description: str = ""
    drawing_number: str = ""
    operations: list[Operation] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def operation_count(self) -> int:
        return len(self.operations)

    @property
    def is_simple(self) -> bool:
        return self.operation_count == 1

    @property
    def is_ready(self) -> bool:
        return len(self.operations) > 0 and all(op.is_ready for op in self.operations)

    def add_operation(self, operation: Operation) -> None:
        self.operations.append(operation)

    def remove_operation(self, op_id: int) -> None:
        self.operations = [op for op in self.operations if op.id != op_id]

    def get_operation(self, op_id: int) -> Operation | None:
        return next((op for op in self.operations if op.id == op_id), None)

@dataclass
class Job:
    """
    A Production Job: A workpiece gets created n-times

    quantity_total
    quantity_done
    current_operation_index

    complete_operation() gets total_ops as a parameter, it never accesses the workpiece
    """

    workpiece_id: int
    quantity_total: int
    id: int | None = None
    quantity_done: int = 0
    current_operation_index: int = 0
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def start(self) -> None:
        if self.status is JobStatus.PENDING:
            self.status = JobStatus.RUNNING
            self.started_at = datetime.now()

    def complete_operation(self, total_ops) -> None:
        if total_ops <= 0:
            return
        next_index = self.current_operation_index + 1
        if next_index >= total_ops:
            self.quantity_done += 1
            self.current_operation_index = 0
            if self.quantity_done >= self.quantity_total:
                self.status = JobStatus.DONE
                self.finished_at = datetime.now()
        else:
            self.current_operation_index = next_index

    def cancel(self) -> None:
        if self.status in (JobStatus.PENDING, JobStatus.RUNNING):
            self.status = JobStatus.CANCELLED

    def fail(self) -> None:
        self.status = JobStatus.FAILED

    @property
    def progress(self) -> float:
        if self.quantity_total <= 0:
            return 0.0
        return self.quantity_done / self.quantity_total

    @property
    def is_done(self) -> bool:
        return self.status == JobStatus.DONE

    def __repr__(self) -> str:
        return (
            f"Job(WP#{self.workpiece_id} "
            f"{self.quantity_done}/{self.quantity_total} "
            f"{self.status.name})"
        )
