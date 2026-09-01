"""
ui/theme_loader.py — Loads the global QSS stylesheet.

No dynamic theme switching (removed from scope) — this loads
one fixed stylesheet at startup.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication


_QSS_PATH = Path(__file__).parent / "resources" / "styles" / "main.qss"


def load_stylesheet(app: QApplication) -> None:
    """Read main.qss and apply it to the whole application."""
    try:
        stylesheet = _QSS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Fail loud during development — a silently unstyled app
        # is harder to debug than a crash at startup.
        raise FileNotFoundError(
            f"Stylesheet not found at {_QSS_PATH}. "
            "Check that ui/resources/styles/main.qss exists."
        )
    app.setStyleSheet(stylesheet)