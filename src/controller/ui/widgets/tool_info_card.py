"""
ui/widgets/tool_info_card.py — Active tool info. Placeholder until the
tool database (repository) exists — set_tool(None) is the current
permanent state; set_tool(tool) will be wired up once ToolRepository
is built.
"""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from controller.domain.models import Tool
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

    def set_tool(self, tool: Tool | None) -> None:
        """tool: domain.models.Tool | None — wired up once the tool
        repository exists. For now, always called with None."""
        if tool is None:
            self._name_label.setText("No tool loaded")
            self._diameter_label.setText("—")
            self._material_label.setText("—")
            return
        # Tool has no dedicated "name" field — fall back to the T-number
        # when no free-text description was entered.
        self._name_label.setText(tool.description or f"T{tool.number}")
        self._diameter_label.setText(f"{tool.diameter} mm")
        self._material_label.setText(tool.material or "—")