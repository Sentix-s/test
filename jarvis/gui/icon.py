"""The JARVIS icon, drawn in code (also exported to .ico for the installer)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient


def make_pixmap(size: int = 256, color: str = "#33d6ff") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    col = QColor(color)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#070b14"))
    p.drawEllipse(QPointF(c, c), c * 0.98, c * 0.98)
    pen = QPen(col, size * 0.07)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    rect = QRectF(c - c * 0.72, c - c * 0.72, c * 1.44, c * 1.44)
    for i in range(3):
        p.drawArc(rect, int((90 + i * 120 + 15) * 16), int(90 * 16))
    g = QRadialGradient(QPointF(c, c), c * 0.42)
    g.setColorAt(0, QColor("white"))
    g.setColorAt(0.5, col)
    end = QColor(col)
    end.setAlphaF(0)
    g.setColorAt(1, end)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(c, c), c * 0.42, c * 0.42)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_pixmap(s))
    return icon
