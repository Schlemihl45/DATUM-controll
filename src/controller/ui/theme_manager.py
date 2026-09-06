"""
ui/theme_manager.py — Application theme manager.

Replaces the single-file theme_loader.py with a multi-theme system that:

  1. Loads .qss stylesheets from ui/resources/styles/
  2. Applies the selected stylesheet to the QApplication
  3. Notifies all registered widgets (currently just Viewport) about
     the window-gradient colors so the corner-fill shader stays in sync
  4. Persists the user's last-used theme via QSettings

Available themes are discovered automatically from the styles/ directory
(any .qss file whose name doesn't start with '_'). The display name shown
in the UI is derived from the filename stem: "dark" → "Dark", "light" → "Light".

Usage:
    mgr = ThemeManager(qt_app)
    mgr.apply_theme("dark")   # on startup (restores last if arg omitted)

    # In SettingsPage:
    mgr.available_themes()    # → ["dark", "light"]
    mgr.current_theme         # → "dark"
    mgr.apply_theme("light")
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Signal, QObject
from PySide6.QtWidgets import QApplication

from controller.ui import icon_loader

logger = logging.getLogger(__name__)

# Directory containing all .qss theme files
_STYLES_DIR = Path(__file__).resolve().parent / "resources" / "styles"

# Per-theme gradient colors used by Viewport.set_window_gradient().
# Must match the QMainWindow background defined in each .qss file.
_THEME_WINDOW_GRADIENT: dict[str, tuple[str, str]] = {
    "dark":  ("#141b26", "#0b0f16"),
    "light": ("#f0f4f8", "#dce4ef"),
}

_SETTINGS_KEY = "ui/theme"
_DEFAULT_THEME = "dark"


class ThemeManager(QObject):
    """Manages application-wide QSS theme switching.

    Signals:
        theme_changed(str): Emitted after a new theme is applied.
                            Argument is the theme name key (e.g. "dark").
    """

    theme_changed = Signal(str)

    def __init__(self, app: QApplication, parent=None) -> None:
        super().__init__(parent)
        self._app      = app
        self._current  = ""
        self._viewports: list = []   # Viewport instances to update on switch

        # Persist across sessions
        self._settings = QSettings("DatumControl", "DatumControl")

    # ── Public API ────────────────────────────────────────────────────────────

    def available_themes(self) -> list[str]:
        """Return the sorted list of available theme names (stems of .qss files)."""
        return sorted(
            p.stem for p in _STYLES_DIR.glob("*.qss")
            if not p.stem.startswith("_")
        )

    @property
    def current_theme(self) -> str:
        """The currently active theme name."""
        return self._current

    def display_name(self, theme: str) -> str:
        """Human-readable label for a theme key (e.g. "dark" → "Dark")."""
        return theme.replace("_", " ").title()

    def apply_theme(self, theme: str | None = None) -> None:
        """Load and apply a .qss theme by name.

        If *theme* is None, restores the last-saved theme (or the default).
        Logs a warning and falls back to the default if the file is missing.

        Args:
            theme: Theme name matching a .qss filename stem, or None to restore.
        """
        if theme is None:
            theme = self._settings.value(_SETTINGS_KEY, _DEFAULT_THEME)

        qss_path = _STYLES_DIR / f"{theme}.qss"

        if not qss_path.exists():
            logger.warning("Theme file not found: %s — falling back to %s", qss_path, _DEFAULT_THEME)
            if theme != _DEFAULT_THEME:
                self.apply_theme(_DEFAULT_THEME)
            return

        try:
            qss = qss_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Could not read theme file %s: %s", qss_path, exc)
            return

        self._app.setStyleSheet(qss)
        self._current = theme
        self._settings.setValue(_SETTINGS_KEY, theme)

        # Newly loaded tint=True icons should read their color from THIS
        # theme's file from now on (see icon_loader.set_active_theme_path's
        # docstring for the known live-icon-refresh limitation this does
        # NOT solve).
        icon_loader.set_active_theme_path(qss_path)

        # Update Viewport corner-fill shaders to match new window gradient
        top, bottom = _THEME_WINDOW_GRADIENT.get(theme, ("#141b26", "#0b0f16"))
        for viewport in self._viewports:
            try:
                viewport.set_window_gradient(top, bottom)
            except Exception as exc:
                logger.debug("Could not update viewport gradient: %s", exc)

        self.theme_changed.emit(theme)
        logger.debug("Applied theme: %s", theme)

    def register_viewport(self, viewport) -> None:
        """Register a Viewport instance to receive gradient updates on theme switch.

        Call this once per Viewport after it's constructed. Safe to call
        multiple times — duplicates are ignored.

        Args:
            viewport: A controller.sim.ui.viewport.Viewport instance.
        """
        if viewport not in self._viewports:
            self._viewports.append(viewport)

    def unregister_viewport(self, viewport) -> None:
        """Remove a previously registered viewport (e.g. on page destruction)."""
        try:
            self._viewports.remove(viewport)
        except ValueError:
            pass
