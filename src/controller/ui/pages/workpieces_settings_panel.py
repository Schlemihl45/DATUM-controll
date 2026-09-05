"""
ui/pages/workpieces_settings_panel.py — "Workpieces" settings section:
the folder-sync root (persistence/workpiece_sync.py) and the default
folder the "Programm laden" file picker (ui/pages/workpiece_detail_page.py)
opens in (e.g. a USB stick's mount point).

Follows the exact widget/sync pattern tools_settings_panel.py and
sim_panel.py's tabs already use (write side: widget signal ->
setattr(s, ...); read side: AppSettings' _changed signal -> sync the
widget back) — see tools_settings_panel.py's module docstring for the
full rationale.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from controller.sim.core.settings import AppSettings

SECTION_ICONS: list[tuple[str, str]] = [
    ("workpieces", "Ordner"),
]


def _sync_line_edit(edit: QLineEdit, value: str) -> None:
    if edit.text() != value:
        edit.blockSignals(True)
        edit.setText(value)
        edit.blockSignals(False)


def _path_row(initial: str, browse_caption: str) -> tuple[QWidget, QLineEdit]:
    row = QHBoxLayout()
    edit = QLineEdit(initial)
    row.addWidget(edit, stretch=1)
    browse_btn = QPushButton("Durchsuchen …")
    row.addWidget(browse_btn)

    def _browse() -> None:
        chosen = QFileDialog.getExistingDirectory(
            edit, browse_caption, edit.text() or "",
        )
        if chosen:
            edit.setText(chosen)

    browse_btn.clicked.connect(_browse)

    widget = QWidget()
    widget.setLayout(row)
    return widget, edit


class _WorkpiecesFoldersTab(QWidget):
    """Root folder for the folder <-> DB sync, and the default folder the
    "Programm laden" file picker opens in."""

    def __init__(self, s: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel("Werkstück-Ordner")
        hdr.setObjectName("CardTitle")
        root.addWidget(hdr)

        form = QFormLayout()
        form.setSpacing(8)

        sync_row, self._sync_edit = _path_row(
            s.workpieces_root_path, "Werkstück-Wurzelordner wählen",
        )
        self._sync_edit.setToolTip(
            "Jeder direkte Unterordner darin wird als eigenes Werkstück "
            "synchronisiert (siehe persistence/workpiece_sync.py)."
        )
        form.addRow("Wurzelordner (Sync)", sync_row)

        explorer_row, self._explorer_edit = _path_row(
            s.workpieces_explorer_root_path, "Standardordner für \"Programm laden\" wählen",
        )
        self._explorer_edit.setToolTip(
            "Ordner, den der Datei-Explorer beim Klick auf \"Programm laden\" "
            "standardmäßig öffnet — z. B. der Mount-Punkt eines USB-Sticks. "
            "Leer lässt den Explorer seinen eigenen Standard verwenden."
        )
        form.addRow("Standardordner (Explorer)", explorer_row)

        root.addLayout(form)
        root.addStretch()

        # Write side
        self._sync_edit.textChanged.connect(lambda v: setattr(s, "workpieces_root_path", v))
        self._explorer_edit.textChanged.connect(
            lambda v: setattr(s, "workpieces_explorer_root_path", v)
        )

        # Read side (cross-instance sync — see module docstring)
        s.workpieces_root_path_changed.connect(lambda v: _sync_line_edit(self._sync_edit, v))
        s.workpieces_explorer_root_path_changed.connect(
            lambda v: _sync_line_edit(self._explorer_edit, v)
        )


def build_workpieces_sections(parent: QWidget | None = None) -> list[QWidget]:
    """Build a fresh set of section widgets, in SECTION_ICONS order — same
    factory-function shape as tools_settings_panel.py's
    build_tools_sections(), so settings_page.py wires this section in
    identically."""
    s = AppSettings.instance()
    return [_WorkpiecesFoldersTab(s, parent)]
