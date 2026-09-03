"""
ui/pages/tool_detail_page.py — ToolDetailPage: full editor for one tool,
shown by ToolPage's internal QStackedWidget when a list card's details
button is clicked.

Layout (top to bottom): header ("Zurück" button + read-only ID/pocket/
name line) — ToolProfileWidget (live 2D preview) — two QFormLayout blocks
side by side (identity/lifecycle fields on the left + remark below it;
geometric/technological parameters on the right).

Persistence: auto-save. Every field commits to ToolDatabase on its own
natural "I'm done with this value" signal — valueChanged for spin boxes
and combo boxes (discrete, already-final on every change), editingFinished
/focus-out for free text (QLineEdit/QPlainTextEdit — NOT textChanged, so a
row isn't rewritten on every keystroke). See _auto_save(). The header
button is therefore just navigation now ("Zurück", no "Speichern" in the
label) — it still calls _auto_save() once more before leaving, as a flush
for a text field whose focus never left before the click. The live 2D
preview (_push_live_profile()) is unrelated to this — it always reflects
the current, not-yet-necessarily-saved form state, same as before.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from controller.persistence.tool_db import ToolDatabase
from controller.sim.simulation.tool_definition import ToolDefinition, ToolType
from controller.sim.simulation.tool_holder import STANDARD_HOLDERS
from controller.ui.icon_loader import get_icon
from controller.ui.widgets.card_button import CardButton
from controller.ui.widgets.tool_profile_widget import ToolProfileWidget

_NO_HOLDER = "No Holder"


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("CardTitle")
    return lbl


class _AutoSaveTextEdit(QPlainTextEdit):
    """QPlainTextEdit has no built-in editingFinished — this adds the
    equivalent (fired on focus-out) so the remark field can auto-save
    without writing on every keystroke."""

    focus_out = Signal()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_out.emit()


class ToolDetailPage(QWidget):
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool: ToolDefinition | None = None
        self._loading = False   # guards _push_live_profile() during set_tool()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
        header = QHBoxLayout()
        # Horizontal orientation (icon left, label right), same proven
        # pattern + size settings_page.py's _NavStack nav buttons use
        # (_NAV_BTN_SIZE=196x44, _NAV_ICON_SIZE=20x20) — the previous
        # vertical CardButton (icon above label) needs ~74px to fit inside
        # Card's hardcoded 16px margins, but was fixed to 48px, squishing
        # it. No more "Speichern" in the label: saving is now continuous
        # (see _auto_save()), this button just navigates back.
        self._back_btn = CardButton(
            "Return", icon=get_icon("return", tint=True, size=QSize(20, 20)),
            icon_size=20, orientation=Qt.Orientation.Horizontal,
        )
        self._back_btn.setProperty("variant", "sim_nav")
        self._back_btn.setFixedSize(QSize(140, 44))
        self._back_btn.clicked.connect(self._close)
        header.addWidget(self._back_btn)

        self._id_lbl = QLabel()
        self._id_lbl.setObjectName("CardTitle")
        self._id_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self._id_lbl, stretch=1)
        root.addLayout(header)

        # ── 2D preview ───────────────────────────────────────────────────────
        self._profile = ToolProfileWidget()
        root.addWidget(self._profile)

        # ── Two form blocks side by side ────────────────────────────────────
        forms_row = QHBoxLayout()
        forms_row.setSpacing(24)

        left_col = QVBoxLayout()
        left_col.addWidget(_section_label("General"))
        left_form = QFormLayout(); left_form.setSpacing(8)

        self._name_edit = QLineEdit()
        left_form.addRow("Name", self._name_edit)

        self._pocket_spin = QSpinBox()
        self._pocket_spin.setRange(-1, 200)
        self._pocket_spin.setSpecialValueText("(not defined)")
        left_form.addRow("Pocket-Nummer", self._pocket_spin)

        self._z_off_spin = QDoubleSpinBox()
        self._z_off_spin.setRange(-500.0, 500.0)
        self._z_off_spin.setDecimals(3)
        self._z_off_spin.setSuffix(" mm")
        left_form.addRow("Offset (Z)", self._z_off_spin)

        self._type_combo = QComboBox()
        for tt in ToolType:
            self._type_combo.addItem(tt.name.replace("_", " ").title(), userData=tt)
        left_form.addRow("Tooltype", self._type_combo)

        self._holder_combo = QComboBox()
        self._holder_combo.addItem(_NO_HOLDER, userData=None)
        for name in sorted(STANDARD_HOLDERS.keys()):
            self._holder_combo.addItem(name, userData=name)
        left_form.addRow("Holder", self._holder_combo)

        self._service_life_spin = QDoubleSpinBox()
        self._service_life_spin.setRange(0.0, 100000.0)
        self._service_life_spin.setSuffix(" min")
        left_form.addRow("Service Life", self._service_life_spin)

        self._used_min_spin = QDoubleSpinBox()
        self._used_min_spin.setRange(0.0, 100000.0)
        self._used_min_spin.setSuffix(" min")
        left_form.addRow("Used", self._used_min_spin)

        left_col.addLayout(left_form)

        left_col.addWidget(_section_label("Notes"))
        self._remark_edit = _AutoSaveTextEdit()
        self._remark_edit.setFixedHeight(70)
        left_col.addWidget(self._remark_edit)
        left_col.addStretch()

        right_col = QVBoxLayout()
        right_col.addWidget(_section_label("Geometry"))
        right_form = QFormLayout(); right_form.setSpacing(8)

        self._diameter_spin = _mm_spin(0.01, 200.0)
        right_form.addRow("Diameter", self._diameter_spin)
        self._cutting_length_spin = _mm_spin(0.0, 500.0)
        right_form.addRow("Cutting Length", self._cutting_length_spin)
        self._shank_dia_spin = _mm_spin(0.0, 200.0)
        right_form.addRow("Shank Diameter", self._shank_dia_spin)
        self._total_len_spin = _mm_spin(0.0, 500.0)
        right_form.addRow("Total Length", self._total_len_spin)
        self._flute_count_spin = QSpinBox()
        self._flute_count_spin.setRange(0, 20)
        right_form.addRow("Flutes", self._flute_count_spin)
        self._corner_r_spin = _mm_spin(0.0, 100.0)
        right_form.addRow("Corner Radius", self._corner_r_spin)
        self._clearance_spin = _deg_spin()
        right_form.addRow("Freiwinkel", self._clearance_spin)
        self._tip_angle_spin = _deg_spin()
        right_form.addRow("Spitzenwinkel", self._tip_angle_spin)
        self._taper_angle_spin = _deg_spin()
        right_form.addRow("Konuswinkel", self._taper_angle_spin)
        self._manufacturer_edit = QLineEdit()
        right_form.addRow("Manufacturer", self._manufacturer_edit)
        self._material_edit = QLineEdit()
        right_form.addRow("Material", self._material_edit)

        right_col.addLayout(right_form)
        right_col.addStretch()

        forms_row.addLayout(left_col, stretch=1)
        forms_row.addLayout(right_col, stretch=1)
        root.addLayout(forms_row, stretch=1)

        # ── Live-preview wiring ──────────────────────────────────────────────
        for spin in (
            self._diameter_spin, self._cutting_length_spin,
            self._shank_dia_spin, self._total_len_spin, self._corner_r_spin,
            self._clearance_spin, self._tip_angle_spin, self._taper_angle_spin,
        ):
            spin.valueChanged.connect(self._push_live_profile)
        self._type_combo.currentIndexChanged.connect(self._push_live_profile)
        self._holder_combo.currentIndexChanged.connect(self._push_live_profile)
        self._name_edit.textChanged.connect(self._update_id_label)
        self._pocket_spin.valueChanged.connect(self._update_id_label)

        # ── Auto-save wiring ─────────────────────────────────────────────────
        # Discrete widgets (spin boxes, combo boxes) are already "final" on
        # every change — valueChanged/currentIndexChanged. Free-text fields
        # use editingFinished/focus-out instead of textChanged so a row
        # isn't rewritten on every keystroke. See _auto_save().
        for spin in (
            self._diameter_spin,self._cutting_length_spin,
            self._shank_dia_spin, self._total_len_spin, self._corner_r_spin,
            self._clearance_spin, self._tip_angle_spin, self._taper_angle_spin,
            self._pocket_spin, self._z_off_spin, self._service_life_spin,
            self._used_min_spin, self._flute_count_spin,
        ):
            spin.valueChanged.connect(self._auto_save)
        self._type_combo.currentIndexChanged.connect(self._auto_save)
        self._holder_combo.currentIndexChanged.connect(self._auto_save)
        self._name_edit.editingFinished.connect(self._auto_save)
        self._manufacturer_edit.editingFinished.connect(self._auto_save)
        self._material_edit.editingFinished.connect(self._auto_save)
        self._remark_edit.focus_out.connect(self._auto_save)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_tool(self, tool: ToolDefinition) -> None:
        """Load *tool* into the form. A deep-enough copy (dataclasses are
        shallow-immutable-by-convention here — nothing on ToolDefinition is
        mutated in place elsewhere) so editing the form can't corrupt the
        caller's object before Save is clicked."""
        self._loading = True
        self._tool = replace(tool)

        self._name_edit.setText(tool.name)
        self._pocket_spin.setValue(tool.pocket if tool.pocket >= 0 else -1)
        self._z_off_spin.setValue(tool.z_offset)
        idx = self._type_combo.findData(tool.tool_type)
        self._type_combo.setCurrentIndex(max(0, idx))
        idx = self._holder_combo.findData(tool.holder_preset)
        self._holder_combo.setCurrentIndex(max(0, idx))
        self._service_life_spin.setValue(tool.service_life_min)
        self._used_min_spin.setValue(tool.used_min)
        self._remark_edit.setPlainText(tool.remark)

        self._diameter_spin.setValue(tool.diameter)
        self._cutting_length_spin.setValue(tool.cutting_length)
        self._shank_dia_spin.setValue(tool.shank_diameter)
        self._total_len_spin.setValue(tool.total_length)
        self._flute_count_spin.setValue(tool.flute_count)
        self._corner_r_spin.setValue(tool.corner_radius)
        self._clearance_spin.setValue(tool.clearance_angle)
        self._tip_angle_spin.setValue(tool.tip_angle)
        self._taper_angle_spin.setValue(tool.taper_angle)
        self._manufacturer_edit.setText(tool.manufacturer)
        self._material_edit.setText(tool.material)

        self._loading = False
        self._update_id_label()
        self._push_live_profile()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_id_label(self, *_args) -> None:
        if self._tool is None:
            return
        pocket = self._pocket_spin.value()
        pocket_txt = str(pocket) if pocket >= 0 else "-"
        name = self._name_edit.text() or self._tool.remark or f"T{self._tool.tool_number}"
        self._id_lbl.setText(f"T{self._tool.tool_number} · Pocket {pocket_txt} · {name}")

    def _collect_form_into(self, base: ToolDefinition) -> ToolDefinition:
        holder_name = self._holder_combo.currentData()
        return replace(
            base,
            name=self._name_edit.text(),
            pocket=self._pocket_spin.value(),
            z_offset=self._z_off_spin.value(),
            tool_type=self._type_combo.currentData(),
            holder_preset=holder_name,
            service_life_min=self._service_life_spin.value(),
            used_min=self._used_min_spin.value(),
            remark=self._remark_edit.toPlainText(),
            diameter=self._diameter_spin.value(),
            cutting_length=self._cutting_length_spin.value(),
            shank_diameter=self._shank_dia_spin.value(),
            total_length=self._total_len_spin.value(),
            flute_count=self._flute_count_spin.value(),
            corner_radius=self._corner_r_spin.value(),
            clearance_angle=self._clearance_spin.value(),
            tip_angle=self._tip_angle_spin.value(),
            taper_angle=self._taper_angle_spin.value(),
            manufacturer=self._manufacturer_edit.text(),
            material=self._material_edit.text(),
        )

    def _push_live_profile(self, *_args) -> None:
        """Live-update the 2D preview from the CURRENT form state, without
        touching the database — see class docstring."""
        if self._loading or self._tool is None:
            return
        preview = self._collect_form_into(self._tool)
        holder_name = self._holder_combo.currentData()
        holder = STANDARD_HOLDERS.get(holder_name) if holder_name else None
        self._profile.set_tool(preview, holder)

    def _auto_save(self, *_args) -> None:
        """Persist the current form state immediately — see class
        docstring's Persistence section. Connected to every field's
        natural "done editing this value" signal."""
        if self._loading or self._tool is None:
            return
        self._tool = self._collect_form_into(self._tool)
        ToolDatabase.instance().upsert_tool(self._tool)

    def _close(self) -> None:
        # Flush once more before leaving: covers a text field the user
        # edited but never tabbed/clicked out of (editingFinished/
        # focus-out wouldn't have fired yet) — spin/combo fields are
        # already saved as of their last valueChanged.
        self._auto_save()
        self.back_requested.emit()


def _mm_spin(lo: float, hi: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setDecimals(2)
    spin.setSuffix(" mm")
    return spin


def _deg_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 180.0)
    spin.setDecimals(1)
    spin.setSuffix(" °")
    return spin
