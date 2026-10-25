#!/usr/bin/env python3
"""
eternal_timer_gui.py -- Eternal Timer v3

A fully custom-painted Qt (PySide6) interface:
  * HH:MM:SS time picker you can type into, scroll, drag or click, + presets
  * shrinking gradient progress ring with a glowing head, a 60-tick dial,
    odometer-style rolling digits, a last-10-seconds pulse, and a
    confetti/ripple/check-mark finish state
  * stopwatch ring with a comet sweeping once per minute
  * animated aurora + drifting particles background, shimmering start button,
    sliding segmented control, lifted cards with staggered entrance

Shares ~/.local/share/eternal-timer/state.json with the `etimer` CLI.
"""

import html
import math
import random
import signal
import sys
import time
from datetime import datetime

import threading
from PySide6.QtCore import (
    QObject, Qt, QTimer, QPoint, QPointF, QRectF, Signal, QVariantAnimation,
    QEasingCurve, QPropertyAnimation, QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QAction, QIcon, QPainter, QColor, QFont, QFontDatabase, QFontMetrics,
    QPixmap, QLinearGradient, QRadialGradient, QConicalGradient, QPen,
    QBrush, QPainterPath, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLayout,
    QLabel, QPushButton, QLineEdit, QScrollArea, QFrame, QSystemTrayIcon,
    QMenu, QToolButton, QGraphicsOpacityEffect, QSizePolicy, QDialog,
    QTextBrowser, QCheckBox,
)

import eternal_timer_core as core
try:
    import eternal_timer_sync as sync
except Exception:          # sync is optional (needs python-cryptography)
    sync = None

# ---------------------------------------------------------------- palette

VIOLET = QColor("#7c6cf0")
LAVENDER = QColor("#a78bfa")
PINK = QColor("#ec6eaa")
TEAL = QColor("#58e0b0")
AMBER = QColor("#f5b85a")
RED = QColor("#ef6a7e")
SKY = QColor("#4aa8e8")
TEXT = QColor("#f4f2ff")
SUBTEXT = QColor(157, 154, 184)
BG_TOP = QColor("#171826")
BG_BOTTOM = QColor("#0c0d15")

ACCENT_HEX = "#7c6cf0"
TEXT_HEX = "#f4f2ff"
SUBTEXT_HEX = "#9d9ab8"
GOOD = TEAL
WARN = RED

# name -> (label, primary, secondary, soft, extra glow)
THEMES = {
    "aurora": ("Aurora", "#7c6cf0", "#ec6eaa", "#a78bfa", "#4aa8e8"),
    "ocean":  ("Ocean",  "#3b82f6", "#22d3ee", "#7dd3fc", "#6366f1"),
    "sunset": ("Sunset", "#f97316", "#ec4899", "#fdba74", "#a855f7"),
    "mint":   ("Mint",   "#10b981", "#a3e635", "#6ee7b7", "#06b6d4"),
}
THEME_ORDER = ["aurora", "ocean", "sunset", "mint"]
CALM = False          # reduced-motion mode (set from settings)


def theme_colors(name):
    _label, v, p, l, s = THEMES.get(name, THEMES["aurora"])
    return [QColor(v), QColor(p), QColor(l), QColor(s)]


def apply_colors(cols):
    """Swap the live accent palette. Painting code reads these module
    globals at paint time, so a repaint picks the new colours up."""
    global VIOLET, PINK, LAVENDER, SKY, ACCENT_HEX
    VIOLET, PINK, LAVENDER, SKY = cols
    ACCENT_HEX = VIOLET.name()


UI_FONT = "Sans"


def init_fonts(app):
    """Pick the nicest installed UI font and make it the app default."""
    global UI_FONT
    fams = set(QFontDatabase.families())
    for f in ("Inter", "Cantarell", "Noto Sans", "Ubuntu", "Segoe UI", "DejaVu Sans"):
        if f in fams:
            UI_FONT = f
            break
    else:
        UI_FONT = app.font().family()
    app.setFont(QFont(UI_FONT, 10))


def ui_font(px, weight=QFont.Weight.Normal):
    f = QFont(UI_FONT)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease_out(t):
    return 1 - (1 - clamp(t)) ** 3


def alpha(c, a):
    d = QColor(c)
    d.setAlpha(int(clamp(a, 0, 255)))
    return d


def lerp_color(c1, c2, t):
    t = clamp(t)
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
        int(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
    )


def clock(ts, now=None):
    """'14:32', or 'Oct 21 14:32' when not today."""
    if ts is None:
        return "?"
    now = now if now is not None else time.time()
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    return d.strftime("%H:%M") if d.date() == n.date() else d.strftime("%b %d %H:%M")


def make_app_icon():
    pix = QPixmap(128, 128)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 128, 128)
    grad.setColorAt(0, LAVENDER)
    grad.setColorAt(1, PINK)
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawEllipse(8, 8, 112, 112)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPointSize(52)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "\u221e")
    p.end()
    return QIcon(pix)


# ---------------------------------------------------------------- rolling digits

class RollingText:
    """Text whose characters slide vertically (odometer style) when they
    change. Digits use a fixed cell width so nothing jitters."""

    DUR = 0.30

    def __init__(self, text=""):
        self.cur = list(text)
        self.prev = [""] * len(text)
        self.t0 = [0.0] * len(text)
        self.dirs = [1] * len(text)

    def text(self):
        return "".join(self.cur)

    def set_text(self, text, direction=1, animate=True):
        now = time.monotonic()
        if len(text) != len(self.cur):
            self.cur = list(text)
            self.prev = [""] * len(text)
            self.t0 = [0.0] * len(text)
            self.dirs = [1] * len(text)
            return True
        changed = False
        for i, ch in enumerate(text):
            if ch != self.cur[i]:
                self.prev[i] = self.cur[i] if animate else ""
                self.t0[i] = now
                self.dirs[i] = direction
                self.cur[i] = ch
                changed = True
        return changed

    def animating(self):
        now = time.monotonic()
        return any(self.prev[i] and now - self.t0[i] < self.DUR for i in range(len(self.cur)))

    def paint(self, p, rect, font, color, opacity=1.0):
        now = time.monotonic()
        fm = QFontMetrics(font)
        dw = fm.horizontalAdvance("0")
        widths = [dw if ch.isdigit() else fm.horizontalAdvance(ch) for ch in self.cur]
        x = rect.center().x() - sum(widths) / 2.0
        shift = rect.height() * 0.55
        p.save()
        p.setClipRect(rect)
        p.setFont(font)
        for i, ch in enumerate(self.cur):
            w = widths[i]
            t = (now - self.t0[i]) / self.DUR if self.prev[i] else 1.0
            if t < 1.0:
                e = ease_out(t)
                d = self.dirs[i]
                p.setPen(alpha(color, color.alpha() * opacity * (1 - e)))
                p.drawText(QRectF(x, rect.top() - d * e * shift, w, rect.height()),
                           Qt.AlignCenter, self.prev[i])
                p.setPen(alpha(color, color.alpha() * opacity * e))
                p.drawText(QRectF(x, rect.top() + d * (1 - e) * shift, w, rect.height()),
                           Qt.AlignCenter, ch)
            else:
                self.prev[i] = ""
                p.setPen(alpha(color, color.alpha() * opacity))
                p.drawText(QRectF(x, rect.top(), w, rect.height()), Qt.AlignCenter, ch)
            x += w
        p.restore()


# ---------------------------------------------------------------- small controls

class GhostButton(QToolButton):
    """Round (or pill) button with an animated hover tint."""

    def __init__(self, text, diameter=32, danger=False, width=None, font_px=13):
        super().__init__()
        self.setText(text)
        if width is None:
            self.setFixedSize(diameter, diameter)
        else:
            self.setFixedHeight(diameter)
            if width == 0:
                self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            else:
                self.setFixedWidth(width)
        self._radius = diameter // 2
        self._font_px = font_px
        self.setCursor(Qt.PointingHandCursor)
        self._base0 = QColor(255, 255, 255, 18)
        self._hover0 = QColor(224, 108, 117, 60) if danger else QColor(255, 255, 255, 48)
        self._base, self._hover = self._base0, self._hover0
        self._fg = TEXT_HEX
        self._current = self._base
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._apply)
        self._apply(self._base)

    def _apply(self, color):
        self._current = color
        self.setStyleSheet(f"""
            QToolButton {{
                background: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});
                color: {self._fg};
                border: none;
                border-radius: {self._radius}px;
                font-size: {self._font_px}px;
                font-weight: 600;
            }}
        """)

    def set_active(self, active):
        self._active = active
        self._base = alpha(VIOLET, 100) if active else self._base0
        self._hover = alpha(VIOLET, 150) if active else self._hover0
        self._fg = "#ffffff" if active else TEXT_HEX
        self._apply(self._base)

    def _animate_to(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._current)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event):
        self._animate_to(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_to(self._base)
        super().leaveEvent(event)


class SoundButton(GhostButton):
    """Title-bar sound toggle. ON: violet note on a violet tint.
    OFF: dim note with a red slash through it on a red tint."""

    def __init__(self, diameter=28):
        super().__init__("\u266a", diameter)
        self._off_base = QColor(224, 108, 117, 55)
        self._off_hover = QColor(224, 108, 117, 90)
        self._muted = False
        self.set_muted(False)

    def set_muted(self, muted):
        self._muted = muted
        self._base = self._off_base if muted else alpha(VIOLET, 70)
        self._hover = self._off_hover if muted else alpha(VIOLET, 110)
        self._fg = SUBTEXT_HEX if muted else LAVENDER.lighter(135).name()
        self._apply(self._base)
        self.update()

    def retheme(self):
        self.set_muted(self._muted)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._muted:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(WARN, 2, Qt.SolidLine, Qt.RoundCap))
            m = self.width() * 0.27
            p.drawLine(int(m), int(self.height() - m), int(self.width() - m), int(m))
            p.end()


class StatusPill(QLabel):
    @staticmethod
    def _color(text):
        return {"running": LAVENDER, "paused": AMBER, "finished": TEAL}.get(text, SUBTEXT)

    def retheme(self):
        last, self._last = self._last, None
        if last:
            self.set_status(last)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)
        self._last = None
        self.set_status("running")

    def set_status(self, text):
        if text == self._last:
            return
        self._last = text
        color = self._color(text)
        self.setText(text.upper())
        self.setMinimumWidth(62)
        self.setStyleSheet(f"""
            QLabel {{
                color: {color.name()};
                background: rgba({color.red()}, {color.green()}, {color.blue()}, 34);
                border-radius: 10px;
                font-size: 10px; font-weight: 700; letter-spacing: 1px;
                padding: 2px 9px;
            }}
        """)


class AddButton(QPushButton):
    """Gradient call-to-action with a slow shimmer and a click ripple."""

    def __init__(self):
        super().__init__("Start timer")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(172, 46)
        self._hover = False
        self._down = False
        self._ripple = None
        self._ok_t0 = -10.0
        self.setMouseTracking(True)

    def celebrate(self):
        self._ok_t0 = time.monotonic()

    def set_mode(self, mode):
        self.setText("Start timer" if mode == "Timer" else "Start stopwatch")

    def enterEvent(self, e):
        self._hover = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._down = False
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._down = True
        self._ripple = (e.position(), time.monotonic())
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._down = False
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._down:
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 15, 15)

        # soft glow underneath
        for i in range(5):
            gp = QPainterPath()
            gp.addRoundedRect(r.adjusted(-i * 1.6, -i * 1.6 + 3, i * 1.6, i * 1.6 + 3), 15 + i, 15 + i)
            p.fillPath(gp, alpha(VIOLET, (22 if self._hover else 14) * (1 - i / 5)))

        g = QLinearGradient(r.left(), 0, r.right(), 0)
        ok = clamp(1 - (now - self._ok_t0) / 1.0)
        ok = min(1.0, ok * 3) if ok > 0 else 0.0
        g.setColorAt(0, lerp_color(VIOLET.lighter(112) if self._hover else VIOLET, TEAL, ok))
        g.setColorAt(1, lerp_color(PINK.lighter(108) if self._hover else PINK, QColor("#7be8c4"), ok))
        p.fillPath(path, g)

        p.save()
        p.setClipPath(path)
        phase = (now % 3.4) / 3.4
        x = -r.width() * 0.4 + phase * r.width() * 1.8
        sg = QLinearGradient(x, 0, x + r.width() * 0.35, 0)
        sg.setColorAt(0, QColor(255, 255, 255, 0))
        sg.setColorAt(0.5, QColor(255, 255, 255, 55))
        sg.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, sg)
        if self._ripple:
            pos, t0 = self._ripple
            t = (now - t0) / 0.55
            if t < 1:
                rad = ease_out(t) * r.width() * 0.7
                p.setBrush(alpha(QColor("white"), 90 * (1 - t)))
                p.setPen(Qt.NoPen)
                p.drawEllipse(pos, rad, rad)
            else:
                self._ripple = None
        p.restore()

        p.setPen(QColor("white"))
        p.setFont(ui_font(14, QFont.Weight.Bold))
        p.drawText(r, Qt.AlignCenter, "\u2713  Started" if ok > 0.02 else "\u25b6  " + self.text())
        p.end()


class SegmentedControl(QWidget):
    def __init__(self, options, on_change):
        super().__init__()
        self.options = options
        self.on_change = on_change
        self.index = 0
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self.buttons = []
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.clicked.connect(lambda _, idx=i: self.set_index(idx))
            lay.addWidget(b)
            self.buttons.append(b)
        self._pill = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._on_anim)
        self._restyle()

    def _on_anim(self, r):
        self._pill = r
        self.update()

    def _restyle(self):
        for i, b in enumerate(self.buttons):
            color = "#ffffff" if i == self.index else SUBTEXT_HEX
            b.setStyleSheet(f"QPushButton {{ color: {color}; background: transparent; border: none;"
                            f" font-weight: 700; font-size: 13px; }}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._snap)

    def _target(self, idx):
        g = self.buttons[idx].geometry()
        return QRectF(g.x(), 4, g.width(), self.height() - 8)

    def _snap(self):
        self._pill = self._target(self.index)
        self.update()

    def set_index(self, idx):
        if idx == self.index:
            return
        self.index = idx
        self._anim.stop()
        self._anim.setStartValue(self._pill)
        self._anim.setEndValue(self._target(idx))
        self._anim.start()
        self._restyle()
        self.on_change(self.options[idx])

    def current(self):
        return self.options[self.index]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(255, 255, 255, 14))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if not self._pill.isEmpty():
            pr = self._pill
            g = QLinearGradient(pr.left(), 0, pr.right(), 0)
            g.setColorAt(0, VIOLET)
            g.setColorAt(1, lerp_color(VIOLET, PINK, 0.65))
            path = QPainterPath()
            path.addRoundedRect(pr, pr.height() / 2, pr.height() / 2)
            for i in range(4):
                gp = QPainterPath()
                gp.addRoundedRect(pr.adjusted(-i * 1.5, -i * 1.5 + 2, i * 1.5, i * 1.5 + 2),
                                  pr.height() / 2 + i, pr.height() / 2 + i)
                p.fillPath(gp, alpha(VIOLET, 22 * (1 - i / 4)))
            p.fillPath(path, g)
        p.end()


# ---------------------------------------------------------------- time picker

class TimePicker(QWidget):
    """Big HH:MM:SS display. Type digits (microwave style: 2 5 0 0 -> 25:00),
    scroll over a segment, drag it up/down, click the arrows, or use the
    arrow keys. Digits roll when they change."""

    changed = Signal(int)
    submitted = Signal()

    SEG_W = 100
    COL_W = 24
    UNITS = (3600, 60, 1)
    NAMES = ("hours", "minutes", "seconds")

    def __init__(self):
        super().__init__()
        self.setFixedHeight(108)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.vals = [0, 25, 0]
        self.rolls = [RollingText("00"), RollingText("25"), RollingText("00")]
        self.sel = 1
        self.hover = -1
        self.hover_zone = 0
        self._typing = False
        self._drag = None
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)

    # ---- value
    def value(self):
        h, m, s = self.vals
        return h * 3600 + m * 60 + s

    def set_seconds(self, total, animate=True, typing=False):
        total = int(clamp(total, 0, 99 * 3600 + 59 * 60 + 59))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        self._apply([h, m, s], animate)
        self._typing = typing

    def _apply(self, vals, animate=True):
        old = self.vals
        if vals == old:
            return
        for i in range(3):
            if vals[i] != old[i]:
                self.rolls[i].set_text(f"{vals[i]:02d}", 1 if vals[i] > old[i] else -1, animate)
        self.vals = vals
        self._anim_timer.start(16)
        self.update()
        self.changed.emit(self.value())

    def step(self, seg, delta):
        self._typing = False
        self.sel = seg
        self.set_seconds(self.value() + delta * self.UNITS[seg])

    # ---- geometry
    def _total_w(self):
        return 3 * self.SEG_W + 2 * self.COL_W

    def _seg_rect(self, i):
        x0 = (self.width() - self._total_w()) / 2.0
        return QRectF(x0 + i * (self.SEG_W + self.COL_W), 10, self.SEG_W, 72)

    def _seg_at(self, pos):
        for i in range(3):
            if self._seg_rect(i).adjusted(-self.COL_W / 2, 0, self.COL_W / 2, 0).contains(pos):
                return i
        return -1

    def _zone(self, i, pos):
        r = self._seg_rect(i)
        if pos.y() < r.top() + 22:
            return 1
        if pos.y() > r.bottom() - 22:
            return -1
        return 0

    # ---- events
    def mousePressEvent(self, e):
        self.setFocus()
        i = self._seg_at(e.position())
        if i < 0 or e.button() != Qt.LeftButton:
            return
        self.sel = i
        self._typing = False
        z = self._zone(i, e.position())
        if z:
            self.step(i, z)
        else:
            self._drag = (i, e.position().y(), self.value())
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag:
            i, y0, v0 = self._drag
            steps = int((y0 - pos.y()) / 9)
            self.set_seconds(v0 + steps * self.UNITS[i])
            self.setCursor(Qt.SizeVerCursor)
            return
        i = self._seg_at(pos)
        z = self._zone(i, pos) if i >= 0 else 0
        if i != self.hover or z != self.hover_zone:
            self.hover, self.hover_zone = i, z
            self.setCursor(Qt.PointingHandCursor if z else (Qt.SizeVerCursor if i >= 0 else Qt.ArrowCursor))
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def leaveEvent(self, e):
        self.hover, self.hover_zone = -1, 0
        self.update()

    def wheelEvent(self, e):
        i = self._seg_at(e.position())
        if i < 0:
            i = self.sel
        d = 1 if e.angleDelta().y() > 0 else -1
        if e.modifiers() & Qt.ShiftModifier:
            d *= 5
        self.step(i, d)
        e.accept()

    def focusOutEvent(self, e):
        self._typing = False
        self.update()
        super().focusOutEvent(e)

    def focusInEvent(self, e):
        self.update()
        super().focusInEvent(e)

    def keyPressEvent(self, e):
        k, t = e.key(), e.text()
        if len(t) == 1 and t.isdigit():
            if self._typing:
                h, m, s = self.vals
                raw = f"{h:02d}{m:02d}{s:02d}"
            else:
                raw = "000000"
            raw = (raw + t)[-6:]
            vals = [int(raw[:2]), min(59, int(raw[2:4])), min(59, int(raw[4:]))]
            self._apply(vals)
            self._typing = True
        elif k == Qt.Key_Backspace:
            if self._typing:
                h, m, s = self.vals
                raw = "0" + f"{h:02d}{m:02d}{s:02d}"[:-1]
                self._apply([int(raw[:2]), int(raw[2:4]), int(raw[4:])])
            else:
                self.set_seconds(0)
        elif k in (Qt.Key_Delete, Qt.Key_Escape):
            self.set_seconds(0)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.submitted.emit()
        elif k == Qt.Key_Up:
            self.step(self.sel, 1)
        elif k == Qt.Key_Down:
            self.step(self.sel, -1)
        elif k == Qt.Key_Left:
            self.sel = max(0, self.sel - 1)
            self.update()
        elif k == Qt.Key_Right:
            self.sel = min(2, self.sel + 1)
            self.update()
        else:
            super().keyPressEvent(e)

    def _anim_tick(self):
        self.update()
        if not any(r.animating() for r in self.rolls):
            self._anim_timer.stop()

    # ---- painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        focused = self.hasFocus()
        p.setBrush(QColor(255, 255, 255, 14))
        if focused:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, alpha(VIOLET, 230))
            g.setColorAt(1, alpha(PINK, 230))
            p.setPen(QPen(QBrush(g), 1.6))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawRoundedRect(r, 20, 20)

        if self.value() > 0:
            p.setFont(ui_font(10, QFont.Weight.Medium))
            p.setPen(alpha(SUBTEXT, 215))
            p.drawText(QRectF(r.left() + 14, r.top() + 7, 110, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       core.format_duration_long(self.value()))
            p.drawText(QRectF(r.right() - 174, r.top() + 7, 160, 16), Qt.AlignRight | Qt.AlignVCenter,
                       "ends " + clock(time.time() + self.value()))
        zero = self.value() == 0
        for i in range(3):
            sr = self._seg_rect(i)
            active = (i == self.sel and focused) or i == self.hover
            if active:
                p.setPen(Qt.NoPen)
                p.setBrush(alpha(VIOLET, 46 if i == self.sel and focused else 24))
                p.drawRoundedRect(sr, 16, 16)
            dim = (self.vals[i] == 0 and i < 2 and not (i == 1 and self.vals[0] == 0 and False))
            col = QColor(TEXT)
            if self.vals[i] == 0 and i == 0:
                col = alpha(TEXT, 105)
            if zero:
                col = alpha(TEXT, 120)
            self.rolls[i].paint(p, QRectF(sr.left(), sr.top() + 14, sr.width(), 44),
                                ui_font(46, QFont.Weight.Light), col)
            p.setPen(alpha(SUBTEXT, 200))
            p.setFont(ui_font(10, QFont.Weight.Medium))
            p.drawText(QRectF(sr.left(), 82, sr.width(), 16), Qt.AlignCenter, self.NAMES[i])

            if i == self.hover and not self._drag:
                for zone, ytop in ((1, sr.top() + 4), (-1, sr.bottom() - 14)):
                    hot = self.hover_zone == zone
                    path = QPainterPath()
                    cx = sr.center().x()
                    if zone == 1:
                        path.moveTo(cx - 7, ytop + 9); path.lineTo(cx, ytop + 2); path.lineTo(cx + 7, ytop + 9)
                    else:
                        path.moveTo(cx - 7, ytop + 2); path.lineTo(cx, ytop + 9); path.lineTo(cx + 7, ytop + 2)
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(alpha(LAVENDER if hot else TEXT, 255 if hot else 120), 2.2,
                                  Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.drawPath(path)

        # colons (two dots, gently breathing)
        pulse = 0.55 + 0.45 * math.sin(time.monotonic() * 2.2)
        for i in range(2):
            x = self._seg_rect(i).right() + self.COL_W / 2
            p.setPen(Qt.NoPen)
            p.setBrush(alpha(TEXT, 70 + 90 * pulse))
            p.drawEllipse(QPointF(x, 34), 2.6, 2.6)
            p.drawEllipse(QPointF(x, 54), 2.6, 2.6)
        p.end()


# ---------------------------------------------------------------- ring

class RingWidget(QWidget):
    """The star of the card. Timers: a gradient arc that shrinks as time
    passes (glowing head, 60-tick dial, last-10-seconds pulse, finish
    ripples + check mark). Stopwatches: a comet sweeping once a minute with
    the dial lighting up second by second."""

    def __init__(self, size=108):
        super().__init__()
        self.setFixedSize(size, size)
        self.mode = "timer"
        self.elapsed = 0.0
        self.sec_pos = 0.0
        self.sec_frac = 0.0
        self.orbit = 0.0
        self.running = True
        self.finished = False
        self.urgency = 0.0
        self.time = RollingText("00:00")
        self.born = time.monotonic()
        self.finish_t0 = 0.0
        self._tick_key = None
        self._tick_pix = None

    def set_finished(self, f):
        if f and not self.finished:
            self.finish_t0 = time.monotonic()
        self.finished = f

    def _build_ticks(self, c1, c2, dim, intro, now):
        S = self.width()
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(S * dpr), int(S * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        q = QPainter(pix)
        q.setRenderHint(QPainter.Antialiasing)
        c = QPointF(S / 2, S / 2)
        R = S / 2 - 16.0
        for i in range(60):
            ang = math.radians(90 - i * 6)
            long_ = i % 5 == 0
            r1, r2 = R + 9, (R + 15 if long_ else R + 12.5)
            if self.finished:
                lit = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 3.2 - i * 0.22))
            elif self.mode == "timer":
                lo, hi = i / 60.0, (i + 1) / 60.0
                lit = 1.0 if lo >= self.elapsed else (clamp((hi - self.elapsed) * 60.0) if hi > self.elapsed else 0.0)
                lit *= intro
            else:
                lit = 1.0 if i + 1 <= int(self.sec_pos) else (clamp(self.sec_pos - i) if i < self.sec_pos else 0.0)
            col = lerp_color(c1, c2, i / 60.0)
            a = (36 + 219 * lit) * dim if lit > 0 else 34
            q.setPen(QPen(alpha(col, a), 2.2 if long_ else 1.6, Qt.SolidLine, Qt.RoundCap))
            q.drawLine(QPointF(c.x() + r1 * math.cos(ang), c.y() - r1 * math.sin(ang)),
                       QPointF(c.x() + r2 * math.cos(ang), c.y() - r2 * math.sin(ang)))
        q.end()
        return pix

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        S = self.width()
        c = QPointF(S / 2, S / 2)
        R = S / 2 - 16.0
        rect = QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R)
        intro = ease_out((now - self.born) / 0.9)

        if self.finished:
            c1, c2 = TEAL, QColor("#a6f7dc")
        else:
            c1 = lerp_color(VIOLET, AMBER, self.urgency)
            c2 = lerp_color(PINK, RED, self.urgency)
            if not self.running:
                c1, c2 = lerp_color(c1, SUBTEXT, 0.6), lerp_color(c2, SUBTEXT, 0.6)
        dim = 1.0 if (self.running or self.finished) else 0.6
        mid = lerp_color(c1, c2, 0.5)

        # inner disc for depth
        rg = QRadialGradient(c, R)
        rg.setColorAt(0, QColor(255, 255, 255, 14))
        rg.setColorAt(1, QColor(255, 255, 255, 3))
        p.setPen(Qt.NoPen)
        p.setBrush(rg)
        p.drawEllipse(c, R - 4, R - 4)

        # liquid: the disc is a vessel that drains as time passes
        if self.mode == "timer" and not self.finished:
            lvl = clamp((1 - self.elapsed) * intro)
            if lvl > 0.003:
                rr = R - 6
                clip = QPainterPath()
                clip.addEllipse(c, rr, rr)
                p.save()
                p.setClipPath(clip)
                top = c.y() + rr - 2 * rr * lvl
                amp = 0.0 if CALM else 2.6 * clamp(min(lvl, 1 - lvl) * 9)
                ph = now * (1.5 if self.running else 0.45)
                x0 = c.x() - rr
                for layer, (scale_, off, spd) in enumerate(((1.0, 0.0, 1.0), (0.7, 1.9, -0.8))):
                    path = QPainterPath()
                    path.moveTo(x0, c.y() + rr + 2)
                    for k in range(33):
                        x = x0 + 2 * rr * k / 32
                        u = (x - x0) / rr
                        y = top + amp * scale_ * math.sin(u * 3.2 + ph * spd + off) \
                            + amp * 0.4 * math.sin(u * 7.0 + ph * 1.3 * spd)
                        path.lineTo(x, y)
                    path.lineTo(c.x() + rr, c.y() + rr + 2)
                    path.closeSubpath()
                    lg = QLinearGradient(0, top - 4, 0, c.y() + rr)
                    lg.setColorAt(0, alpha(c2, (86 if layer == 0 else 54) * dim))
                    lg.setColorAt(1, alpha(c1, (30 if layer == 0 else 20) * dim))
                    p.fillPath(path, lg)
                p.restore()
        # glassy specular highlight on the vessel
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 38), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(c.x() - R + 8, c.y() - R + 8, 2 * R - 16, 2 * R - 16), 108 * 16, 58 * 16)

        # dial ticks (cached: they only change when a tick boundary is crossed)
        if self.finished:
            bucket = int(now * 14)
        elif self.mode == "timer":
            bucket = int(self.elapsed * 480) + int(intro * 24) * 1000000
        else:
            bucket = int(self.sec_pos * 4)
        key = (self.mode, self.finished, self.running, c1.rgba(), c2.rgba(), bucket)
        if key != self._tick_key:
            self._tick_pix = self._build_ticks(c1, c2, dim, intro, now)
            self._tick_key = key
        p.drawPixmap(0, 0, self._tick_pix)

        # track
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 22), 6))
        p.drawEllipse(rect)

        head = None
        if self.finished:
            t = now - self.finish_t0
            p.setPen(QPen(alpha(TEAL, 60), 12))
            p.drawEllipse(rect)
            p.setPen(QPen(TEAL, 6))
            p.drawEllipse(rect)
            for k in range(2):  # expanding ripples
                ph = ((t - k * 0.9) % 1.8) / 1.8 if t >= k * 0.9 else None
                if ph is not None:
                    rad = R + ph * (S / 2 - R - 1)
                    p.setPen(QPen(alpha(TEAL, 130 * (1 - ph)), 2))
                    p.drawEllipse(c, rad, rad)
            # check mark draws itself
            prog = ease_out(t / 0.5)
            pts = [QPointF(c.x() - 13, c.y() + 1), QPointF(c.x() - 4, c.y() + 10), QPointF(c.x() + 14, c.y() - 10)]
            seg_len = [math.hypot(pts[1].x() - pts[0].x(), pts[1].y() - pts[0].y()),
                       math.hypot(pts[2].x() - pts[1].x(), pts[2].y() - pts[1].y())]
            want = prog * sum(seg_len)
            path = QPainterPath(pts[0])
            if want <= seg_len[0]:
                f = want / seg_len[0]
                path.lineTo(pts[0] + (pts[1] - pts[0]) * f)
            else:
                path.lineTo(pts[1])
                f = (want - seg_len[0]) / seg_len[1]
                path.lineTo(pts[1] + (pts[2] - pts[1]) * clamp(f))
            glow = 0.6 + 0.4 * math.sin(now * 3)
            p.setPen(QPen(alpha(TEAL, 50 * glow), 11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(QColor("#d9fff1"), 4.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            p.end()
            return

        if self.mode == "timer":
            rf = clamp((1 - self.elapsed) * intro)
            if rf > 0.002:
                start_deg = 90 - 360 * (1 - rf)
                p.setPen(QPen(alpha(mid, 46 * dim), 13, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(rect, int(start_deg * 16), int(-360 * rf * 16))
                grad = QConicalGradient(c, 90)
                grad.setColorAt(0, c1)
                grad.setColorAt(0.5, c2)
                grad.setColorAt(1, c1)
                p.setPen(QPen(QBrush(grad), 6, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(rect, int(start_deg * 16), int(-360 * rf * 16))
                head = start_deg
        else:
            deg = 90 - 360 * (self.sec_pos / 60.0)
            n, tail = 18, 130.0
            step = tail / n
            for k in range(n):
                a = (1 - k / n) ** 1.6
                p.setPen(QPen(alpha(lerp_color(c1, c2, k / n), 235 * a * dim), 6 * (1 - 0.55 * k / n),
                              Qt.SolidLine, Qt.RoundCap))
                p.drawArc(rect, int((deg + k * step) * 16), int(step * 1.3 * 16))
            head = deg

        if head is not None:
            ang = math.radians(head)
            hp = QPointF(c.x() + R * math.cos(ang), c.y() - R * math.sin(ang))
            pulse = 0.75 + 0.25 * math.sin(now * 4.0 + self.urgency * 3)
            hg = QRadialGradient(hp, 15)
            hg.setColorAt(0, alpha(QColor("white"), 150 * pulse * dim))
            hg.setColorAt(0.35, alpha(mid, 95 * pulse * dim))
            hg.setColorAt(1, alpha(mid, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(hg)
            p.drawEllipse(hp, 15, 15)
            p.setBrush(alpha(QColor("white"), 255 * dim))
            p.drawEllipse(hp, 3.8, 3.8)

        # seconds orbit: tiny dot circling once a minute, so even a 2-hour
        # timer visibly moves
        if self.mode == "timer" and self.running and not self.finished:
            ro = R - 11
            oa = math.radians(90 - 360 * (self.orbit / 60.0))
            for k in range(9):
                a2 = oa + math.radians(k * 5.5)
                fade = (1 - k / 9.0)
                p.setPen(Qt.NoPen)
                p.setBrush(alpha(QColor("white"), 150 * fade * fade))
                p.drawEllipse(QPointF(c.x() + ro * math.cos(a2), c.y() - ro * math.sin(a2)),
                              2.0 * (0.5 + 0.5 * fade), 2.0 * (0.5 + 0.5 * fade))

        # time text (rolls); bumps each second in the last 10 s; breathes if paused
        txt = self.time.text()
        px = 23 if len(txt) <= 5 else 14
        scale = 1.0
        if self.running and self.urgency > 0:
            scale = 1 + 0.09 * clamp((self.sec_frac - 0.7) / 0.3)
        opacity = 1.0 if self.running else 0.55 + 0.45 * (0.5 + 0.5 * math.sin(now * 2.6))
        col = lerp_color(TEXT, lerp_color(AMBER, RED, 0.5), self.urgency)
        p.save()
        p.translate(c)
        p.scale(scale, scale)
        p.translate(-c.x(), -c.y())
        self.time.paint(p, QRectF(c.x() - R + 2, c.y() - 16, 2 * R - 4, 32),
                        ui_font(px, QFont.Weight.Medium), col, opacity)
        p.restore()
        p.end()


# ---------------------------------------------------------------- timer card

class EditableLabel(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()


class NameEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(e)


class TimerCard(QFrame):
    H = 136

    def __init__(self, item_id, on_change, on_history, on_finished):
        super().__init__()
        self.item_id = item_id
        self.on_change = on_change
        self.on_history = on_history
        self.on_finished = on_finished
        self.item = None
        self._finished_prev = False
        self._refreshed = False
        self._hover = 0.0
        self._bg_key = None
        self._bg_pix = None
        self.spot = None
        self._renaming = False
        self._n = 0
        self.setObjectName("card")
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)       # grows during the entrance animation
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(180)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)

        layout = QHBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(20, 12, 18, 18)
        layout.setSpacing(16)

        self.ring = RingWidget(108)
        layout.addWidget(self.ring)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        mid.addStretch(1)
        self.label_lbl = EditableLabel()
        self.label_lbl.setToolTip("Double-click to rename")
        self.label_lbl.doubleClicked.connect(self._begin_rename)
        self.label_lbl.setStyleSheet(f"color: {TEXT_HEX}; font-size: 16px; font-weight: 700; background: transparent;")
        self.label_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.name_edit = NameEdit()
        self.name_edit.setFixedHeight(28)
        self.name_edit.setStyleSheet(f"""
            QLineEdit {{ background: rgba(255,255,255,22); color: {TEXT_HEX}; border: 1px solid rgba(255,255,255,50);
                         border-radius: 9px; padding: 0 8px; font-size: 15px; font-weight: 700; }}
        """)
        self.name_edit.hide()
        self.name_edit.editingFinished.connect(self._commit_rename)
        self.name_edit.cancelled.connect(self._cancel_rename)
        self.pill = StatusPill()
        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.addWidget(self.pill)
        pill_row.addStretch(1)
        self.info_lbl = QLabel()
        self.info_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.info_lbl.setStyleSheet(f"color: {SUBTEXT_HEX}; font-size: 12px; background: transparent;")
        mid.addWidget(self.label_lbl)
        mid.addWidget(self.name_edit)
        mid.addLayout(pill_row)
        mid.addWidget(self.info_lbl)
        mid.addStretch(1)
        layout.addLayout(mid, stretch=1)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        self.toggle_btn = GhostButton("\u23f8", 32)
        self.toggle_btn.clicked.connect(self._toggle)
        self.restart_btn = GhostButton("\u21bb", 32)
        self.restart_btn.setToolTip("Restart")
        self.restart_btn.clicked.connect(self._restart)
        self.history_btn = GhostButton("\u2630", 32)
        self.history_btn.setToolTip("History")
        self.history_btn.clicked.connect(self._history)
        self.remove_btn = GhostButton("\u2715", 32, danger=True)
        self.remove_btn.setToolTip("Remove")
        self.remove_btn.clicked.connect(self._remove)
        grid.addWidget(self.toggle_btn, 0, 0)
        grid.addWidget(self.restart_btn, 0, 1)
        grid.addWidget(self.history_btn, 1, 0)
        grid.addWidget(self.remove_btn, 1, 1)
        layout.addLayout(grid)

    # ---- painting
    def set_spot(self, pt):
        if self.spot is None or abs(pt.x() - self.spot.x()) + abs(pt.y() - self.spot.y()) > 3:
            self.spot = pt
            self.update()

    def _on_hover(self, v):
        self._hover = v
        self.update()

    def enterEvent(self, e):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        # shadow + gradient body + border are expensive; cache them as a
        # pixmap and only rebuild when size / hover / finished state changes
        finished = self.ring.finished
        key = (self.width(), self.height(), finished, int(self._hover * 16), LAVENDER.rgba(), PINK.rgba())
        if key != self._bg_key:
            dpr = self.devicePixelRatioF()
            pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.transparent)
            q = QPainter(pix)
            self._paint_bg(q, finished)
            q.end()
            self._bg_pix, self._bg_key = pix, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg_pix)
        if self.spot is not None and self._hover > 0.02:   # cursor spotlight
            p.setRenderHint(QPainter.Antialiasing)
            r = QRectF(self.rect()).adjusted(5, 5 - 2.5 * self._hover, -5, -9 - 2.5 * self._hover)
            clip = QPainterPath()
            clip.addRoundedRect(r, 20, 20)
            p.setClipPath(clip)
            rg = QRadialGradient(self.spot, 190)
            rg.setColorAt(0, alpha(QColor("white"), 34 * self._hover))
            rg.setColorAt(0.5, alpha(LAVENDER, 12 * self._hover))
            rg.setColorAt(1, alpha(QColor("white"), 0))
            p.fillRect(r, rg)
        p.end()

    def _paint_bg(self, p, finished):
        p.setRenderHint(QPainter.Antialiasing)
        lift = 2.5 * self._hover
        r = QRectF(self.rect()).adjusted(5, 5 - lift, -5, -9 - lift)
        for i in range(8):  # soft shadow
            sp = QPainterPath()
            sp.addRoundedRect(r.adjusted(-i * 1.6, -i * 1.2 + 5, i * 1.6, i * 1.9 + 5), 20 + i, 20 + i)
            p.fillPath(sp, QColor(0, 0, 0, int((16 + 8 * self._hover) * (1 - i / 8))))
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        if finished:
            g.setColorAt(0, QColor(40, 66, 66, 240))
            g.setColorAt(1, QColor(28, 46, 50, 240))
        else:
            g.setColorAt(0, QColor(52, 54, 80, 246))
            g.setColorAt(1, QColor(36, 38, 58, 246))
        p.fillPath(path, g)
        if finished:
            p.fillPath(path, alpha(TEAL, 10))

        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        if finished:
            bg.setColorAt(0, alpha(TEAL, 170)); bg.setColorAt(1, alpha(TEAL, 60))
        else:
            k = 28 + 40 * self._hover
            bg.setColorAt(0, alpha(LAVENDER, k + 30)); bg.setColorAt(0.5, QColor(255, 255, 255, int(k * 0.5)))
            bg.setColorAt(1, alpha(PINK, k + 10))
        p.setPen(QPen(QBrush(bg), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # top sheen
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawLine(QPointF(r.left() + 18, r.top() + 1), QPointF(r.right() - 18, r.top() + 1))

    # ---- entrance / exit
    def start_entrance(self, delay_ms):
        self._entrance_timer = QTimer(self)
        self._entrance_timer.setSingleShot(True)
        self._entrance_timer.timeout.connect(self.play_entrance)
        self._entrance_timer.start(delay_ms)

    def play_entrance(self):
        self.ring.born = time.monotonic()
        fx = QGraphicsOpacityEffect(self)
        fx.setOpacity(0.0)
        self.setGraphicsEffect(fx)
        h = QPropertyAnimation(self, b"maximumHeight")
        h.setDuration(380)
        h.setStartValue(0)
        h.setEndValue(self.H)
        h.setEasingCurve(QEasingCurve.OutBack)
        o = QVariantAnimation(self)
        o.setDuration(420)
        o.setStartValue(0.0)
        o.setEndValue(1.0)
        o.valueChanged.connect(lambda v: fx.setOpacity(clamp(v)))
        group = QParallelAnimationGroup(self)
        group.addAnimation(h)
        group.addAnimation(o)
        group.finished.connect(self._entrance_done)
        self._entrance_group = group
        group.start()

    def _entrance_done(self):
        self.setGraphicsEffect(None)
        self.setMaximumHeight(self.H)

    def play_exit(self, on_done):
        fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(fx)
        o = QVariantAnimation(self)
        o.setDuration(220)
        o.setStartValue(1.0)
        o.setEndValue(0.0)
        o.valueChanged.connect(lambda v: fx.setOpacity(clamp(v)))
        h = QPropertyAnimation(self, b"maximumHeight")
        h.setDuration(260)
        h.setStartValue(max(1, self.height()))
        h.setEndValue(0)
        h.setEasingCurve(QEasingCurve.InCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(h)
        group.addAnimation(o)
        group.finished.connect(on_done)
        self._exit_group = group
        group.start()

    # ---- inline rename (double-click the name)
    def _begin_rename(self):
        if not self.item:
            return
        self._renaming = True
        self.name_edit.setText(self.item.get("label") or "")
        self.label_lbl.hide()
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _commit_rename(self):
        if not self._renaming:
            return
        self._renaming = False
        text = self.name_edit.text().strip()
        self.name_edit.hide()
        self.label_lbl.show()
        core.rename_item(core.load_state(), self.item_id, text)
        self.on_change()

    def _cancel_rename(self):
        self._renaming = False
        self.name_edit.hide()
        self.label_lbl.show()

    # ---- actions
    def _toggle(self):
        state = core.load_state()
        item = core.find(state, self.item_id)
        if not item:
            return
        if item.get("running"):
            core.pause_item(state, self.item_id)
        else:
            core.resume_item(state, self.item_id)
        self.on_change()

    def _restart(self):
        core.restart_item(core.load_state(), self.item_id)
        self.ring.born = time.monotonic()
        self.on_change()

    def _history(self):
        if self.on_history:
            self.on_history(self.item_id)

    def _remove(self):
        core.remove_item(core.load_state(), self.item_id)
        self.on_change(removed_id=self.item_id)

    # ---- data
    def _info_text(self, item, finished, now):
        if item["mode"] == "timer":
            dur = core.format_duration_long(item["target_seconds"])
            if finished:
                ts = next((e.get("ts") for e in reversed(item.get("history", []))
                           if e.get("type") == "finished" and e.get("ts")), None)
                return f"Finished {clock(ts, now)} \u00b7 {dur}"
            if item.get("running"):
                return f"Ends {clock(now + core.remaining_seconds(item, now), now)} \u00b7 {dur}"
            return f"{core.format_hms(core.remaining_seconds(item, now))} left \u00b7 {dur}"
        started = item.get("created_at")
        return f"Started {clock(started, now)}"

    def refresh(self, item):
        self.item = item
        now = time.time()
        label = item.get("label") or ("Timer" if item["mode"] == "timer" else "Stopwatch")
        self.label_lbl.setText(label)
        finished = item["mode"] == "timer" and core.is_finished(item)
        self.pill.set_status("finished" if finished else ("running" if item.get("running") else "paused"))
        self.info_lbl.setText(self._info_text(item, finished, now))
        self.restart_btn.setVisible(item["mode"] == "timer")
        self.toggle_btn.setText("\u23f8" if item.get("running") else "\u25b6")
        self.toggle_btn.setVisible(not finished)
        if finished and not self._finished_prev and self._refreshed:
            self.on_finished(self)
        self._finished_prev = finished
        self._refreshed = True
        self.tick(now, force=True)

    def tick(self, now, force=False):
        item = self.item
        if not item:
            return
        ring = self.ring
        if item["mode"] == "timer":
            rem = max(0.0, core.remaining_seconds(item, now))
            total = float(item.get("target_seconds") or 1.0)
            ring.mode = "timer"
            ring.elapsed = clamp(1 - rem / total)
            ring.running = bool(item.get("running")) and rem > 0
            ring.set_finished(rem <= 0)
            ring.urgency = clamp((10.0 - rem) / 10.0) if (ring.running and rem <= 10 and total >= 30) else 0.0
            ring.sec_frac = rem % 1.0
            ring.orbit = core.elapsed_seconds(item, now) % 60.0
            ring.time.set_text(core.format_hms(int(math.ceil(rem - 1e-9))))
        else:
            el = core.elapsed_seconds(item, now)
            ring.mode = "stopwatch"
            ring.running = bool(item.get("running"))
            ring.finished = False
            ring.sec_pos = el % 60.0
            ring.urgency = 0.0
            ring.time.set_text(core.format_hms(int(el)))
        self._repaint_ring(now, force=force)

    def _repaint_ring(self, now, force=False):
        """Repaint at full frame rate only while something visibly moves;
        otherwise a slow rate keeps CPU use low."""
        ring = self.ring
        self._n += 1
        mono = time.monotonic()
        busy = (ring.urgency > 0 or ring.time.animating()
                or mono - ring.born < 1.0 or mono - ring.finish_t0 < 0.6)
        if force or busy:
            period = 1
        elif ring.finished:
            period = 2
        elif ring.running:
            period = 6 if CALM else 2
        else:
            period = 6 if CALM else 4
        if self._n % period == 0:
            ring.update()


# ---------------------------------------------------------------- empty state

class EmptyState(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(190)

    def tick(self):
        if self.isVisible():
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        c = QPointF(self.width() / 2, 70 + 5 * math.sin(now * 1.3))
        R = 34
        p.setBrush(Qt.NoBrush)
        pen = QPen(QColor(255, 255, 255, 30), 5)
        p.setPen(pen)
        p.drawEllipse(c, R, R)
        g = QConicalGradient(c, -now * 60)
        g.setColorAt(0, alpha(VIOLET, 230)); g.setColorAt(0.35, alpha(PINK, 0)); g.setColorAt(1, alpha(VIOLET, 230))
        p.setPen(QPen(QBrush(g), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), int(-now * 60 * 16), 110 * 16)
        p.setPen(alpha(TEXT, 210))
        p.setFont(ui_font(30, QFont.Weight.Light))
        p.drawText(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), Qt.AlignCenter, "\u221e")
        p.setPen(alpha(TEXT, 230))
        p.setFont(ui_font(15, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, 124, self.width(), 24), Qt.AlignCenter, "Nothing running yet")
        p.setPen(alpha(SUBTEXT, 255))
        p.setFont(ui_font(12))
        p.drawText(QRectF(0, 148, self.width(), 20), Qt.AlignCenter,
                   "Set a time above and press Start. Timers survive shutdowns.")
        p.end()


# ---------------------------------------------------------------- background + confetti

class GlassPanel(QWidget):
    """Rounded gradient window background with drifting aurora blobs and
    slow floating particles."""

    def __init__(self):
        super().__init__()
        self.t = 0.0
        self.mood = None            # QColor or None
        self.mood_amt = 0.0
        self.mood_color = QColor(TEAL)
        rnd = random.Random(7)
        self.particles = [[rnd.random(), rnd.random(), rnd.uniform(0.8, 2.4),
                           rnd.uniform(0.004, 0.012), rnd.uniform(0, 6.28)] for _ in range(26)]

    def advance(self, dt):
        target = 1.0 if self.mood is not None else 0.0
        if self.mood is not None:
            self.mood_color = lerp_color(self.mood_color, self.mood, clamp(dt * 3))
        self.mood_amt += (target - self.mood_amt) * clamp(dt * 2.2)
        if CALM:
            return
        self.t += dt * 0.7
        for pt in self.particles:
            pt[1] -= pt[3] * dt * 6
            if pt[1] < -0.03:
                pt[1] = 1.03
                pt[0] = random.random()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 24, 24)
        p.setClipPath(path)
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0, BG_TOP)
        grad.setColorAt(1, BG_BOTTOM)
        p.fillPath(path, grad)

        w, h, t = rect.width(), rect.height(), self.t
        blobs = (
            (w * 0.22 + math.sin(t) * w * 0.12, h * 0.14 + math.cos(t * 0.8) * h * 0.04, alpha(VIOLET, 84), w * 0.55),
            (w * 0.85 + math.cos(t * 0.7) * w * 0.10, h * 0.55 + math.sin(t * 0.6) * h * 0.08, alpha(PINK, 58), w * 0.5),
            (w * 0.30 + math.sin(t * 0.5 + 2) * w * 0.14, h * 0.95 + math.cos(t * 0.9) * h * 0.04,
         lerp_color(alpha(SKY, 44), alpha(self.mood_color, 120), self.mood_amt), w * (0.55 + 0.25 * self.mood_amt)),
        )
        p.setPen(Qt.NoPen)
        for cx, cy, color, r in blobs:
            rg = QRadialGradient(QPointF(cx, cy), r)
            rg.setColorAt(0, color)
            rg.setColorAt(1, alpha(color, 0))
            p.setBrush(rg)
            p.drawEllipse(QPointF(cx, cy), r, r)

        for x, y, r, _s, ph in self.particles:
            a = 38 + 38 * math.sin(t * 1.6 + ph)
            p.setBrush(QColor(255, 255, 255, int(clamp(a, 0, 255))))
            p.drawEllipse(QPointF(x * w, y * h), r, r)

        sheen = QLinearGradient(0, 0, 0, 130)
        sheen.setColorAt(0, QColor(255, 255, 255, 16))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(0, 0, w, 130), sheen)

        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 24, 24)
        if self.mood_amt > 0.02:
            p.setPen(QPen(alpha(self.mood_color, 110 * self.mood_amt), 1.6))
            p.drawRoundedRect(rect.adjusted(0.8, 0.8, -0.8, -0.8), 24, 24)
        p.end()


class ConfettiOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pieces = []
        self.hide()

    def burst(self, origin, count=70):
        for _ in range(count):
            ang = random.uniform(-math.pi * 0.95, -math.pi * 0.05)
            sp = random.uniform(220, 620)
            self.pieces.append({
                "x": origin.x(), "y": origin.y(),
                "vx": math.cos(ang) * sp, "vy": math.sin(ang) * sp,
                "rot": random.uniform(0, 360), "vr": random.uniform(-520, 520),
                "w": random.uniform(5, 10), "h": random.uniform(3, 6),
                "color": random.choice([VIOLET, LAVENDER, PINK, TEAL, AMBER, SKY, QColor("#ffffff")]), "age": 0.0, "life": random.uniform(1.3, 2.2),
                "round": random.random() < 0.3,
            })
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()

    def tick(self, dt):
        if not self.pieces:
            return
        for pc in self.pieces:
            pc["vy"] += 980 * dt
            pc["vx"] *= 0.992
            pc["x"] += pc["vx"] * dt
            pc["y"] += pc["vy"] * dt
            pc["rot"] += pc["vr"] * dt
            pc["age"] += dt
        self.pieces = [pc for pc in self.pieces if pc["age"] < pc["life"] and pc["y"] < self.height() + 30]
        if self.pieces:
            self.update()
        else:
            self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for pc in self.pieces:
            a = 255 * (1 - clamp(pc["age"] / pc["life"]) ** 3)
            p.save()
            p.translate(pc["x"], pc["y"])
            p.rotate(pc["rot"])
            p.setBrush(alpha(pc["color"], a))
            if pc["round"]:
                p.drawEllipse(QPointF(0, 0), pc["h"], pc["h"])
            else:
                p.drawRoundedRect(QRectF(-pc["w"] / 2, -pc["h"] / 2, pc["w"], pc["h"]), 1.5, 1.5)
            p.restore()
        p.end()


# ---------------------------------------------------------------- sound

class SoundPlayer:
    """Plays the finish sound in a separate process (see core.spawn_player),
    so a missing/broken audio setup can never crash the timer window."""

    def play(self, path):
        import threading
        threading.Thread(target=core.spawn_player, args=(path,), daemon=True).start()


# ---------------------------------------------------------------- sync (desktop <-> phone)

class SyncManager(QObject):
    """Owns the live relay connection. Network work happens on background
    threads; results come back to the GUI thread as signals."""

    payload = Signal(object)
    status = Signal(str)
    pushed = Signal(str, str, int)      # kind, id, rev
    publish_failed = Signal()           # relay rejected/unreachable: retry later with backoff

    def __init__(self):
        super().__init__()
        self.sub = None
        self.state = "off"             # off | connecting | online | offline | nocrypto

    def available(self):
        return sync is not None and sync.HAVE_CRYPTO

    def active(self):
        return self.sub is not None

    def start(self):
        self.stop()
        if sync is None or not sync.HAVE_CRYPTO:
            self._set("nocrypto")
            return
        try:
            cfg = sync.load_config()
        except Exception:
            cfg = None
        if cfg is None:
            self._set("off")
            return
        self.sub = sync.Subscriber(cfg, lambda p: self.payload.emit(p), self._set)
        self.sub.start()

    def stop(self):
        if self.sub is not None:
            self.sub.stop()
            self.sub = None
        self._set("off")

    def _set(self, s):
        self.state = s
        self.status.emit(s)

    def publish_async(self, items):
        sub = self.sub
        if sub is None:
            return

        def work():
            for kind, iid, rev, payload in items:
                try:
                    sub.publish(payload)
                except Exception:
                    self.publish_failed.emit()       # e.g. rate-limited: back off, don't give up
                    return
                self.pushed.emit(kind, iid, rev)
        threading.Thread(target=work, daemon=True).start()

    def hello_async(self):
        sub = self.sub
        if sub is None:
            return

        def work():
            try:
                sub.publish({"v": 1, "t": "hello", "dev": core.device_id()})
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()


class SyncButton(GhostButton):
    """Cloud button with a status dot: green online, amber connecting,
    red offline, grey off."""
    DOT = {"online": TEAL, "connecting": AMBER, "offline": RED}

    def __init__(self, diameter=28):
        super().__init__("\u2601", diameter)
        self.state = "off"

    def set_state(self, state):
        self.state = state
        tips = {"online": "Sync: connected", "connecting": "Sync: connecting\u2026",
                "offline": "Sync: offline \u2014 will retry", "off": "Sync: not set up (click to pair a device)",
                "nocrypto": "Sync needs python-cryptography"}
        self.setToolTip(tips.get(state, "Sync"))
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = self.DOT.get(self.state, SUBTEXT)
        p.setPen(QPen(QColor(BG_TOP), 1.5))
        p.setBrush(col)
        p.drawEllipse(QPointF(self.width() - 7, self.height() - 7), 3.6, 3.6)
        p.end()


class SyncDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.setWindowTitle("Sync with your phone")
        self.resize(460, 470)
        self.setStyleSheet(f"""
            QDialog {{ background: {BG_TOP.name()}; }}
            QLabel {{ color: {TEXT_HEX}; }}
            QLineEdit {{ background: rgba(255,255,255,14); color: {TEXT_HEX}; border: 1px solid rgba(255,255,255,30);
                         border-radius: 10px; padding: 8px 12px; font-size: 13px; }}
            QPushButton {{ background: rgba(255,255,255,22); color: {TEXT_HEX}; border: none; border-radius: 10px;
                           padding: 9px 14px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,38); }}
            QPushButton#primary {{ background: {ACCENT_HEX}; }}
            QPushButton#primary:hover {{ background: {LAVENDER.name()}; }}
        """)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 20)
        self.lay.setSpacing(10)
        self.rebuild()

    def _clear(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _label(self, text, size=13, color=TEXT_HEX, bold=False, wrap=True):
        l = QLabel(text)
        l.setWordWrap(wrap)
        l.setStyleSheet(f"font-size: {size}px; color: {color}; {'font-weight: 700;' if bold else ''}")
        self.lay.addWidget(l)
        return l

    def rebuild(self):
        self._clear()
        self._label("Sync with your phone", 18, bold=True)
        if not self.win.sync.available():
            self._label("Sync needs the python-cryptography package.\n\n    sudo pacman -S python-cryptography",
                        13, SUBTEXT_HEX)
            self.lay.addStretch(1)
            return
        cfg = sync.load_config()
        if cfg is None:
            self._label("Your timers live on the phone too, so they keep counting and ring there even if this PC "
                        "loses power. Everything is end-to-end encrypted; the relay only sees scrambled text.",
                        13, SUBTEXT_HEX)
            self.lay.addSpacing(6)
            self._label("1.  On this PC", 14, bold=True)
            b = QPushButton("Create a pairing code")
            b.setObjectName("primary")
            b.clicked.connect(self._create)
            self.lay.addWidget(b)
            self.lay.addSpacing(6)
            self._label("2.  Already have a code from your phone?", 14, bold=True)
            self.code_edit = QLineEdit()
            self.code_edit.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
            self.lay.addWidget(self.code_edit)
            j = QPushButton("Join with this code")
            j.clicked.connect(self._join)
            self.lay.addWidget(j)
            self.err = self._label("", 12, RED.name())
            self.lay.addSpacing(4)
            self._label("Relay server", 12, SUBTEXT_HEX)
            self.server_edit = QLineEdit(sync.DEFAULT_SERVER)
            self.lay.addWidget(self.server_edit)
            self.lay.addStretch(1)
        else:
            state = self.win.sync.state
            names = {"online": "Connected", "connecting": "Connecting\u2026", "offline": "Offline \u2014 retrying",
                     "off": "Off"}
            self._label(f"Status:  {names.get(state, state)}", 14, bold=True)
            self._label("On your phone, tap the cloud button (top right), paste this code and tap Join. "
                        "Keep it private \u2014 anyone with it can read and change your timers.", 13, SUBTEXT_HEX)
            self.lay.addSpacing(4)
            code = QLabel(cfg["secret"].replace("-", "-\u200b"))
            code.setWordWrap(True)
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            code.setAlignment(Qt.AlignCenter)
            code.setStyleSheet(f"font-family: monospace; font-size: 17px; font-weight: 700; letter-spacing: 1px; "
                               f"color: {LAVENDER.name()}; background: rgba(255,255,255,12); "
                               f"border: 1px solid rgba(255,255,255,26); border-radius: 12px; padding: 16px;")
            self.lay.addWidget(code)
            cp = QPushButton("Copy code")
            cp.setObjectName("primary")
            cp.clicked.connect(lambda: QApplication.clipboard().setText(cfg["secret"]))
            self.lay.addWidget(cp)
            self._label(f"Relay: {cfg['server']}    \u00b7    this device: {core.device_id()}", 11, SUBTEXT_HEX)
            self.lay.addStretch(1)
            off = QPushButton("Turn sync off")
            off.clicked.connect(self._off)
            self.lay.addWidget(off)

    def _create(self):
        try:
            self.win.sync_configure(sync.new_secret(), self.server_edit.text().strip() or sync.DEFAULT_SERVER)
        except Exception as ex:
            self.err.setText(str(ex))
            return
        self.rebuild()

    def _join(self):
        try:
            sync.parse_secret(self.code_edit.text())
        except ValueError as ex:
            self.err.setText(str(ex))
            return
        self.win.sync_configure(self.code_edit.text().strip().upper(),
                                self.server_edit.text().strip() or sync.DEFAULT_SERVER, hello=True)
        self.rebuild()

    def _off(self):
        self.win.sync_off()
        self.rebuild()


# ---------------------------------------------------------------- stopwatch warning

STOPWATCH_JOKES = [
    ("This stopwatch has no finish line",
     "Once started, it will keep counting until you press stop \u2014 or until the universe collapses in on "
     "itself, whichever comes first. Cosmologists say that's billions of years away. Your attention span? "
     "Slightly less."),
    ("Are you absolutely sure?",
     "A stopwatch never gives up. Left alone, it will cheerfully count past your lunch, your weekend, your next "
     "birthday and the heat death of the universe. Only you can stop it."),
    ("Commitment issues? Not this stopwatch.",
     "It will run until you stop it, or until the last star burns out. We're assuming you'll stop it before "
     "then. No promises about the stars."),
    ("Time to make a bold promise",
     "This stopwatch will tick on forever unless you press stop. It has no off switch of its own and "
     "absolutely no sense of proportion."),
    ("Warning: eternity ahead",
     "Start this and it will count until the end of time. Your PC will need a reboot long before that, "
     "though, so maybe don't wait."),
]


class StopwatchWarning(QDialog):
    """A deliberately dramatic heads-up: a stopwatch has no finish line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Heads up")
        self.setModal(True)
        self.resize(450, 330)
        self.dont_ask = False
        self.setStyleSheet(f"""
            QDialog {{ background: {BG_TOP.name()}; }}
            QLabel {{ color: {TEXT_HEX}; }}
            QCheckBox {{ color: {SUBTEXT_HEX}; font-size: 12px; spacing: 8px; }}
            QPushButton {{ background: rgba(255,255,255,22); color: {TEXT_HEX}; border: none; border-radius: 12px;
                           padding: 11px 16px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,38); }}
            QPushButton#primary {{ background: {ACCENT_HEX}; }}
            QPushButton#primary:hover {{ background: {LAVENDER.name()}; }}
        """)
        headline, joke = random.choice(STOPWATCH_JOKES)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        icon = QLabel("\u221e")
        icon.setStyleSheet(f"font-size: 44px; color: {LAVENDER.name()};")
        lay.addWidget(icon)
        h = QLabel(headline)
        h.setWordWrap(True)
        h.setStyleSheet("font-size: 19px; font-weight: 700;")
        lay.addWidget(h)
        j = QLabel(joke)
        j.setWordWrap(True)
        j.setStyleSheet("font-size: 14px;")
        lay.addWidget(j)
        fine = QLabel("(Technically nothing ticks while the app is closed. It just does the math when you come "
                      "back, and shows you exactly how long it has been \u201crunning\u201d.)")
        fine.setWordWrap(True)
        fine.setStyleSheet(f"font-size: 12px; color: {SUBTEXT_HEX};")
        lay.addWidget(fine)
        lay.addStretch(1)
        self.cb = QCheckBox("Don't warn me again")
        lay.addWidget(self.cb)
        row = QHBoxLayout()
        no = QPushButton("Never mind, I'll behave")
        no.clicked.connect(self.reject)
        yes = QPushButton("Start it anyway")
        yes.setObjectName("primary")
        yes.setDefault(True)
        yes.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)

    def accept(self):
        self.dont_ask = self.cb.isChecked()
        super().accept()


# ---------------------------------------------------------------- history dialog

KIND_COLORS = {
    "started": "#a78bfa", "restarted": "#a78bfa", "resumed": "#58e0b0",
    "finished": "#58e0b0", "paused": "#f0c674", "downtime": "#e06c75",
}


class HistoryDialog(QDialog):
    """Per-item log: start, every pause (when + how long), offline gaps
    (power cut / shutdown / app closed / sleep) and the finish time.
    Newest entry first; refreshes every second so an ongoing pause ticks."""

    def __init__(self, item_id, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self._last_html = None
        self.setWindowTitle("History")
        self.resize(450, 560)
        self.setStyleSheet(f"QDialog {{ background: {BG_TOP.name()}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        self.title_lbl = QLabel()
        self.title_lbl.setStyleSheet(f"color: {TEXT_HEX}; font-size: 16px; font-weight: 700;")
        lay.addWidget(self.title_lbl)

        self.summary_lbl = QLabel()
        self.summary_lbl.setWordWrap(True)
        self.summary_lbl.setStyleSheet(f"color: {SUBTEXT_HEX}; font-size: 12px;")
        lay.addWidget(self.summary_lbl)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setStyleSheet(f"""
            QTextBrowser {{
                background: rgba(255,255,255,10); color: {TEXT_HEX};
                border: 1px solid rgba(255,255,255,24); border-radius: 12px; padding: 6px;
            }}
        """)
        lay.addWidget(self.view, stretch=1)

        close = QPushButton("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedHeight(36)
        close.clicked.connect(self.close)
        close.setStyleSheet(f"""
            QPushButton {{ background: rgba(255,255,255,20); color: {TEXT_HEX};
                          border: none; border-radius: 10px; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(255,255,255,36); }}
        """)
        lay.addWidget(close)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def refresh(self):
        item = core.find(core.load_state(), self.item_id)
        if item is None:
            self.close()
            return
        now = time.time()
        rows, totals = core.history_rows(item, now)
        label = item.get("label") or ("Timer" if item["mode"] == "timer" else "Stopwatch")
        self.title_lbl.setText(f"{label} \u2014 history")
        self.summary_lbl.setText(
            f"{totals['pauses']} pause{'s' if totals['pauses'] != 1 else ''}"
            f" ({core.format_duration_long(totals['paused_seconds'])} total)   \u00b7   "
            f"{totals['downtimes']} offline gap{'s' if totals['downtimes'] != 1 else ''}"
            f" ({core.format_duration_long(totals['downtime_seconds'])} total)"
        )

        parts = ['<table width="100%" cellspacing="0" cellpadding="7">']
        for r in reversed(rows):
            c = KIND_COLORS.get(r["kind"], SUBTEXT_HEX)
            dur = ""
            if r["duration"] is not None:
                dur = core.format_duration_long(r["duration"])
                if r["ongoing"]:
                    dur += " \u2026"
            detail = (f'<br><span style="color:{SUBTEXT_HEX}; font-size:11px;">'
                      f'{html.escape(r["detail"])}</span>') if r["detail"] else ""
            parts.append(
                f'<tr><td width="16" valign="top" style="color:{c}; font-size:15px;">&#9679;</td>'
                f'<td valign="top"><span style="color:{TEXT_HEX}; font-weight:600;">{html.escape(r["title"])}</span>'
                f'<br><span style="color:{SUBTEXT_HEX}; font-size:12px;">{html.escape(core.row_when_text(r, now))}</span>'
                f'{detail}</td>'
                f'<td align="right" valign="top"><span style="color:{c}; font-weight:600;">{dur}</span></td></tr>'
            )
        parts.append("</table>")
        new_html = "".join(parts)
        if new_html != self._last_html:
            sb = self.view.verticalScrollBar()
            pos = sb.value()
            self.view.setHtml(new_html)
            sb.setValue(pos)
            self._last_html = new_html


# ---------------------------------------------------------------- title bar

class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self._window = window
        self._drag_pos = None
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 14, 0)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setPixmap(make_app_icon().pixmap(28, 28))
        logo.setStyleSheet("background: transparent;")
        title = QLabel("Eternal Timer")
        title.setStyleSheet(f"color: {TEXT_HEX}; font-size: 16px; font-weight: 800; letter-spacing: 0.4px; background: transparent;")
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addStretch(1)

        self.sync_btn = SyncButton(28)
        self.sync_btn.clicked.connect(lambda: window._open_sync())
        self.sync_btn.set_state("off")
        self.theme_btn = GhostButton("\u25d0", 28)
        self.theme_btn.clicked.connect(lambda: window._next_theme())
        self.set_theme_name(window._theme)
        self.sound_btn = SoundButton(28)
        self.sound_btn.clicked.connect(lambda: window._toggle_sound())
        self.set_sound_state(core.sound_enabled())
        layout.addWidget(self.sync_btn)
        layout.addWidget(self.theme_btn)
        layout.addWidget(self.sound_btn)
        self.min_btn = GhostButton("\u2013", 28)
        self.min_btn.clicked.connect(window.hide_to_tray)
        self.close_btn = GhostButton("\u2715", 28, danger=True)
        self.close_btn.clicked.connect(window.hide_to_tray)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.close_btn)

    def set_theme_name(self, name):
        label = THEMES.get(name, THEMES["aurora"])[0]
        self.theme_btn.setToolTip(f"Theme: {label} (click to change)")

    def set_sound_state(self, on):
        self.sound_btn.set_muted(not on)
        self.sound_btn.setToolTip("Finish sound: ON (click to mute)" if on else "Finish sound: OFF (click to turn on)")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            # startSystemMove works on Wayland and X11; manual move only as fallback
            if handle is not None and handle.startSystemMove():
                return
            self._drag_pos = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------- main window

PRESETS = [("1m", 60), ("5m", 300), ("10m", 600), ("15m", 900), ("25m", 1500), ("45m", 2700), ("1h", 3600)]
TIMER_BOX_H = 108 + 10 + 34


class EternalTimerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        global CALM
        _s = core.load_settings()
        self._theme = _s.get("theme") if _s.get("theme") in THEMES else "aurora"
        apply_colors(theme_colors(self._theme))
        CALM = bool(_s.get("calm", False))
        self.setFixedSize(520, 760)
        self.cards = {}
        self.sound = SoundPlayer()
        self._history_dialogs = {}
        self._init_idx = 0
        self._fcount = 0
        self._last_frame = time.monotonic()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.glass = GlassPanel()
        root.addWidget(self.glass)

        gl = QVBoxLayout(self.glass)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(0)
        self.title_bar = TitleBar(self)
        gl.addWidget(self.title_bar)

        body = QVBoxLayout()
        body.setContentsMargins(24, 4, 24, 22)
        body.setSpacing(14)
        gl.addLayout(body)

        self.segment = SegmentedControl(["Timer", "Stopwatch"], self._on_mode_change)
        body.addWidget(self.segment)

        # --- timer-only box: picker + presets (collapses for stopwatch)
        self.timer_box = QWidget()
        self.timer_box.setMaximumHeight(TIMER_BOX_H)
        self.timer_box.setMinimumHeight(0)
        tb = QVBoxLayout(self.timer_box)
        tb.setSizeConstraint(QLayout.SetNoConstraint)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(10)
        self.picker = TimePicker()
        self.picker.submitted.connect(self._add_item)
        self.picker.changed.connect(self._on_picker_changed)
        tb.addWidget(self.picker)
        self.chip_row = QWidget()
        chips = QHBoxLayout(self.chip_row)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(6)
        self.chips = []
        for text, secs in PRESETS:
            chip = GhostButton(text, 34, width=0, font_px=12)
            chip.seconds = secs
            chip.clicked.connect(lambda _=False, s=secs: self.picker.set_seconds(s))
            chips.addWidget(chip)
            self.chips.append(chip)
        tb.addWidget(self.chip_row)
        body.addWidget(self.timer_box)

        # --- label + start
        row = QHBoxLayout()
        row.setSpacing(10)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("What's this for?  (optional)")
        self.label_edit.setFixedHeight(46)
        self._style_label_edit()
        self.label_edit.returnPressed.connect(self._add_item)
        self.add_btn = AddButton()
        self.add_btn.clicked.connect(self._add_item)
        row.addWidget(self.label_edit, stretch=1)
        row.addWidget(self.add_btn)
        body.addLayout(row)

        # --- list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
            QScrollBar::handle:vertical {{ background: rgba(255,255,255,40); border-radius: 4px; min-height: 30px; }}
            QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,80); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setSpacing(0)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.empty = EmptyState()
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)
        body.addWidget(self.scroll, stretch=1)

        self.confetti = ConfettiOverlay(self)

        self._build_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(400)

        self.picker.set_seconds(25 * 60, animate=False)
        self._on_picker_changed(self.picker.value())

        # Work out (and log) how long the app/PC was down since last run.
        # Must happen before the first refresh() so a timer that finished
        # while we were off still gets its offline gap recorded.
        self.startup_gaps = core.record_startup_downtime(core.load_state())
        core.write_heartbeat()
        self._last_hb = time.time()
        self.hb_timer = QTimer(self)
        self.hb_timer.timeout.connect(self._heartbeat)
        self.hb_timer.start(5000)

        self.refresh(initial=True)
        self._init_sync()

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._frame)
        self.frame_timer.start(self._fps_interval())
        self._play_intro()

    # ---- per-frame animation clock
    def _frame(self):
        now_m = time.monotonic()
        dt = min(0.1, now_m - self._last_frame)
        self._last_frame = now_m
        self._fcount += 1
        self.glass.advance(dt)
        if self._fcount % 3 == 0:
            self.glass.update()
        wall = time.time()
        for card in list(self.cards.values()):
            card.tick(wall)
        self.empty.tick()
        for card in self.cards.values():
            if card._hover > 0.02:
                card.set_spot(QPointF(card.mapFromGlobal(QCursor.pos())))
        if self._fcount % 8 == 0:
            self._update_mood()
        if self._fcount % 90 == 0:
            self.picker.update()
        if self.add_btn.isVisible() and self._fcount % 2 == 0:
            self.add_btn.update()
        if self.picker.hasFocus():
            self.picker.update()
        self.confetti.tick(dt)

    def _fps_interval(self):
        if CALM:
            return 100
        return 33 if self.isActiveWindow() else 100

    def _style_label_edit(self):
        self.label_edit.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255,255,255,14); color: {TEXT_HEX};
                border: 1px solid rgba(255,255,255,26); border-radius: 15px;
                padding: 0 16px; font-size: 14px;
            }}
            QLineEdit:focus {{ border: 1.5px solid {ACCENT_HEX}; background: rgba(255,255,255,20); }}
        """)

    # ---- themes
    def _next_theme(self):
        i = THEME_ORDER.index(self._theme) if self._theme in THEME_ORDER else 0
        self._set_theme(THEME_ORDER[(i + 1) % len(THEME_ORDER)])

    def _set_theme(self, name, animate=True):
        if name not in THEMES:
            return
        self._theme = name
        s = core.load_settings()
        s["theme"] = name
        core.save_settings(s)
        self.title_bar.set_theme_name(name)
        new = theme_colors(name)
        if not animate or CALM:
            apply_colors(new)
            self._retheme()
            return
        old = [QColor(VIOLET), QColor(PINK), QColor(LAVENDER), QColor(SKY)]
        anim = QVariantAnimation(self)
        anim.setDuration(520)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def step(t):
            apply_colors([lerp_color(o, n, t) for o, n in zip(old, new)])
            self._retheme()
        anim.valueChanged.connect(step)
        self._theme_anim = anim
        anim.start()

    def _retheme(self):
        self._style_label_edit()
        self._on_picker_changed(self.picker.value())
        self.title_bar.sound_btn.retheme()
        for card in self.cards.values():
            card.pill.retheme()
            card.ring._tick_key = None
            card.update()
            card.ring.update()
        for w in (self.glass, self.picker, self.add_btn, self.segment, self.empty):
            w.update()

    def _set_warn(self, on):
        s = core.load_settings()
        s["warn_stopwatch"] = bool(on)
        core.save_settings(s)
        self.tray_warn_action.setChecked(bool(on))

    # ---- calm mode (reduced motion)
    def _set_calm(self, on):
        global CALM
        CALM = bool(on)
        s = core.load_settings()
        s["calm"] = CALM
        core.save_settings(s)
        self.tray_calm_action.setChecked(CALM)
        if self.frame_timer.isActive():
            self.frame_timer.setInterval(self._fps_interval())

    # ---- mood: the background glows amber when a timer is about to end,
    # teal for a moment after one finishes
    def _update_mood(self):
        mono = time.monotonic()
        done = any(c.ring.finished and mono - c.ring.finish_t0 < 30 for c in self.cards.values())
        urgent = any(c.ring.urgency > 0.05 for c in self.cards.values())
        self.glass.mood = TEAL if done else (AMBER if urgent else None)

    # ---- staggered intro
    def _play_intro(self):
        if CALM:
            return
        for i, wd in enumerate([self.segment, self.picker, self.chip_row, self.label_edit, self.add_btn]):
            self._fade_in(wd, 60 + i * 85)

    def _fade_in(self, widget, delay):
        fx = QGraphicsOpacityEffect(widget)
        fx.setOpacity(0.0)
        widget.setGraphicsEffect(fx)
        anim = QVariantAnimation(widget)
        anim.setDuration(460)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(lambda v: fx.setOpacity(clamp(v)))
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        QTimer.singleShot(delay, anim.start)

    # ---- sync
    def _init_sync(self):
        self._push_all = False
        self._sync_hello = True
        self._inflight = set()
        self._retry_base = 10          # seconds; doubles up to 120 after each failed push
        self._retry_delay = 0
        self._retry_until = 0.0
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_push)
        self.sync = SyncManager()
        self.sync.payload.connect(self._on_sync_payload)
        self.sync.status.connect(self._on_sync_status)
        self.sync.pushed.connect(self._on_sync_pushed)
        self.sync.publish_failed.connect(self._on_publish_failed)
        self.sync.start()

    def _open_sync(self):
        dlg = SyncDialog(self)
        dlg.exec()

    def sync_configure(self, code, server, hello=False):
        sync.save_config(code, server)
        self._push_all = True
        self._sync_hello = hello
        self.sync.start()

    def sync_off(self):
        sync.disable()
        self.sync.stop()
        self.title_bar.sync_btn.set_state("off")

    def _on_sync_status(self, s):
        self.title_bar.sync_btn.set_state(s)
        if s != "online":
            self._inflight.clear()        # failed publishes get retried after reconnect
        if s == "online":
            if getattr(self, "_sync_hello", True):
                self.sync.hello_async()      # ask the other device to resend everything
            self._sync_hello = True
            self._schedule_push(100)

    def _on_sync_payload(self, payload):
        state = core.load_state()
        changed, snapshot = sync.handle_payload(state, payload, core.device_id())
        if changed:
            core.save_state(state)
            self.refresh()
        if snapshot and time.time() - getattr(self, "_last_snapshot", 0) > 30:
            # a peer said hello: resend everything, but at most once per 30 s so
            # many devices reconnecting can't create a storm of messages
            self._push_all = True
            self._last_snapshot = time.time()
        self._schedule_push()

    def _schedule_push(self, ms=250):
        if time.time() >= self._retry_until:        # honour backoff after a failed push
            self._sync_timer.start(ms)

    def _on_publish_failed(self):
        self._inflight.clear()
        self._retry_delay = min(120, self._retry_delay * 2 if self._retry_delay else self._retry_base)
        self._retry_until = time.time() + self._retry_delay
        self._sync_timer.start(int(self._retry_delay * 1000))

    def _on_sync_pushed(self, kind, iid, rev):
        self._retry_delay = 0
        self._retry_until = 0.0
        self._inflight.discard((kind, iid, rev))
        state = core.load_state()
        sync.mark_pushed(state, kind, iid, rev)
        core.save_state(state)

    def _sync_push(self):
        if not self.sync.active() or self.sync.state != "online":
            return
        state = core.load_state()
        if sync.ensure_stamped(state):
            core.save_state(state)
        out = [o for o in sync.collect_outgoing(state, force_all=self._push_all)
               if (o[0], o[1], o[2]) not in self._inflight]
        self._push_all = False
        if out:
            for o in out:
                self._inflight.add((o[0], o[1], o[2]))
            self.sync.publish_async(out)

    def changeEvent(self, e):
        # full 30 fps only while the window is focused; 10 fps otherwise
        if e.type() == e.Type.ActivationChange and hasattr(self, "frame_timer") and self.frame_timer.isActive():
            self.frame_timer.setInterval(self._fps_interval())
        super().changeEvent(e)

    def _celebrate(self, card):
        pt = card.ring.mapTo(self, card.ring.rect().center())
        self.confetti.burst(QPointF(pt), 24 if CALM else 70)

    # ---- mode / picker
    def _on_mode_change(self, mode):
        is_timer = mode == "Timer"
        self.add_btn.set_mode(mode)
        self.picker.setEnabled(is_timer)
        fx = QGraphicsOpacityEffect(self.timer_box)
        self.timer_box.setGraphicsEffect(fx)
        h = QPropertyAnimation(self.timer_box, b"maximumHeight")
        h.setDuration(320)
        h.setEasingCurve(QEasingCurve.OutCubic)
        h.setStartValue(self.timer_box.maximumHeight())
        h.setEndValue(TIMER_BOX_H if is_timer else 0)
        o = QVariantAnimation(self)
        o.setDuration(260)
        o.setStartValue(fx.opacity())
        o.setEndValue(1.0 if is_timer else 0.0)
        o.valueChanged.connect(lambda v: fx.setOpacity(clamp(v)))
        group = QParallelAnimationGroup(self)
        group.addAnimation(h)
        group.addAnimation(o)
        group.finished.connect(lambda: self.timer_box.setGraphicsEffect(None))
        self._mode_anim = group
        group.start()

    def _on_picker_changed(self, secs):
        for chip in self.chips:
            chip.set_active(chip.seconds == secs)

    def _add_item(self):
        state = core.load_state()
        label = self.label_edit.text().strip()
        if self.segment.current() == "Timer":
            secs = self.picker.value()
            if secs <= 0:
                self._shake(self.picker)
                return
            core.add_timer(state, secs, label)
        else:
            if core.load_settings().get("warn_stopwatch", True):
                dlg = StopwatchWarning(self)
                if dlg.exec() != QDialog.Accepted:
                    return
                if dlg.dont_ask:
                    self._set_warn(False)
            state = core.load_state()                 # the dialog was modal: re-read before writing
            core.add_stopwatch(state, label)
        self.label_edit.clear()
        self.add_btn.celebrate()
        self.refresh()

    def _shake(self, widget):
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(300)
        g = widget.pos()
        anim.setKeyValueAt(0.0, g)
        anim.setKeyValueAt(0.2, g + QPoint(-8, 0))
        anim.setKeyValueAt(0.4, g + QPoint(8, 0))
        anim.setKeyValueAt(0.6, g + QPoint(-5, 0))
        anim.setKeyValueAt(0.8, g + QPoint(5, 0))
        anim.setKeyValueAt(1.0, g)
        anim.start()
        self._shake_anim = anim

    # ---- heartbeat / sound
    def _heartbeat(self):
        now = time.time()
        if now - self._last_hb > 20:
            # our own 5 s tick was starved for 20+ s: the PC was asleep
            core.record_gap(core.load_state(), self._last_hb, now, "suspend")
        self._last_hb = now
        core.write_heartbeat(now=now)

    def _gui_notify(self, title, body):
        try:
            on = core.sound_enabled()
        except Exception:
            on = True
        if not on:
            body += "  (finish sound is off \u2014 click \u266a to turn it on)"
        self.tray.showMessage(title, body, QSystemTrayIcon.Information, 4000)
        try:
            if on:
                self.sound.play(core.sound_path())
            else:
                core._sound_log("finish sound skipped: sound is OFF (click the \u266a button or: etimer sound on)")
        except Exception as e:
            core._sound_log(f"GUI sound error: {e!r}")

    def _toggle_sound(self, on=None):
        on = (not core.sound_enabled()) if on is None else bool(on)
        core.set_sound_enabled(on)
        self.title_bar.set_sound_state(on)
        self.tray_sound_action.setChecked(on)

    def _test_sound(self):
        self.sound.play(core.sound_path())

    def _open_history(self, item_id):
        dlg = self._history_dialogs.get(item_id)
        if dlg is None or not dlg.isVisible():
            dlg = HistoryDialog(item_id, self)
            self._history_dialogs[item_id] = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ---- state sync
    def refresh(self, initial=False):
        state = core.check_and_notify(core.load_state(), notifier=self._gui_notify)
        items = state["items"]
        self.empty.setVisible(len(items) == 0)
        if hasattr(self, "sync") and self.sync.active() and sync.has_dirty(state):
            self._schedule_push()

        seen = set()
        for item in items:
            seen.add(item["id"])
            card = self.cards.get(item["id"])
            if card is None:
                card = TimerCard(item["id"], self._on_card_change, self._open_history, self._celebrate)
                self.cards[item["id"]] = card
                self.list_layout.insertWidget(self.list_layout.count() - 1, card)
                card.refresh(item)
                card.start_entrance(70 * self._init_idx if initial else 0)
                if initial:
                    self._init_idx += 1
                continue
            card.refresh(item)

        for item_id in list(self.cards.keys()):
            if item_id not in seen:
                self._remove_card(self.cards.pop(item_id))
        self._refresh_tray(items)

    def _remove_card(self, card):
        def done():
            self.list_layout.removeWidget(card)
            card.deleteLater()
        card.play_exit(done)

    def _on_card_change(self, removed_id=None):
        self.refresh()

    # ---- system tray
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_app_icon(), self)
        self.tray.setToolTip("Eternal Timer")
        self.tray_menu = QMenu()
        self.tray_open_action = QAction("Open Eternal Timer")
        self.tray_open_action.triggered.connect(self._show_window)
        self.tray_menu.addAction(self.tray_open_action)
        self.tray_menu.addSeparator()
        self.tray_sound_action = QAction("Finish sound")
        self.tray_sound_action.setCheckable(True)
        self.tray_sound_action.setChecked(core.sound_enabled())
        self.tray_sound_action.triggered.connect(lambda checked: self._toggle_sound(checked))
        self.tray_menu.addAction(self.tray_sound_action)
        self.tray_test_action = QAction("Test sound")
        self.tray_test_action.triggered.connect(self._test_sound)
        self.tray_menu.addAction(self.tray_test_action)
        self.tray_calm_action = QAction("Calm mode (less animation)")
        self.tray_calm_action.setCheckable(True)
        self.tray_calm_action.setChecked(CALM)
        self.tray_calm_action.triggered.connect(lambda checked: self._set_calm(checked))
        self.tray_menu.addAction(self.tray_calm_action)
        self.tray_warn_action = QAction("Warn before starting a stopwatch")
        self.tray_warn_action.setCheckable(True)
        self.tray_warn_action.setChecked(core.load_settings().get("warn_stopwatch", True))
        self.tray_warn_action.triggered.connect(lambda checked: self._set_warn(checked))
        self.tray_menu.addAction(self.tray_warn_action)
        self.tray_menu.addSeparator()
        self.tray_quit_action = QAction("Quit")
        self.tray_quit_action.triggered.connect(QApplication.quit)
        self.tray_menu.addAction(self.tray_quit_action)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self._show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _refresh_tray(self, items):
        for action in list(self.tray_menu.actions()):
            if action.data() == "live":
                self.tray_menu.removeAction(action)
        insert_before = self.tray_sound_action
        if not items:
            a = QAction("Nothing running")
            a.setEnabled(False)
            a.setData("live")
            self.tray_menu.insertAction(insert_before, a)
        else:
            for item in items:
                a = QAction(core.summary_line(item).split("] ", 1)[1])
                a.setEnabled(False)
                a.setData("live")
                self.tray_menu.insertAction(insert_before, a)

        active = [i for i in items if i["mode"] == "timer" and i["running"]]
        if active:
            soonest = min(core.remaining_seconds(i) for i in active)
            self.tray.setToolTip(f"Eternal Timer \u2014 next in {core.format_hms(max(0, soonest))}")
        else:
            self.tray.setToolTip("Eternal Timer")

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if not self.frame_timer.isActive():
            self._last_frame = time.monotonic()
            self.frame_timer.start(self._fps_interval())
        self.frame_timer.setInterval(self._fps_interval())

    def hide_to_tray(self):
        self.hide()
        self.frame_timer.stop()      # no animation work while hidden
        self.tray.showMessage(
            "Eternal Timer",
            "Still running in the background \u2014 click the tray icon to reopen.",
            QSystemTrayIcon.Information, 2000,
        )

    def closeEvent(self, event):
        event.ignore()
        self.hide_to_tray()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    init_fonts(app)
    app.setWindowIcon(make_app_icon())
    window = EternalTimerWindow()
    window.show()

    # A clean-exit marker lets the next start tell "app was closed / PC shut
    # down normally" apart from "power cut or crash". SIGTERM/SIGINT (what a
    # normal logout/shutdown sends) are routed through app.quit() so they
    # count as clean; the 400 ms refresh timer lets Python see the signal.
    import os
    core.app_log(f"GUI started pid={os.getpid()} platform={app.platformName()}")

    def on_quit():
        core.app_log("GUI quitting (aboutToQuit)")
        core.write_heartbeat(clean=True)

    def on_signal(signum, _frame):
        core.app_log(f"GUI got signal {signum} -> quitting")
        app.quit()

    app.aboutToQuit.connect(on_quit)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    code = app.exec()
    core.app_log(f"GUI event loop ended code={code}")
    sys.exit(code)


if __name__ == "__main__":
    main()
