"""
ui/widgets/gcode_highlighter.py — Syntax highlighting for G-code display.

One QRegularExpression finds all G/M/T tokens; the numeric value (not
leading zeros) decides the color, so G0/G00/G000 all land in the same
bucket. Comments are applied last so they override code coloring
inside parentheses or after a semicolon.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QTextDocument


class GCodeHighlighter(QSyntaxHighlighter):

    _CODE_RE = QRegularExpression(r"\b([GMT])(\d+)(\.\d+)?")
    _COMMENT_PAREN_RE = QRegularExpression(r"\([^)]*\)")
    _COMMENT_SEMI_RE = QRegularExpression(r";.*$")

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)

        def make_fmt(hex_color: str, italic: bool = False, bold: bool = False) -> QTextCharFormat:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(hex_color))
            if italic:
                fmt.setFontItalic(True)
            if bold:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            return fmt

        self._fmt_rapid = make_fmt("#C97064")           # G0
        self._fmt_linear = make_fmt("#5FA8E0")           # G1
        self._fmt_arc = make_fmt("#4FBF9E")              # G2/G3
        self._fmt_g_other = make_fmt("#8fa0ba")          # restliche G-Codes
        self._fmt_m = make_fmt("#9B8FD9")                # M-Codes
        self._fmt_t = make_fmt("#D97B3F", bold=True)     # T-Codes
        self._fmt_comment = make_fmt("#5f6b7a", italic=True)

    def highlightBlock(self, text: str) -> None:
        it = self._CODE_RE.globalMatch(text)
        while it.hasNext():
            m = it.next()
            letter = m.captured(1)
            number = int(m.captured(2))
            start, length = m.capturedStart(), m.capturedLength()

            if letter == "G":
                if number == 0:
                    fmt = self._fmt_rapid
                elif number == 1:
                    fmt = self._fmt_linear
                elif number in (2, 3):
                    fmt = self._fmt_arc
                else:
                    fmt = self._fmt_g_other
            elif letter == "M":
                fmt = self._fmt_m
            else:  # "T"
                fmt = self._fmt_t

            self.setFormat(start, length, fmt)

        # Kommentare zuletzt — überschreiben Code-Färbung innerhalb des Kommentars
        for regex in (self._COMMENT_PAREN_RE, self._COMMENT_SEMI_RE):
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), self._fmt_comment)