"""
ui/widgets/gcode_highlighter.py — Syntax highlighting for G-code display.

One QRegularExpression finds all G/M/T tokens; the numeric value (not
leading zeros) decides the color, so G0/G00/G000 all land in the same
bucket. Comments are applied last so they override code coloring
inside parentheses or after a semicolon.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument


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

        self._fmt_rapid = make_fmt("#FF5C5C", bold=True)  # G0 — Kräftiges Alarm-Rot für Eilgang
        self._fmt_linear = make_fmt("#3EA6FF",bold=True  )  # G1 — Leuchtendes Hellblau für lineare Bewegungen
        self._fmt_arc = make_fmt("#2EE59D",bold=True)  # G2/G3 — Knalliges Mintgrün für Kreisbögen
        self._fmt_g_other = make_fmt("#B2BEC3", bold=True)  # restliche G-Codes — Klares, helles Grau-Blau
        self._fmt_m = make_fmt("#A29BFE", bold=True)  # M-Codes — Modernes, kräftiges Violett
        self._fmt_t = make_fmt("#FF9F43", bold=True)  # T-Codes — Lebendiges, auffälliges Orange
        self._fmt_comment = make_fmt("#718093", italic=True)  # Kommentare — Angenehm lesbares Mittelgrau

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