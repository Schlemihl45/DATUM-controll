"""
ui/widgets/machine_info_cards.py — Three separate live-data cards:
axis positions, feedrate, spindle RPM.

All internal spacing (icon-to-letter, letter-to-value, value widths,
unit widths, card padding) is defined as a PERCENTAGE of the card's
own width, recalculated on every resize via _PercentRow. Nothing is a
hardcoded pixel value except icon pixel size itself (rendered via the
DPI-aware icon_loader, sized independently of layout spacing).

Value labels still get a fixed pixel width per resize (computed from
%), so a live-updating number never shifts anything else WITHIN one
frame — it only changes when the card itself is resized, which is
expected, not a live-update artifact.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

from controller.core.machine.controller import MachineController
from controller.domain.models import FeedData, Position
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.load_bar import LoadBar

# ======================================================================
# Alle Abstände als % der jeweiligen Card-Breite — hier zentral einstellbar
# ======================================================================
PAD_LEFT_PCT = 8
PAD_RIGHT_PCT = 8
PAD_TOP_PCT = 6
PAD_BOTTOM_PCT = 6

GAP_ICON_TO_LETTER_PCT = 4
GAP_LETTER_TO_VALUE_PCT = 14
GAP_VALUE_TO_TOGO_PCT = 3
GAP_TOGO_TO_UNIT_PCT = 2
GAP_ROWS_PCT = 5          # vertikaler Abstand zwischen Zeilen

LETTER_WIDTH_PCT = 8
VALUE_WIDTH_PCT = 26
TOGO_WIDTH_PCT = 14
UNIT_WIDTH_PCT = 20

ICON_SIZE = 64             # bewusst NICHT prozentual — feste physische
                            # Icon-Größe, DPI-Korrektur passiert im Loader


def _value_font() -> QFont:
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class _PercentRow(QWidget):
    """
    Horizontale Zeile, deren Lücken (QSpacerItem) und optional fest
    zugewiesene Widget-Breiten sich als Prozent der EIGENEN Widget-
    Breite bei jedem Resize neu berechnen.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._gaps: list[tuple[QSpacerItem, float]] = []
        self._fixed_widgets: list[tuple[QWidget, float]] = []

    def add_widget(self, widget: QWidget, width_pct: float | None = None) -> None:
        self._layout.addWidget(widget)
        if width_pct is not None:
            self._fixed_widgets.append((widget, width_pct))

    def add_gap(self, pct: float) -> None:
        spacer = QSpacerItem(1, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._layout.addSpacerItem(spacer)
        self._gaps.append((spacer, pct))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = max(self.width(), 1)
        for spacer, pct in self._gaps:
            spacer.changeSize(int(w * pct / 100), 0,
                               QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        for widget, pct in self._fixed_widgets:
            widget.setFixedWidth(int(w * pct / 100))
        self._layout.invalidate()
        self._layout.activate()


class _LiveValueCard(Card):
    """
    Gemeinsame Basis: übernimmt die prozentuale Innenabstand-Berechnung
    (überschreibt Cards festen 16px-Rand) — jede Subklasse baut nur
    noch ihre eigenen Zeilen über _PercentRow.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title=title, parent=parent)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = max(self.width(), 1)
        left = int(w * PAD_LEFT_PCT / 100)
        right = int(w * PAD_RIGHT_PCT / 100)
        top = int(w * PAD_TOP_PCT / 100)
        bottom = int(w * PAD_BOTTOM_PCT / 100)
        self.layout().setContentsMargins(left, top, right, bottom)


def _build_value_row(
    icon_name: str, letter: str | None, unit_text: str
) -> tuple[_PercentRow, QLabel]:
    row = _PercentRow()

    icon_size = QSize(ICON_SIZE, ICON_SIZE)
    icon_label = QLabel()
    icon_label.setObjectName("InfoIcon")
    icon_label.setFixedSize(icon_size)
    icon_label.setPixmap(get_icon(icon_name, size=icon_size).pixmap(icon_size))
    row.add_widget(icon_label)

    row.add_gap(GAP_ICON_TO_LETTER_PCT)

    if letter is not None:
        letter_label = QLabel(letter)
        letter_label.setObjectName("InfoAxisLetter")
        row.add_widget(letter_label, width_pct=LETTER_WIDTH_PCT)
        row.add_gap(GAP_LETTER_TO_VALUE_PCT)

    value_label = QLabel("0")
    value_label.setObjectName("InfoValue")
    value_label.setFont(_value_font())
    value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.add_widget(value_label, width_pct=VALUE_WIDTH_PCT)

    row.add_gap(GAP_TOGO_TO_UNIT_PCT)

    unit_label = QLabel(unit_text)
    unit_label.setObjectName("InfoUnit")
    row.add_widget(unit_label, width_pct=UNIT_WIDTH_PCT)

    return row, value_label


# ======================================================================
# Card 1 — Achspositionen
# ======================================================================

class AxisPositionCard(_LiveValueCard):

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(title="Position", parent=parent)
        self._controller = controller

        self._position_labels: dict[str, QLabel] = {}

        for axis in ("x", "y", "z"):
            row, value_label = _build_value_row(f"{axis}-coordinate", axis.upper(), "[mm]")
            value_label.setText("0000.00")
            self.content_layout.addWidget(row)
            self._position_labels[axis] = value_label

        controller.position_changed.connect(self._on_position)

    def _on_position(self, pos: Position) -> None:
        self._position_labels["x"].setText(f"{pos.x:.2f}")
        self._position_labels["y"].setText(f"{pos.y:.2f}")
        self._position_labels["z"].setText(f"{pos.z:.2f}")


# ======================================================================
# Card 2 — Vorschub
# ======================================================================

class FeedrateCard(_LiveValueCard):

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(title="Feedrate", parent=parent)
        self._controller = controller

        row, self._feedrate_value = _build_value_row("spindle_moving", None, "[mm min⁻¹]")
        self.content_layout.addWidget(row)

        controller.feed_changed.connect(self._on_feed)

    def _on_feed(self, feed: FeedData) -> None:
        self._feedrate_value.setText(f"{feed.feed_actual:.0f}")


# ======================================================================
# Card 3 — Spindeldrehzahl
# ======================================================================

class SpindleCard(_LiveValueCard):

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(title="Spindle", parent=parent)
        self._controller = controller

        self._load_bar = LoadBar(radius=12, parent=self)
        self._load_bar.lower()  # hinter den restlichen Inhalt legen

        row, self._rpm_value = _build_value_row("spindle", None, "[min⁻¹]")
        self.content_layout.addWidget(row)

        controller.feed_changed.connect(self._on_feed)
        controller.feed_changed.connect(self._on_load)

        # TODO: controller.spindle_load_changed.connect(self._load_bar.set_load)
        #       Signal existiert noch nicht — siehe Hinweis oben.

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._load_bar.setGeometry(self.rect())

    def _on_feed(self, feed: FeedData) -> None:
        self._rpm_value.setText(f"{feed.spindle_rpm:.0f}")

    def _on_load(self, feed: FeedData) -> None:
        self._load_bar.set_load(feed.spindle_load.percent / 100.0)