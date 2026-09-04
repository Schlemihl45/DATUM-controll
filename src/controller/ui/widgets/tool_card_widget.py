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
from PySide6.QtGui import QDrag, QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QScrollArea,
    QScroller, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from controller.persistence.tool_db import ToolDatabase
from controller.sim.core.settings import AppSettings
from controller.sim.simulation.tool_definition import ToolDefinition, ToolType, UNASSIGNED_POCKET
from controller.sim.simulation.tool_holder import STANDARD_HOLDERS
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card import Card
from controller.ui.widgets.elided_label import ElidedLabel
from controller.ui.widgets.tool_drag import DragHoldMixin, scaled_drag_pixmap
from controller.ui.widgets.tool_icons import tool_type_icon
from controller.ui.widgets.tool_magazine_bar import TOOL_MIME_TYPE
from controller.ui.widgets.tool_param_group import ParamGroup
from controller.ui.widgets.tool_profile_widget import ToolProfileWidget

_NO_HOLDER = "No Holder defined"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_DIAMETER       = 8.0
DEFAULT_TOTAL_LEN      = 60.0
DEFAULT_SHANK_DIA      = 8.0
DEFAULT_CUTTING_LEN    = 22.0
DEFAULT_FLUTE_COUNT    = 4
DEFAULT_CORNER_RADIUS  = 0.0
DEFAULT_TIP_ANGLE      = 118.0
DEFAULT_TAPER_ANGLE    = 0.0
DEFAULT_SERVICE_LIFE   = 120.0
DEFAULT_USED_MIN       = 0.0
DEFAULT_Z_OFFSET       = 0.0


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
        self._type_icon_lbl.setFixedSize(28, 28)
        header_row.addWidget(self._type_icon_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._pocket_badge = QLabel()
        self._pocket_badge.setObjectName("PocketBadge")
        self._pocket_badge.setFixedSize(30, 22)
        self._pocket_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self._pocket_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._id_lbl = QLabel()
        self._id_lbl.setObjectName("CardButtonLabel")
        header_row.addWidget(self._id_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Collapsed-only quick-glance info grid (Name/Length/Cutting
        # Length/Flutes) — the "old card" 2-row label/value layout this
        # replaces the plain title row with. Hidden again once the card
        # is expanded (see set_expanded()): the expanded body already
        # shows all of these as editable fields, so keeping the grid
        # visible too would just be a redundant, non-editable copy.
        self._info_grid_widget = self._build_collapsed_info_grid()
        header_row.addWidget(
            self._info_grid_widget, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter
        )

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

    # ── Collapsed-state info grid ───────────────────────────────────────────

    def _build_collapsed_info_grid(self) -> QWidget:
        """The "old card" 2-row label/value strip (see module docstring on
        ToolCardWidget) — Name/Length/Cutting Length/Flutes, updated in
        _update_header_label(), shown only while the card is collapsed."""
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(1)

        self._info_name_val = ElidedLabel()
        self._info_name_val.setMaximumWidth(160)
        self._info_length_val = QLabel()
        self._info_cutting_val = QLabel()
        self._info_flutes_val = QLabel()

        for col, (caption, value_lbl) in enumerate((
            ("Name", self._info_name_val),
            ("Length", self._info_length_val),
            ("Cutting Length", self._info_cutting_val),
            ("Flutes", self._info_flutes_val),
        )):
            caption_lbl = QLabel(caption)
            caption_lbl.setObjectName("CardTitle")
            value_lbl.setObjectName("CardButtonLabel")
            grid.addWidget(caption_lbl, 0, col)
            grid.addWidget(value_lbl, 1, col)
        grid.setColumnStretch(4, 1)   # keep the four columns left-packed
        return widget

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
        self._z_off_edit = _num_edit(decimals=3)
        basic_form.addRow("Offset (Z)", self._z_off_edit)
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
        self._diameter_edit = _num_edit()
        geo.add_field("⌀", "mm", self._diameter_edit, "Diameter")
        self._total_len_edit = _num_edit()
        geo.add_field("L", "mm", self._total_len_edit, "Tool Length")
        self._shank_dia_edit = _num_edit()
        geo.add_field("⌀s", "mm", self._shank_dia_edit, "Shank Diameter")
        lower.addWidget(geo)
        lower.addWidget(_vline())

        # flute_length was removed per explicit request — cutting_length
        # is the only length that actually matters here; replace() in
        # _collect_form_into() preserves whatever flute_length a tool
        # already had in the DB, same as the drag&drop-only pocket field.
        flutes = ParamGroup("Flutes")
        self._cutting_length_edit = _num_edit()
        flutes.add_field("Lc", "mm", self._cutting_length_edit, "Cutting Length")
        self._flute_count_edit = _int_edit()
        flutes.add_field("Z", "", self._flute_count_edit, "Flutes")
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
        self._corner_r_edit = _num_edit()
        specific.add_field("R", "mm", self._corner_r_edit, "Corner Radius")
        self._tip_angle_edit = _num_edit(decimals=1)
        specific.add_field("κ", "°", self._tip_angle_edit, "Point Angle")
        self._taper_angle_edit = _num_edit(decimals=1)
        specific.add_field("τ", "°", self._taper_angle_edit, "Taper Angle")
        lower.addWidget(specific)
        self._specific_sep2 = _vline()
        lower.addWidget(self._specific_sep2)
        self._specific_group = specific

        lifecycle = ParamGroup("Lifecycle")
        self._service_life_edit = _num_edit(decimals=1)
        lifecycle.add_field("Ts", "min", self._service_life_edit, "Service Life")
        self._used_min_edit = _num_edit(decimals=1)
        lifecycle.add_field("Tu", "min", self._used_min_edit, "Used")
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
        geometry_edits = (
            self._diameter_edit, self._cutting_length_edit,
            self._shank_dia_edit, self._total_len_edit, self._corner_r_edit,
            self._tip_angle_edit, self._taper_angle_edit,
        )
        for edit in geometry_edits:
            edit.textChanged.connect(self._push_live_profile)
        self._type_combo.currentIndexChanged.connect(self._push_live_profile)
        self._type_combo.currentIndexChanged.connect(self._update_type_specific_visibility)
        self._holder_combo.currentIndexChanged.connect(self._push_live_profile)

        # Auto-save: discrete widgets are already "final" on every change
        # (valueChanged/currentIndexChanged); free-text fields use
        # editingFinished/focus-out instead of textChanged, so a row isn't
        # rewritten on every keystroke.
        for edit in geometry_edits + (
            self._z_off_edit, self._service_life_edit, self._used_min_edit,
            self._flute_count_edit,
        ):
            edit.editingFinished.connect(self._auto_save)

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
        self._specific_group.set_field_visible(self._corner_r_edit, show_corner)
        self._specific_group.set_field_visible(self._tip_angle_edit, show_tip)
        self._specific_group.set_field_visible(self._taper_angle_edit, show_taper)
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
        self._remark_edit.setPlainText(tool.remark)

        self._z_off_edit.setText(f"{tool.z_offset:.3f}")

        dia = tool.diameter if tool.diameter > 0 else DEFAULT_DIAMETER
        self._diameter_edit.setText(f"{dia:.2f}")

        tlen = tool.total_length if tool.total_length > 0 else DEFAULT_TOTAL_LEN
        self._total_len_edit.setText(f"{tlen:.2f}")

        sdia = tool.shank_diameter if tool.shank_diameter > 0 else DEFAULT_SHANK_DIA
        self._shank_dia_edit.setText(f"{sdia:.2f}")

        clen = tool.cutting_length if tool.cutting_length > 0 else DEFAULT_CUTTING_LEN
        self._cutting_length_edit.setText(f"{clen:.2f}")

        fl = tool.flute_count if tool.flute_count > 0 else DEFAULT_FLUTE_COUNT
        self._flute_count_edit.setText(str(fl))

        self._corner_r_edit.setText(f"{tool.corner_radius:.2f}")

        tip = tool.tip_angle if tool.tip_angle > 0 else DEFAULT_TIP_ANGLE
        self._tip_angle_edit.setText(f"{tip:.1f}")

        self._taper_angle_edit.setText(f"{tool.taper_angle:.1f}")

        sl = tool.service_life_min if tool.service_life_min > 0 else DEFAULT_SERVICE_LIFE
        self._service_life_edit.setText(f"{sl:.1f}")

        self._used_min_edit.setText(f"{tool.used_min:.1f}")

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
        # The collapsed-only info grid is purely a quick-glance preview —
        # the expanded body already shows the same fields as editable
        # inputs, so keeping the grid up too would just be a redundant,
        # read-only duplicate of what's right below it.
        self._info_grid_widget.setVisible(not expanded)
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
        self._type_icon_lbl.setPixmap(tool_type_icon(tt, size=28).pixmap(28, 28))

        # Collapsed-state info grid — live values straight off the form
        # widgets (not self._tool) so it reflects unsaved edits too, same
        # as the rest of this method already does for name/pocket.
        self._info_name_val.set_full_text(name)
        self._info_length_val.setText(f"{self._total_len_spin.value():.1f} mm")
        self._info_cutting_val.setText(f"{self._cutting_length_spin.value():.1f} mm")
        self._info_flutes_val.setText(str(self._flute_count_spin.value()))

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
            z_offset=_parse_float(self._z_off_edit.text(), DEFAULT_Z_OFFSET),
            remark=self._remark_edit.toPlainText(),
            diameter=_parse_float(self._diameter_edit.text(), DEFAULT_DIAMETER),
            total_length=_parse_float(self._total_len_edit.text(), DEFAULT_TOTAL_LEN),
            shank_diameter=_parse_float(self._shank_dia_edit.text(), DEFAULT_SHANK_DIA),
            cutting_length=_parse_float(self._cutting_length_edit.text(), DEFAULT_CUTTING_LEN),
            flute_count=_parse_int(self._flute_count_edit.text(), DEFAULT_FLUTE_COUNT),
            corner_radius=_parse_float(self._corner_r_edit.text(), DEFAULT_CORNER_RADIUS),
            tip_angle=_parse_float(self._tip_angle_edit.text(), DEFAULT_TIP_ANGLE),
            taper_angle=_parse_float(self._taper_angle_edit.text(), DEFAULT_TAPER_ANGLE),
            service_life_min=_parse_float(self._service_life_edit.text(), DEFAULT_SERVICE_LIFE),
            used_min=_parse_float(self._used_min_edit.text(), DEFAULT_USED_MIN),
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
        # Sized up (padding/font + icon-size, see dark.qss/light.qss's
        # QMenu rule) per explicit request for a more touch-friendly menu.
        # QMenu has no setIconSize() method (that's QToolBar/QListView) —
        # the "icon-size" QSS property is the correct way to size it.
        menu = QMenu(self)
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


def _num_edit(decimals: int = 2) -> QLineEdit:
    edit = QLineEdit()
    edit.setMaximumWidth(90)
    val = QDoubleValidator()
    val.setDecimals(decimals)
    val.setNotation(QDoubleValidator.Notation.StandardNotation)
    edit.setValidator(val)
    return edit


def _int_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setMaximumWidth(70)
    edit.setValidator(QIntValidator(0, 999))
    return edit


def _parse_float(text: str, default: float) -> float:
    try:
        return float(text.replace(",", "."))
    except (ValueError, TypeError):
        return default


def _parse_int(text: str, default: int) -> int:
    try:
        return int(text)
    except (ValueError, TypeError):
        return default


def _vline() -> QFrame:
    line = QFrame()
    line.setObjectName("ParamSeparator")
    line.setFixedWidth(1)
    return line