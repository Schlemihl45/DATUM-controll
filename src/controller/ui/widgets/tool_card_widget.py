"""
ui/widgets/tool_card_widget.py — ToolCardWidget: one tool, rendered as an
inline-expanding card (DATRON Next style) — replaces the previous separate
ToolDetailPage entirely. Collapsed: just the header (pocket badge, tool
number, name, type icon, "..." options menu). Clicking the header toggles
an expanded body below it, in place, inside the same vertical list —
there is no page navigation anymore.

Every field in the expanded body auto-saves individually (valueChanged/
editingFinished -> ToolDatabase.upsert_tool()) — see _auto_save(). There
is no Save action; the header's options menu only offers Measure (stub —
no measurement backend exists anywhere in this app yet, see
_show_measure_stub()) and Delete (confirmed via QMessageBox, the same
addButton/ButtonRole/clickedButton idiom machine_page.py already uses for
its collision dialogs).

The header is also this tool's drag SOURCE into a magazine pocket — same
DragHoldMixin press-and-hold gating tool_magazine_bar.py's _PocketSlot
uses, so a plain click (which toggles expand/collapse here) is never
misread as a drag start.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QScrollArea,
    QScroller, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from controller.persistence.tool_db import ToolDatabase
from controller.sim.core.settings import AppSettings
from controller.sim.simulation.tool_definition import ToolDefinition, ToolType, UNASSIGNED_POCKET
from controller.sim.simulation.tool_holder import STANDARD_HOLDERS
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.tool_drag import DragHoldMixin, scaled_drag_pixmap
from controller.ui.widgets.tool_icons import tool_type_icon
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE
from controller.ui.widgets.tool_param_group import ParamGroup
from controller.ui.widgets.tool_profile_widget import ToolProfileWidget

_NO_HOLDER = "— Keine —"


class _AutoSaveTextEdit(QPlainTextEdit):
    """QPlainTextEdit has no built-in editingFinished — this adds the
    equivalent (fired on focus-out) so Notes can auto-save without
    writing on every keystroke."""

    focus_out = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_out.emit()


class _CardHeader(DragHoldMixin, QWidget):
    """Always-visible row. A plain click (press+release, no drag ever
    started) emits clicked — ToolCardWidget toggles expand/collapse on
    it. A press-and-hold-then-move starts a drag of this tool into a
    magazine pocket, via the same TOOL_MIME_TYPE tool_magazine_bar.py's
    pocket slots accept."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dh_init()
        self._tool_number: int | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_tool_number(self, tool_number: int) -> None:
        self._tool_number = tool_number

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dh_press(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._tool_number is not None and self._dh_move(event.position().toPoint()):
            self._dh_release()
            self._start_drag()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_click = self._dh_press_pos is not None
        self._dh_release()
        if was_click and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TOOL_MIME_TYPE, str(self._tool_number).encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(scaled_drag_pixmap(self))
        drag.exec(Qt.DropAction.MoveAction)


class ToolCardWidget(Card):
    """One tool's list row. See module docstring."""

    # tool_number, target_pocket — emitted by the header menu's "Set Pocket
    # Number" action. Deliberately NOT written straight to ToolDatabase
    # here: ToolPage already owns the occupant-kick-on-swap logic for
    # pocket reassignment (see its _on_pocket_reassigned(), also driven by
    # ToolMagazineBar's drag&drop) — re-emitting through the same handler
    # keeps that one place as the single source of truth instead of a
    # second, divergent implementation.
    pocket_change_requested = Signal(int, int)

    def __init__(self, tool: ToolDefinition, parent: QWidget | None = None) -> None:
        super().__init__(title=None, parent=parent)
        self._tool: ToolDefinition | None = None
        self._tool_number = tool.tool_number
        self._expanded = False
        self._loading = False

        self.content_layout.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # ── Header ───────────────────────────────────────────────────────────
        self._header = _CardHeader(self)
        self._header.set_tool_number(tool.tool_number)
        self._header.clicked.connect(self.toggle_expanded)
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        self._type_icon_lbl = QLabel()
        self._type_icon_lbl.setFixedSize(24, 24)
        header_row.addWidget(self._type_icon_lbl)

        self._pocket_badge = QLabel()
        self._pocket_badge.setObjectName("PocketBadge")
        self._pocket_badge.setFixedSize(30, 22)
        self._pocket_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self._pocket_badge)

        self._id_lbl = QLabel()
        self._id_lbl.setObjectName("CardButtonLabel")
        header_row.addWidget(self._id_lbl, stretch=1)

        self._menu_btn = QToolButton(self._header)
        self._menu_btn.setIcon(get_icon("settings", tint=True))
        self._menu_btn.setIconSize(QSize(18, 18))
        self._menu_btn.setFixedSize(32, 32)
        self._menu_btn.setObjectName("ToolMenuButton")
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setToolTip("Optionen")
        self._menu_btn.clicked.connect(self._show_menu)
        header_row.addWidget(self._menu_btn)

        self.content_layout.addWidget(self._header)

        # ── Body (built once; shown/hidden on expand/collapse) ─────────────────
        self._body = QWidget(self)
        self._body.setVisible(False)
        self._build_body()
        self.content_layout.addWidget(self._body)

        self.set_tool(tool)

    # ── Body construction ───────────────────────────────────────────────────

    def _build_body(self) -> None:
        root = QVBoxLayout(self._body)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(12)

        # Upper block: 2D preview (left) + basic identity fields (right).
        upper = QHBoxLayout()
        upper.setSpacing(20)

        self._profile = ToolProfileWidget()
        self._profile.setMinimumWidth(220)
        upper.addWidget(self._profile, stretch=1)

        basic_form = QFormLayout()
        basic_form.setSpacing(6)
        self._name_edit = QLineEdit()
        basic_form.addRow("Name", self._name_edit)
        self._type_combo = QComboBox()
        for tt in ToolType:
            self._type_combo.addItem(tt.name.replace("_", " ").title(), userData=tt)
        basic_form.addRow("Tooltype", self._type_combo)
        self._holder_combo = QComboBox()
        self._holder_combo.addItem(_NO_HOLDER, userData=None)
        for name in sorted(STANDARD_HOLDERS.keys()):
            self._holder_combo.addItem(name, userData=name)
        basic_form.addRow("Holder", self._holder_combo)
        self._manufacturer_edit = QLineEdit()
        basic_form.addRow("Hersteller", self._manufacturer_edit)
        self._material_edit = QLineEdit()
        basic_form.addRow("Material", self._material_edit)
        self._z_off_spin = QDoubleSpinBox()
        self._z_off_spin.setRange(-500.0, 500.0)
        self._z_off_spin.setDecimals(3)
        self._z_off_spin.setSuffix(" mm")
        basic_form.addRow("Offset (Z)", self._z_off_spin)
        self._remark_edit = _AutoSaveTextEdit()
        self._remark_edit.setFixedHeight(54)
        basic_form.addRow("Notes", self._remark_edit)

        basic_widget = QWidget()
        basic_widget.setLayout(basic_form)
        upper.addWidget(basic_widget, stretch=1)
        root.addLayout(upper)

        # Lower block: parameter groups, separated by thin vertical rules,
        # left-aligned with a trailing stretch pushing everything left.
        # Wrapped in its own non-resizable, horizontally-scrolling strip
        # (rather than laid straight into `root`) because this row's
        # natural minimum width (four ParamGroups' worth of columns) can
        # easily exceed the card's/list's actual width — without this,
        # ToolListView's own horizontal scrollbar stays off (by design,
        # only vertical scrolling), so the excess used to simply be
        # clipped at the card's right edge instead of reachable.
        lower = QHBoxLayout()
        lower.setSpacing(0)

        geo = ParamGroup("Geometry")
        self._diameter_spin = _mm_spin(0.01, 200.0)
        geo.add_field("⌀", "mm", self._diameter_spin, "Diameter")
        self._total_len_spin = _mm_spin(0.0, 500.0)
        geo.add_field("L", "mm", self._total_len_spin, "Tool Length")
        self._shank_dia_spin = _mm_spin(0.0, 200.0)
        geo.add_field("⌀s", "mm", self._shank_dia_spin, "Shank Diameter")
        lower.addWidget(geo)
        lower.addWidget(_vline())

        # flute_length was removed per explicit request — cutting_length
        # is the only length that actually matters here; replace() in
        # _collect_form_into() preserves whatever flute_length a tool
        # already had in the DB, same as the drag&drop-only pocket field.
        flutes = ParamGroup("Flutes")
        self._cutting_length_spin = _mm_spin(0.0, 500.0)
        flutes.add_field("Lc", "mm", self._cutting_length_spin, "Cutting Length")
        self._flute_count_spin = QSpinBox()
        self._flute_count_spin.setRange(0, 20)
        self._flute_count_spin.setMaximumWidth(70)
        flutes.add_field("Z", "", self._flute_count_spin, "Flutes")
        lower.addWidget(flutes)
        self._specific_sep = _vline()
        lower.addWidget(self._specific_sep)

        # "Specific" — every field here is dynamically shown only for the
        # tool_type that actually uses it (see
        # ToolDefinition.profile_radius_at()/profile_radius_at_array():
        # BULL_ENDMILL alone consumes corner_radius, CHAMFER and DRILL
        # both consume tip_angle, TAPER alone consumes taper_angle; every
        # other type's profile ignores whatever value sits there). Unlike
        # the previous version, there is no always-shown field (clearance
        # angle/"alpha" was removed per explicit request — it wasn't
        # self-explanatory and nothing reads it) — so for ENDMILL/
        # BALL_ENDMILL, where none of the three apply, the whole group
        # (and one of its two separators) is hidden rather than shown
        # empty; see _update_type_specific_visibility().
        specific = ParamGroup("Specific")
        self._corner_r_spin = _mm_spin(0.0, 100.0)
        specific.add_field("R", "mm", self._corner_r_spin, "Corner Radius")
        self._tip_angle_spin = _deg_spin()
        specific.add_field("κ", "°", self._tip_angle_spin, "Point Angle")
        self._taper_angle_spin = _deg_spin()
        specific.add_field("τ", "°", self._taper_angle_spin, "Taper Angle")
        lower.addWidget(specific)
        self._specific_sep2 = _vline()
        lower.addWidget(self._specific_sep2)
        self._specific_group = specific

        lifecycle = ParamGroup("Lifecycle")
        self._service_life_spin = QDoubleSpinBox()
        self._service_life_spin.setRange(0.0, 100000.0)
        self._service_life_spin.setMaximumWidth(90)
        lifecycle.add_field("Ts", "min", self._service_life_spin, "Service Life")
        self._used_min_spin = QDoubleSpinBox()
        self._used_min_spin.setRange(0.0, 100000.0)
        self._used_min_spin.setMaximumWidth(90)
        lifecycle.add_field("Tu", "min", self._used_min_spin, "Used")
        lower.addWidget(lifecycle)

        lower.addStretch(1)

        lower_widget = QWidget()
        lower_widget.setLayout(lower)
        lower_scroll = QScrollArea()
        lower_scroll.setWidget(lower_widget)
        lower_scroll.setWidgetResizable(False)
        lower_scroll.setFrameShape(QFrame.Shape.NoFrame)
        lower_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        lower_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lower_scroll.setFixedHeight(lower_widget.sizeHint().height() + 18)
        QScroller.grabGesture(
            lower_scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture
        )
        root.addWidget(lower_scroll)

        self._wire_live_preview_and_autosave()

    def _wire_live_preview_and_autosave(self) -> None:
        geometry_spins = (
            self._diameter_spin, self._cutting_length_spin,
            self._shank_dia_spin, self._total_len_spin, self._corner_r_spin,
            self._tip_angle_spin, self._taper_angle_spin,
        )
        for spin in geometry_spins:
            spin.valueChanged.connect(self._push_live_profile)
        self._type_combo.currentIndexChanged.connect(self._push_live_profile)
        self._type_combo.currentIndexChanged.connect(self._update_type_specific_visibility)
        self._holder_combo.currentIndexChanged.connect(self._push_live_profile)

        # Auto-save: discrete widgets are already "final" on every change
        # (valueChanged/currentIndexChanged); free-text fields use
        # editingFinished/focus-out instead of textChanged, so a row isn't
        # rewritten on every keystroke.
        for spin in geometry_spins + (
            self._z_off_spin, self._service_life_spin, self._used_min_spin,
            self._flute_count_spin,
        ):
            spin.valueChanged.connect(self._auto_save)
        self._type_combo.currentIndexChanged.connect(self._auto_save)
        self._holder_combo.currentIndexChanged.connect(self._auto_save)
        self._name_edit.editingFinished.connect(self._auto_save)
        self._manufacturer_edit.editingFinished.connect(self._auto_save)
        self._material_edit.editingFinished.connect(self._auto_save)
        self._remark_edit.focus_out.connect(self._auto_save)

    def _update_type_specific_visibility(self, *_args) -> None:
        tt = self._type_combo.currentData()
        show_corner = tt == ToolType.BULL_ENDMILL
        show_tip = tt in (ToolType.CHAMFER, ToolType.DRILL)
        show_taper = tt == ToolType.TAPER
        self._specific_group.set_field_visible(self._corner_r_spin, show_corner)
        self._specific_group.set_field_visible(self._tip_angle_spin, show_tip)
        self._specific_group.set_field_visible(self._taper_angle_spin, show_taper)
        # "Specific" has no always-shown field anymore (clearance angle
        # was removed) — for a tool_type where none of the three apply
        # (ENDMILL, BALL_ENDMILL), hide the whole group rather than
        # showing an empty category header. Its trailing separator hides
        # with it so the remaining ones read as "Flutes | Lifecycle"
        # instead of a double rule; the separator BEFORE it stays put and
        # simply ends up doing that job.
        has_specific = show_corner or show_tip or show_taper
        self._specific_group.setVisible(has_specific)
        self._specific_sep2.setVisible(has_specific)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_tool(self, tool: ToolDefinition) -> None:
        self._loading = True
        self._tool = replace(tool)
        self._tool_number = tool.tool_number
        self._header.set_tool_number(tool.tool_number)

        self._name_edit.setText(tool.name)
        idx = self._type_combo.findData(tool.tool_type)
        self._type_combo.setCurrentIndex(max(0, idx))
        idx = self._holder_combo.findData(tool.holder_preset)
        self._holder_combo.setCurrentIndex(max(0, idx))
        self._manufacturer_edit.setText(tool.manufacturer)
        self._material_edit.setText(tool.material)
        self._z_off_spin.setValue(tool.z_offset)
        self._remark_edit.setPlainText(tool.remark)

        self._diameter_spin.setValue(tool.diameter)
        self._total_len_spin.setValue(tool.total_length)
        self._shank_dia_spin.setValue(tool.shank_diameter)
        self._cutting_length_spin.setValue(tool.cutting_length)
        self._flute_count_spin.setValue(tool.flute_count)
        self._corner_r_spin.setValue(tool.corner_radius)
        self._tip_angle_spin.setValue(tool.tip_angle)
        self._taper_angle_spin.setValue(tool.taper_angle)
        self._service_life_spin.setValue(tool.service_life_min)
        self._used_min_spin.setValue(tool.used_min)

        self._loading = False
        self._update_type_specific_visibility()
        self._update_header_label()
        self._push_live_profile()

    @property
    def tool_number(self) -> int:
        return self._tool_number

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        # Dynamic property so QSS can style an expanded card differently
        # (e.g. an accent border) — same unpolish/polish idiom
        # card_button.py's checkable state already uses.
        self.setProperty("expanded", expanded)
        self.style().unpolish(self)
        self.style().polish(self)

    def is_expanded(self) -> bool:
        return self._expanded

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_header_label(self, *_args) -> None:
        if self._tool is None:
            return
        pocket = self._tool.pocket
        self._pocket_badge.setText(str(pocket) if pocket >= 1 else "-")
        name = self._name_edit.text() or self._tool.remark or f"T{self._tool_number}"
        self._id_lbl.setText(f"T{self._tool_number}  ·  {name}")
        tt = self._type_combo.currentData() or ToolType.ENDMILL
        self._type_icon_lbl.setPixmap(tool_type_icon(tt, size=24).pixmap(24, 24))

    def _collect_form_into(self, base: ToolDefinition) -> ToolDefinition:
        # pocket, cutting_speed, feed_rate, flute_length, clearance_angle
        # deliberately NOT listed here: pocket is drag&drop-only/"Set
        # Pocket Number"-only (never a plain form field in this card);
        # cutting_speed/feed_rate were removed from this UI per explicit
        # request; flute_length ("no added value over cutting_length")
        # and clearance_angle ("alpha" — unexplained, unused) were removed
        # per explicit request too. replace() preserves whatever value
        # each already had on `base`.
        return replace(
            base,
            name=self._name_edit.text(),
            tool_type=self._type_combo.currentData(),
            holder_preset=self._holder_combo.currentData(),
            manufacturer=self._manufacturer_edit.text(),
            material=self._material_edit.text(),
            z_offset=self._z_off_spin.value(),
            remark=self._remark_edit.toPlainText(),
            diameter=self._diameter_spin.value(),
            total_length=self._total_len_spin.value(),
            shank_diameter=self._shank_dia_spin.value(),
            cutting_length=self._cutting_length_spin.value(),
            flute_count=self._flute_count_spin.value(),
            corner_radius=self._corner_r_spin.value(),
            tip_angle=self._tip_angle_spin.value(),
            taper_angle=self._taper_angle_spin.value(),
            service_life_min=self._service_life_spin.value(),
            used_min=self._used_min_spin.value(),
        )

    def _push_live_profile(self, *_args) -> None:
        if self._loading or self._tool is None:
            return
        preview = self._collect_form_into(self._tool)
        holder_name = self._holder_combo.currentData()
        holder = STANDARD_HOLDERS.get(holder_name) if holder_name else None
        self._profile.set_tool(preview, holder)

    def _auto_save(self, *_args) -> None:
        if self._loading or self._tool is None:
            return
        self._tool = self._collect_form_into(self._tool)
        ToolDatabase.instance().upsert_tool(self._tool)
        self._update_header_label()

    def _show_menu(self) -> None:
        # Sized up (iconSize + QMenu::item padding/font, see dark.qss/
        # light.qss) per explicit request for a more touch-friendly menu.
        menu = QMenu(self)
        menu.setIconSize(QSize(22, 22))
        measure_action = menu.addAction(get_icon("scan-cube", tint=True), "Measure")
        measure_action.triggered.connect(self._show_measure_stub)
        pocket_action = menu.addAction(get_icon("tools", tint=True), "Set Pocket Number")
        pocket_action.triggered.connect(self._prompt_set_pocket)
        delete_action = menu.addAction(get_icon("delete", tint=True), "Delete")
        delete_action.triggered.connect(self._confirm_delete)
        menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))

    def _prompt_set_pocket(self) -> None:
        """Manual alternative to drag&drop for pocket assignment — asks
        for a pocket number and re-emits it as pocket_change_requested
        rather than writing ToolDatabase directly, so ToolPage's existing
        occupant-kick-on-swap logic (shared with magazine drag&drop)
        handles it uniformly. -1 unassigns, same convention as the
        magazine bar's "drop onto the list" gesture."""
        if self._tool is None:
            return
        pocket_count = AppSettings.instance().tool_pocket_count
        current = self._tool.pocket if self._tool.pocket >= 1 else UNASSIGNED_POCKET
        value, ok = QInputDialog.getInt(
            self, "Set Pocket Number",
            f"Pocket for T{self._tool_number} ({UNASSIGNED_POCKET} = unassigned):",
            current, UNASSIGNED_POCKET, pocket_count,
        )
        if ok:
            self.pocket_change_requested.emit(self._tool_number, value)

    def _show_measure_stub(self) -> None:
        # No tool-measurement backend/hardware hook exists anywhere in
        # this app yet — an honest stub rather than a fabricated result.
        QMessageBox.information(
            self, "Measure",
            "Die Werkzeug-Vermessung ist noch nicht implementiert.",
        )

    def _confirm_delete(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Werkzeug löschen")
        box.setText(
            f"Werkzeug T{self._tool_number} wirklich löschen?\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )
        delete_btn = box.addButton("Löschen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(delete_btn)
        box.exec()
        if box.clickedButton() is delete_btn:
            ToolDatabase.instance().delete_tool(self._tool_number)


def _mm_spin(lo: float, hi: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setDecimals(2)
    spin.setMaximumWidth(90)
    return spin


def _deg_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 180.0)
    spin.setDecimals(1)
    spin.setMaximumWidth(90)
    return spin


def _vline() -> QFrame:
    line = QFrame()
    line.setObjectName("ParamSeparator")
    line.setFixedWidth(1)
    return line
