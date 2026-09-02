"""
ui/pages/settings_page.py — Application settings page.

Only app-wide settings live here (currently: theme). Everything related to
the 3D sim widget (viewport background, tool/material colors, voxel/stock
settings, display toggles) lives in the sim widget's own overlay panel
(sim/ui/overlay/panels/sim_panel.py) instead — keeping sim-specific settings
next to the sim widget they affect, rather than scattered across a separate
global page.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    """Application settings page embedded in the main window stack.

    Args:
        theme_manager:  The application ThemeManager instance.
    """

    def __init__(
        self,
        theme_manager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._theme_combo: QComboBox | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        root.addWidget(_section_label("Appearance"))

        if self._theme_manager is not None:
            theme_form = QFormLayout()
            theme_form.setSpacing(10)

            self._theme_combo = QComboBox()
            for key in theme_manager.available_themes():
                self._theme_combo.addItem(theme_manager.display_name(key), userData=key)

            # Pre-select current theme
            current = theme_manager.current_theme
            for i in range(self._theme_combo.count()):
                if self._theme_combo.itemData(i) == current:
                    self._theme_combo.setCurrentIndex(i)
                    break

            theme_form.addRow("Theme", self._theme_combo)
            root.addLayout(theme_form)

            self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        else:
            root.addWidget(QLabel(
                "Theme manager not available.",
                styleSheet="color: #8fa0ba; font-size: 12px;",
            ))

        root.addStretch()

    def _on_theme_changed(self, index: int) -> None:
        key = self._theme_combo.itemData(index)
        if key:
            self._theme_manager.apply_theme(key)


def _section_label(text: str) -> QLabel:
    """Styled section heading label."""
    lbl = QLabel(text)
    lbl.setObjectName("CardTitle")
    return lbl
