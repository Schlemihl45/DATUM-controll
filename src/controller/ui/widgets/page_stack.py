"""
ui/widgets/page_stack.py — PageStack: a small push/pop navigation helper
for a page hierarchy nested inside ONE entry of the app-wide QStackedWidget
(main_window.py's self._stack).

Home/Machine/Settings/Tools stay reachable via main_window.py's fixed
top-level indices exactly as before — this class is not a replacement for
that, only an addition for sections (currently: Workpieces) that need more
than one level of navigation (list -> WorkpieceDetailPage ->
ProgramDetailPage, plus ProgramDetailPage -> ProgramDetailPage for an old
version reached from the history list).

Each pushed page carries a title, so a caller (main_window.py's return
button) can show a simple breadcrumb/back label without knowing anything
about the pushed widgets themselves. The base page (index 0) is pushed once
at construction and can never be popped — popping past it is a no-op.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QWidget


class PageStack(QStackedWidget):
    """See module docstring."""

    # Emitted after every push()/pop()/reset() with the new top page's title.
    title_changed = Signal(str)

    def __init__(self, base: QWidget, base_title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._titles: list[str] = []
        self.push(base, base_title)

    def push(self, widget: QWidget, title: str) -> None:
        """Add *widget* on top of the stack and make it current."""
        self.addWidget(widget)
        self._titles.append(title)
        self.setCurrentWidget(widget)
        self.title_changed.emit(self.current_title)

    def pop(self) -> QWidget | None:
        """Remove and return the current top page, revealing the one below
        it. No-op (returns None) if only the base page is left — the base
        page is never popped."""
        if not self.can_pop():
            return None
        widget = self.currentWidget()
        self.removeWidget(widget)
        widget.deleteLater()
        self._titles.pop()
        self.setCurrentWidget(self.widget(self.count() - 1))
        self.title_changed.emit(self.current_title)
        return widget

    def can_pop(self) -> bool:
        """True while there is a non-base page on top to pop."""
        return len(self._titles) > 1

    def reset(self) -> None:
        """Pop back down to the base page — used when the whole section is
        left (e.g. via the app-wide Home/Return button) so the next visit
        starts fresh at the list rather than wherever navigation was left."""
        while self.can_pop():
            self.pop()

    @property
    def current_title(self) -> str:
        return self._titles[-1] if self._titles else ""
