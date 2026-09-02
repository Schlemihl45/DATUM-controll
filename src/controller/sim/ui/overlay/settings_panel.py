"""
sim/ui/overlay/settings_panel.py — Right-edge slide-out settings panel.

Contains a vertical strip of tab buttons that toggle a side panel open/closed.
Only one tab exists: "Simulation" (functional settings like path/tool display).

The "Viewport/Camera" tab was intentionally removed: background color and
camera speed settings now live in the application SettingsPage (General tab),
where they persist in app-level QSettings and are accessible from all pages.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from controller.sim.ui.overlay.panels.sim_panel import SimPanel

# ── Layout constants ──────────────────────────────────────────────────────────
STRIP_W = 48    # width of the tab-button strip
PANEL_W = 280   # width of the expanded content panel

# Icons live in the shared resources folder (parents[3] = src/controller/)
_ICONS_DIR = Path(__file__).resolve().parents[3] / "ui" / "resources" / "icons"

# ── Strip styles ──────────────────────────────────────────────────────────────
_FADE_STOP = f"{10 / STRIP_W:.4f}"   # 10px fade relative to strip width

_STRIP_CLOSED = f"""
QWidget {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(30, 30, 30, 0),
        stop:{_FADE_STOP} rgba(30, 30, 30, 220),
        stop:1 rgba(30, 30, 30, 220)
    );
    border-top-right-radius: 12px;
    border-bottom-right-radius: 12px;
}}
"""

_STRIP_OPEN = f"""
QWidget {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(30, 30, 30, 0),
        stop:{_FADE_STOP} rgba(30, 30, 30, 220),
        stop:1 rgba(30, 30, 30, 220)
    );
}}
"""

_PANEL_STYLE = """
QFrame {
    background: rgba(30, 30, 30, 220);
    border-left: 1px solid rgba(255, 255, 255, 30);
    border-top-right-radius: 12px;
    border-bottom-right-radius: 12px;
}
"""


class SettingsPanel(QWidget):
    """Overlay settings panel anchored to the right edge of DatumSimWidget.

    A narrow strip of icon buttons serves as tabs. Clicking a tab toggles
    the wider content panel open or closed. Clicking the active tab closes
    the panel.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(STRIP_W)
        self._active     = -1
        self._panel_open = False

        # ── Tab-button strip ──────────────────────────────────────────────────
        self._strip = QWidget(self)
        self._strip.setFixedWidth(STRIP_W)
        self._strip.setStyleSheet(_STRIP_CLOSED)

        self._strip_layout = QVBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(4, 8, 4, 8)
        self._strip_layout.setSpacing(4)
        self._strip_layout.setAlignment(Qt.AlignTop)

        # ── Content panel ─────────────────────────────────────────────────────
        self._panel = QFrame(self)
        self._panel.setFixedWidth(PANEL_W)
        self._panel.setStyleSheet(_PANEL_STYLE)
        self._panel.hide()

        self._stack = QStackedWidget(self._panel)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addWidget(self._stack)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs: list[QToolButton] = []
        self._add_tab(str(_ICONS_DIR / "scan-cube.svg"), "Simulation")
        self._strip_layout.addStretch()

        # ── Panels ────────────────────────────────────────────────────────────
        self._sim_panel = SimPanel(self)
        self._set_tab_content(0, self._sim_panel)

    # ── Public ───────────────────────────────────────────────────────────────

    @property
    def sim_panel(self) -> SimPanel:
        """The functional simulation settings panel."""
        return self._sim_panel

    # ── Tab management ────────────────────────────────────────────────────────

    def _add_tab(self, icon_path: str, tooltip: str) -> None:
        index = len(self._tabs)

        btn = QToolButton(self._strip)
        btn.setIcon(QIcon(icon_path))
        btn.setIconSize(QSize(24, 24))
        btn.setToolTip(tooltip)
        btn.setFixedSize(40, 40)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _=False, i=index: self._on_tab_clicked(i))

        self._strip_layout.insertWidget(index, btn)
        self._tabs.append(btn)
        self._stack.addWidget(QWidget())   # placeholder, replaced below

    def _set_tab_content(self, index: int, widget: QWidget) -> None:
        """Replace the placeholder widget at `index` with the real content."""
        old = self._stack.widget(index)
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(index, widget)

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
        self._strip.setStyleSheet(_STRIP_OPEN)
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
        self._strip.setStyleSheet(_STRIP_CLOSED)
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
