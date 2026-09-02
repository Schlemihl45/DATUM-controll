"""
sim/ui/overlay/settings_panel.py — Right-edge slide-out settings panel.

A narrow strip of checkable CardButtons — one per settings section
(Darstellung/Optik/Simulation/Rohteil, see sim_panel.py) — toggles a wider
content panel open/closed, jumping straight to that section instead of
opening to a single generic tab first.

Both the strip and the panel use objectName="Card" with a "sim_overlay"
variant (see ui/resources/styles/{dark,light}.qss) so their background
follows the active app theme instead of a fixed hard-coded gray.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controller.sim.ui.overlay.panels.sim_panel import SECTION_ICONS, build_sections
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton

# ── Layout constants ──────────────────────────────────────────────────────────
STRIP_W = 48    # width of the tab-button strip
PANEL_W = 320   # width of the expanded content panel


class SettingsPanel(QWidget):
    """Overlay settings panel anchored to the right edge of DatumSimWidget.

    A narrow strip of icon buttons, one per settings section, serves as
    tabs. Clicking a tab toggles the wider content panel open or closed,
    showing that section directly. Clicking the active tab closes the panel.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(STRIP_W)
        self._active     = -1
        self._panel_open = False

        # ── Tab-button strip ──────────────────────────────────────────────────
        self._strip = QFrame(self)
        self._strip.setObjectName("Card")
        self._strip.setProperty("variant", "sim_overlay")
        self._strip.setFixedWidth(STRIP_W)

        self._strip_layout = QVBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(4, 8, 4, 8)
        self._strip_layout.setSpacing(4)
        self._strip_layout.setAlignment(Qt.AlignTop)

        # ── Content panel ─────────────────────────────────────────────────────
        self._panel = QFrame(self)
        self._panel.setObjectName("Card")
        self._panel.setProperty("variant", "sim_overlay")
        self._panel.setFixedWidth(PANEL_W)
        self._panel.hide()

        self._stack = QStackedWidget(self._panel)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self._stack)

        # ── One tab per settings section ────────────────────────────────────
        self._tabs: list[CardButton] = []
        sections = build_sections(self._stack)
        for (icon_name, tooltip), widget in zip(SECTION_ICONS, sections):
            self._add_tab(icon_name, tooltip, widget)
        self._strip_layout.addStretch()

    # ── Tab management ────────────────────────────────────────────────────────

    def _add_tab(self, icon_name: str, tooltip: str, content: QWidget) -> None:
        index = len(self._tabs)

        btn = CardButton(icon=get_icon(icon_name, tint=True, size=QSize(22, 22)),
                          icon_size=22, parent=self._strip)
        btn.setToolTip(tooltip)
        btn.setFixedSize(40, 40)
        btn.setCheckable(True)
        btn.setProperty("variant", "sim_nav")
        btn.clicked.connect(lambda _=False, i=index: self._on_tab_clicked(i))

        self._strip_layout.insertWidget(index, btn)
        self._tabs.append(btn)
        self._stack.addWidget(content)

    def _on_tab_clicked(self, index: int) -> None:
        if self._panel_open and self._active == index:
            self._close_panel()
        else:
            self._active = index
            self._stack.setCurrentIndex(index)
            for i, btn in enumerate(self._tabs):
                btn.setChecked(i == index)
            if not self._panel_open:
                self._open_panel()

    def _open_panel(self) -> None:
        self._panel_open = True
        self.setFixedWidth(STRIP_W + PANEL_W)
        self._panel.show()
        self._relayout()
        if self.parent():
            self.parent()._layout_overlays()

    def _close_panel(self) -> None:
        self._panel_open = False
        self._active     = -1
        for btn in self._tabs:
            btn.setChecked(False)
        self._panel.hide()
        self.setFixedWidth(STRIP_W)
        if self.parent():
            self.parent()._layout_overlays()

    def _relayout(self) -> None:
        h = self.height()
        self._strip.setGeometry(0, 0, STRIP_W, h)
        if self._panel_open:
            self._panel.setGeometry(STRIP_W, 0, PANEL_W, h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()
