"""
ui/pages/settings_page.py — Application settings page.

A QTabWidget with one tab for now:

  General
  ├─ Theme switcher    (dark / light .qss, via ThemeManager)
  └─ Sim background    (color picker → SimSettings.bg_color)

Additional tabs will be added as features grow. Each tab is a self-contained
QWidget so they can be moved to separate files without touching this module.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ── General Tab ───────────────────────────────────────────────────────────────

class _GeneralTab(QWidget):
    """General application settings (theme + sim background color)."""

    def __init__(
        self,
        theme_manager,              # ThemeManager | None
        sim_settings=None,          # AppSettings instance or None if sim unavailable
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._sim_settings  = sim_settings
        self._theme_combo: QComboBox | None = None   # only set when theme_manager is not None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        # ── Theme ─────────────────────────────────────────────────────────────
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

        # ── Sim background ────────────────────────────────────────────────────
        root.addWidget(_section_label("3D Viewport"))

        if self._sim_settings is not None:
            bg_row = QHBoxLayout()
            bg_row.setSpacing(10)

            self._bg_swatch = _ColorSwatch(self._sim_settings.bg_color)
            self._bg_btn    = QPushButton("Choose…")
            self._bg_btn.setFixedWidth(90)
            self._bg_btn.clicked.connect(self._pick_bg_color)

            bg_row.addWidget(self._bg_swatch)
            bg_row.addWidget(QLabel("Background color"))
            bg_row.addStretch()
            bg_row.addWidget(self._bg_btn)

            bg2_row = QHBoxLayout()
            bg2_row.setSpacing(10)

            self._bg2_swatch = _ColorSwatch(self._sim_settings.bg_color_2)
            self._bg2_btn    = QPushButton("Choose…")
            self._bg2_btn.setFixedWidth(90)
            self._bg2_btn.clicked.connect(self._pick_bg2_color)

            bg2_row.addWidget(self._bg2_swatch)
            bg2_row.addWidget(QLabel("Background gradient outer"))
            bg2_row.addStretch()
            bg2_row.addWidget(self._bg2_btn)

            sim_form = QFormLayout()
            sim_form.setSpacing(10)
            sim_form.addRow("", bg_row)
            sim_form.addRow("", bg2_row)
            root.addLayout(sim_form)
        else:
            root.addWidget(QLabel(
                "3D simulation not available (moderngl not installed).",
                styleSheet="color: #8fa0ba; font-size: 12px;"
            ))

        root.addStretch()

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def _on_theme_changed(self, index: int) -> None:
        key = self._theme_combo.itemData(index)
        if key:
            self._theme_manager.apply_theme(key)

    def _pick_bg_color(self) -> None:
        if self._sim_settings is None:
            return
        initial = QColor(self._sim_settings.bg_color)
        color   = QColorDialog.getColor(initial, self, "Background Color")
        if color.isValid():
            hex_val = color.name()
            self._sim_settings.bg_color = hex_val
            self._bg_swatch.set_color(hex_val)

    def _pick_bg2_color(self) -> None:
        if self._sim_settings is None:
            return
        initial = QColor(self._sim_settings.bg_color_2)
        color   = QColorDialog.getColor(initial, self, "Background Gradient Outer")
        if color.isValid():
            hex_val = color.name()
            self._sim_settings.bg_color_2 = hex_val
            self._bg2_swatch.set_color(hex_val)


# ── Reusable helpers ──────────────────────────────────────────────────────────

class _ColorSwatch(QWidget):
    """Small colored rectangle showing a hex color."""

    def __init__(self, hex_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._color = hex_color
        self._update_style()

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background: {self._color}; border: 1px solid rgba(255,255,255,0.15);"
            f" border-radius: 4px;"
        )


def _section_label(text: str) -> QLabel:
    """Styled section heading label."""
    lbl = QLabel(text)
    lbl.setObjectName("CardTitle")
    return lbl


# ── SettingsPage ──────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    """Application settings page embedded in the main window stack.

    Instantiate this after ThemeManager is created and optionally after
    the sim package is confirmed importable (pass sim_settings=None if not).

    Args:
        theme_manager:  The application ThemeManager instance.
        sim_settings:   AppSettings singleton from controller.sim.core.settings,
                        or None when moderngl is unavailable.
    """

    def __init__(
        self,
        theme_manager,
        sim_settings=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.addTab(
            _GeneralTab(theme_manager, sim_settings, self),
            "General",
        )
        root.addWidget(self._tabs)
