"""The animated glowing JARVIS core (arc-reactor style), drawn with QPainter.

Rotation speed, glow and color follow the assistant state; the inner ring
pulses with the microphone level or with JARVIS's own speech.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from jarvis.gui.theme import STATE_COLORS

_SPEED = {"off": 0.0, "sleep": 0.3, "ready": 0.3, "starting": 2.0, "active": 1.0, "processing": 2.4,
          "busy": 2.4, "speaking": 1.4, "error": 0.0}


class CoreWidget(QWidget):
    def __init__(self, parent: QWidget | None = None, fps: int = 60) -> None:
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.state = "off"
        self.level = 0.0       # 0..1 input/output loudness
        self._shown_level = 0.0
        self._angle = 0.0
        self._t = 0.0
        self._color = QColor(STATE_COLORS["off"])
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(int(1000 / fps))

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def _step(self) -> None:
        self._t += 0.016
        self._angle = (self._angle + _SPEED.get(self.state, 1.0) * 1.6) % 360
        self._shown_level += (self.level - self._shown_level) * 0.25
        target = QColor(STATE_COLORS.get(self.state, STATE_COLORS["active"]))
        c = self._color  # smooth color transition between states
        self._color = QColor(int(c.red() + (target.red() - c.red()) * 0.1),
                             int(c.green() + (target.green() - c.green()) * 0.1),
                             int(c.blue() + (target.blue() - c.blue()) * 0.1))
        if self.isVisible():
            self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        cx, cy = self.width() / 2, self.height() / 2
        r = side / 2
        col = self._color
        breathe = 0.5 + 0.5 * math.sin(self._t * (1.2 if self.state in ("sleep", "ready") else 3.0))
        glow = 0.25 + 0.25 * breathe + 0.5 * self._shown_level
        if self.state == "off":
            glow = 0.08

        # outer glow
        grad = QRadialGradient(QPointF(cx, cy), r)
        g = QColor(col)
        g.setAlphaF(min(1.0, 0.35 * glow))
        grad.setColorAt(0.35, g)
        g2 = QColor(col)
        g2.setAlphaF(0.0)
        grad.setColorAt(1.0, g2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(cx, cy), r, r)

        def ring(radius: float, width: float, alpha: float, start: float, span: float, segments: int) -> None:
            c = QColor(col)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            pen = QPen(c, width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            step = 360 / segments
            for i in range(segments):
                p.drawArc(rect, int((start + i * step) * 16), int(span * step * 16))

        ring(r * 0.86, max(2.0, r * 0.03), 0.5 + 0.3 * glow, self._angle, 0.7, 6)
        ring(r * 0.74, max(1.5, r * 0.018), 0.35 + 0.3 * glow, -self._angle * 1.5, 0.45, 12)
        ring(r * 0.62, max(3.0, r * 0.05), 0.6 + 0.4 * glow, self._angle * 0.6, 0.82, 3)
        # level ring (voice)
        lr = r * (0.42 + 0.12 * self._shown_level)
        ring(lr, max(2.0, r * 0.025), 0.4 + 0.6 * self._shown_level, 0, 1.0, 1)

        # core
        core = QRadialGradient(QPointF(cx, cy), r * 0.36)
        white = QColor(255, 255, 255)
        white.setAlphaF(0.85 if self.state != "off" else 0.15)
        core.setColorAt(0.0, white)
        cc = QColor(col)
        cc.setAlphaF(0.9 if self.state != "off" else 0.3)
        core.setColorAt(0.45, cc)
        cc2 = QColor(col)
        cc2.setAlphaF(0.0)
        core.setColorAt(1.0, cc2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(core)
        p.drawEllipse(QPointF(cx, cy), r * 0.36, r * 0.36)
        p.end()
