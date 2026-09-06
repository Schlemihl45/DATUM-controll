"""
ui/widgets/tool_usage_card.py — ToolUsageCard: one pocket referenced by a
G-code T-address, rendered as a read-only card.

Built ONCE here and used by BOTH ui/pages/workpiece_detail_page.py's
aggregated tool accordion and ui/pages/program_detail_page.py's
per-operation tool section — two divergent copies of essentially the same
card would otherwise need keeping in sync by hand every time either page
changed.

A T-address in G-code selects a MAGAZINE POCKET, not a persistent
ToolDefinition.tool_number identity (see gcode/compiler.py's
ToolChange.pocket_number docstring — MachinePage's own Start-button
validation already relies on this distinction via get_tool_by_pocket) —
this card resolves/displays via ToolDatabase.get_tool_by_pocket(), never
get_tool().
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget

from controller.persistence.tool_db import ToolDatabase, ToolDatabaseSignals
from controller.sim.simulation.tool_definition import ToolType
from controller.ui.widgets.card import Card
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.tool_icons import tool_type_icon


class ToolUsageCard(Card):
    """One pocket: type icon + pocket badge + name, and a read-only "im
    Magazin" checkbox in place of a settings button — deliberately a
    disabled QCheckBox (setEnabled(False)), not an interactive control:
    there is no user action here that would write anything (the magazine
    status comes exclusively from ToolDatabase), so a clickable checkbox
    would suggest an interaction that doesn't exist.

    Subscribes to ToolDatabaseSignals ITSELF and refreshes its own state
    on every change — callers never need to wire that up per page (see
    module docstring)."""

    def __init__(self, pocket: int, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._pocket = pocket
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout()
        row.setSpacing(10)

        self._type_icon_lbl = QLabel()
        self._type_icon_lbl.setFixedSize(40, 40)
        row.addWidget(self._type_icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._pocket_badge = QLabel(str(pocket))
        self._pocket_badge.setObjectName("PocketBadge")
        self._pocket_badge.setFixedSize(30, 22)
        self._pocket_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._pocket_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = ElidedLabel()
        self._name_lbl.setObjectName("ToolCardButtonLabel")
        row.addWidget(self._name_lbl, stretch=1)

        self._magazine_chk = QCheckBox("im Magazin")
        self._magazine_chk.setEnabled(False)
        row.addWidget(self._magazine_chk, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.content_layout.addLayout(row)

        ToolDatabaseSignals.instance().tool_changed.connect(self._on_tool_changed)
        self.refresh()

    @property
    def pocket(self) -> int:
        return self._pocket

    def refresh(self) -> None:
        tool = ToolDatabase.instance().get_tool_by_pocket(self._pocket)
        occupied = tool is not None

        tool_type = tool.tool_type if tool is not None else ToolType.ENDMILL
        icon_size = QSize(40, 40)
        self._type_icon_lbl.setPixmap(tool_type_icon(tool_type, size=40).pixmap(icon_size))

        if tool is not None:
            self._name_lbl.set_full_text(tool.name or tool.remark or f"Pocket {self._pocket}")
        else:
            self._name_lbl.set_full_text(f"Pocket {self._pocket} — leer")

        self._magazine_chk.setChecked(occupied)
        # Empty string (not None) so the QSS attribute selector reliably
        # stops matching — see dark.qss/light.qss's
        # QFrame#Card[variant="in_magazine"] rule (same colours as the
        # Start button's own "start" variant, reused deliberately, not a
        # new green). Absence of the magazine state is the NORMAL look,
        # not a warning — no separate "not in magazine" colour.
        self.setProperty("variant", "in_magazine" if occupied else "")
        self.style().unpolish(self)
        self.style().polish(self)

    def _on_tool_changed(self, _tool_number: int) -> None:
        self.refresh()
