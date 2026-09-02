"""
ui/widgets/tool_info_card.py — Active tool info.

Displays the tool datum_sim's DatumSimWidget currently has active —
i.e. the tool called out by the running G-code program, whether that's
driven by SIM-mode playback or a real MACHINE-mode run (see
DatumSimWidget.tool_changed, wired up in MachinePage). Uses
sim.simulation.tool_definition.ToolDefinition, the tool type datum_sim
actually has data for today — not domain.models.Tool, which nothing in
the codebase populates (no ToolRepository exists yet).
"""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from controller.sim.simulation.tool_definition import ToolDefinition
from controller.ui.widgets.card import Card


class ToolInfoCard(Card):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(title="Tool", parent=parent)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.content_layout.addLayout(grid)

        self._name_label = QLabel("No tool loaded")
        self._name_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("Name"), 0, 0)
        grid.addWidget(self._name_label, 0, 1)

        self._diameter_label = QLabel("—")
        self._diameter_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("Ø"), 1, 0)
        grid.addWidget(self._diameter_label, 1, 1)

        self._material_label = QLabel("—")
        self._material_label.setObjectName("InfoValueSecondary")
        grid.addWidget(QLabel("Material"), 2, 0)
        grid.addWidget(self._material_label, 2, 1)

    def set_tool(self, tool: ToolDefinition | None) -> None:
        if tool is None:
            self._name_label.setText("No tool loaded")
            self._diameter_label.setText("—")
            self._material_label.setText("—")
            return
        # ToolDefinition has no dedicated "name" field — fall back to the
        # T-number when no free-text remark was entered.
        self._name_label.setText(tool.remark or f"T{tool.tool_number}")
        self._diameter_label.setText(f"{tool.diameter} mm")
        self._material_label.setText(tool.material or "—")