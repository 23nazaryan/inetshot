#!/usr/bin/env python3
"""
InetShot for Linux — a Lightshot-style capture & annotate tool.

Flow: launch -> grabs the whole screen -> dims it -> you drag a region ->
inline toolbar appears (Move / Arrow / Pen / Rect / Highlighter / Line / Text,
colors, sizes, Undo) -> Copy / Save / Close.

Works on Wayland (GNOME) and X11. The capture is taken with an external grabber
(grim / gnome-screenshot / spectacle) that can see real Wayland content; the
selection overlay is then shown under XWayland. Launch via the wrapper script
(inetshot.sh) which forces QT_QPA_PLATFORM=xcb.

Keys:  Esc cancel · Enter or Ctrl+C copy · Ctrl+S save · Ctrl+Z undo
"""

import os
import sys
import math
import time
import shutil
import tempfile
import subprocess
from datetime import datetime
from urllib.parse import unquote, urlparse

from PyQt6.QtCore import (Qt, QRect, QPoint, QPointF, QSize, QMimeData, QUrl,
                          QTimer)
from PyQt6.QtGui import (
    QGuiApplication, QImage, QPixmap, QPainter, QPen, QBrush, QColor, QFont,
    QPolygonF, QIcon, QFontMetrics, QShortcut, QKeySequence,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QToolButton, QButtonGroup, QFileDialog,
    QLineEdit, QFrame,
)

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
COLORS = ["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#111111", "#ffffff"]
PEN_WIDTHS = {0: 2, 1: 4, 2: 7}        # S / M / L for line tools
MARKER_WIDTHS = {0: 10, 1: 18, 2: 28}  # S / M / L for highlighter
DEFAULT_COLOR = "#ef4444"
DEFAULT_SIZE = 1


# ----------------------------------------------------------------------------
# Screen capture (tries several backends, first success wins)
# ----------------------------------------------------------------------------
def _load(tmp) -> QImage | None:
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        img = QImage(tmp)
        if not img.isNull():
            return img
    return None


def _gnome_dbus_shot(tmp) -> bool:
    """Ask GNOME Shell to screenshot directly (no GTK process, no flash).
    Works on GNOME up to ~43; harmlessly fails on newer versions."""
    if not shutil.which("gdbus"):
        return False
    try:
        r = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.gnome.Shell.Screenshot",
             "--object-path", "/org/gnome/Shell/Screenshot",
             "--method", "org.gnome.Shell.Screenshot.Screenshot",
             "false", "false", tmp],   # include_cursor=false, flash=false
            capture_output=True, text=True, timeout=8)
    except Exception:
        return False
    return r.returncode == 0 and "true" in r.stdout.lower()


def _portal_shot() -> QImage | None:
    """xdg-desktop-portal Screenshot, called in-process (no GTK subprocess).
    Fastest path on modern GNOME/Wayland. Returns the image and removes the
    file the portal writes to ~/Pictures. Degrades to None if unavailable."""
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except Exception:
        return None
    try:
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        obj = bus.get_object("org.freedesktop.portal.Desktop",
                             "/org/freedesktop/portal/desktop")
        sc = dbus.Interface(obj, "org.freedesktop.portal.Screenshot")
        loop = GLib.MainLoop()
        res = {}
        sender = bus.get_unique_name()[1:].replace(".", "_")
        token = "inetshot%d" % int(time.time() * 1000)
        req_path = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender, token)

        def on_response(response, results):
            res["uri"] = results.get("uri")
            loop.quit()

        bus.add_signal_receiver(
            on_response, signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request", path=req_path)
        sc.Screenshot("", dbus.Dictionary(
            {"handle_token": dbus.String(token),
             "interactive": dbus.Boolean(False)}, signature="sv"))
        GLib.timeout_add(8000, loop.quit)
        loop.run()
    except Exception:
        return None
    uri = res.get("uri")
    if not uri or not str(uri).startswith("file://"):
        return None
    fpath = unquote(urlparse(str(uri)).path)
    img = QImage(fpath)
    try:
        os.remove(fpath)        # don't litter ~/Pictures with transient grabs
    except OSError:
        pass
    return img if not img.isNull() else None


def capture_screen() -> QImage | None:
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        # 1) fastest paths first
        if shutil.which("grim"):                       # wlroots (sway/hyprland)
            try:
                subprocess.run(["grim", tmp], timeout=8,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                img = _load(tmp)
                if img:
                    return img
            except Exception:
                pass
        portal_img = _portal_shot()                     # xdg portal, in-process (modern GNOME)
        if portal_img is not None:
            return portal_img
        if _gnome_dbus_shot(tmp):                       # legacy GNOME (<=43), no flash
            img = _load(tmp)
            if img:
                return img
        # 2) heavier fallbacks
        candidates = [
            ["gnome-screenshot", "-f", tmp],
            ["spectacle", "-b", "-n", "-o", tmp],
        ]
        for cmd in candidates:
            if not shutil.which(cmd[0]):
                continue
            try:
                subprocess.run(cmd, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                continue
            img = _load(tmp)
            if img:
                return img
        # 3) X11 fallback via Qt (also works under XWayland for X apps only)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            pm = screen.grabWindow(0)
            if not pm.isNull():
                return pm.toImage()
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Small vector icons drawn in code (no asset files needed)
# ----------------------------------------------------------------------------
def _icon(draw_fn, size=20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1f2937"))
    pen.setWidthF(1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    draw_fn(p, size)
    p.end()
    return QIcon(pm)


def _ic_move(p, s):
    # hand-ish / move arrows
    c = s / 2
    p.drawLine(int(c), 3, int(c), s - 3)
    p.drawLine(3, int(c), s - 3, int(c))
    p.drawLine(int(c), 3, int(c) - 3, 7); p.drawLine(int(c), 3, int(c) + 3, 7)
    p.drawLine(int(c), s - 3, int(c) - 3, s - 7); p.drawLine(int(c), s - 3, int(c) + 3, s - 7)
    p.drawLine(3, int(c), 7, int(c) - 3); p.drawLine(3, int(c), 7, int(c) + 3)
    p.drawLine(s - 3, int(c), s - 7, int(c) - 3); p.drawLine(s - 3, int(c), s - 7, int(c) + 3)


def _ic_arrow(p, s):
    p.drawLine(4, s - 4, s - 5, 5)
    p.drawLine(s - 5, 5, s - 10, 5)
    p.drawLine(s - 5, 5, s - 5, 10)


def _ic_pen(p, s):
    p.drawLine(4, s - 4, s - 6, 6)
    p.drawLine(s - 6, 6, s - 4, 8)
    p.drawLine(4, s - 4, 4, s - 4)


def _ic_rect(p, s):
    p.drawRect(4, 5, s - 8, s - 10)


def _ic_marker(p, s):
    pen = p.pen(); pen.setWidthF(5); pen.setColor(QColor(245, 200, 60))
    p.setPen(pen)
    p.drawLine(4, s - 5, s - 4, 5)


def _ic_line(p, s):
    p.drawLine(4, s - 4, s - 4, 4)


def _ic_text(p, s):
    f = QFont(); f.setPixelSize(int(s * 0.7)); f.setBold(True)
    p.setFont(f)
    p.drawText(QRect(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "T")


def _ic_undo(p, s):
    p.drawArc(4, 4, s - 8, s - 8, 40 * 16, 260 * 16)
    p.drawLine(5, 6, 9, 5); p.drawLine(5, 6, 6, 10)


def _ic_save(p, s):
    p.drawLine(int(s / 2), 3, int(s / 2), s - 7)
    p.drawLine(int(s / 2), s - 7, int(s / 2) - 4, s - 11)
    p.drawLine(int(s / 2), s - 7, int(s / 2) + 4, s - 11)
    p.drawLine(4, s - 4, s - 4, s - 4)


def _ic_close(p, s):
    p.drawLine(5, 5, s - 5, s - 5)
    p.drawLine(s - 5, 5, 5, s - 5)


# ----------------------------------------------------------------------------
# Annotation model
# ----------------------------------------------------------------------------
class Annotation:
    def __init__(self, kind, color, width, p1=None, p2=None, points=None, text=""):
        self.kind = kind          # pen|line|arrow|rect|marker|text
        self.color = QColor(color)
        self.width = width
        self.p1 = p1
        self.p2 = p2
        self.points = points or []
        self.text = text


def _draw_arrowhead(painter, p1: QPointF, p2: QPointF, width):
    angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
    size = max(10.0, width * 3.2)
    a1 = angle + math.radians(150)
    a2 = angle - math.radians(150)
    q1 = QPointF(p2.x() + size * math.cos(a1), p2.y() + size * math.sin(a1))
    q2 = QPointF(p2.x() + size * math.cos(a2), p2.y() + size * math.sin(a2))
    poly = QPolygonF([p2, q1, q2])
    painter.setBrush(QBrush(painter.pen().color()))
    painter.drawPolygon(poly)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def draw_annotations(painter: QPainter, items, current=None):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    seq = list(items)
    if current is not None:
        seq.append(current)
    for a in seq:
        pen = QPen(a.color)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if a.kind == "marker":
            col = QColor(a.color); col.setAlpha(110)
            pen.setColor(col); pen.setWidthF(a.width)
            painter.setPen(pen)
            if a.points:
                for i in range(1, len(a.points)):
                    painter.drawLine(a.points[i - 1], a.points[i])
        elif a.kind == "pen":
            pen.setWidthF(a.width); painter.setPen(pen)
            for i in range(1, len(a.points)):
                painter.drawLine(a.points[i - 1], a.points[i])
        elif a.kind == "line":
            pen.setWidthF(a.width); painter.setPen(pen)
            if a.p1 and a.p2:
                painter.drawLine(a.p1, a.p2)
        elif a.kind == "arrow":
            pen.setWidthF(a.width); painter.setPen(pen)
            if a.p1 and a.p2:
                painter.drawLine(a.p1, a.p2)
                _draw_arrowhead(painter, QPointF(a.p1), QPointF(a.p2), a.width)
        elif a.kind == "rect":
            pen.setWidthF(a.width); painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if a.p1 and a.p2:
                painter.drawRect(QRect(a.p1, a.p2).normalized())
        elif a.kind == "text":
            f = QFont(); f.setPixelSize(max(12, int(a.width * 4)))
            painter.setFont(f); painter.setPen(QPen(a.color))
            if a.p1 and a.text:
                fm = QFontMetrics(f)
                painter.drawText(a.p1.x(), a.p1.y() + fm.ascent(), a.text)


# ----------------------------------------------------------------------------
# Toolbar
# ----------------------------------------------------------------------------
class Toolbar(QFrame):
    def __init__(self, overlay):
        super().__init__(overlay)
        self.overlay = overlay
        self.setObjectName("toolbar")
        self.setStyleSheet("""
            QFrame#toolbar {
                background: #f4f4f5; border: 1px solid #d4d4d8;
                border-radius: 12px;
            }
            QToolButton {
                background: transparent; border: none; border-radius: 7px;
                padding: 5px; margin: 1px;
            }
            QToolButton:hover { background: #e4e4e7; }
            QToolButton:checked { background: #d4d4d8; }
            QToolButton#move:checked { background: #ff5a36; }
            QToolButton#copy {
                background: #ef4444; color: white; border-radius: 8px;
                padding: 5px 10px; font-weight: 600;
            }
            QToolButton#copy:hover { background: #dc2626; }
            QToolButton#copypath {
                background: #e4e4e7; color: #374151; border-radius: 8px;
                padding: 5px 10px; font-weight: 600;
            }
            QToolButton#copypath:hover { background: #d4d4d8; }
            #sep { color: #d4d4d8; }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(2)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        def add_tool(name, icon, tooltip, obj=None):
            b = QToolButton()
            b.setIcon(icon); b.setIconSize(QSize(20, 20))
            b.setCheckable(True); b.setToolTip(tooltip)
            if obj:
                b.setObjectName(obj)
            self.tool_group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _, n=name: self.overlay.set_tool(n))
            return b

        self.btn_move = add_tool("move", _icon(_ic_move), "Move / resize", "move")
        self.btn_move.setChecked(True)
        add_tool("arrow", _icon(_ic_arrow), "Arrow")
        add_tool("pen", _icon(_ic_pen), "Pen")
        add_tool("rect", _icon(_ic_rect), "Rectangle")
        add_tool("marker", _icon(_ic_marker), "Highlighter")
        add_tool("line", _icon(_ic_line), "Line")
        add_tool("text", _icon(_ic_text), "Text")

        self._sep(lay)

        # color swatches
        self.color_group = QButtonGroup(self)
        self.color_group.setExclusive(True)
        for c in COLORS:
            b = QToolButton(); b.setCheckable(True)
            b.setFixedSize(22, 22)
            border = "#9ca3af" if c.lower() == "#ffffff" else c
            b.setStyleSheet(
                f"QToolButton{{background:{c};border:1px solid {border};border-radius:11px;}}"
                f"QToolButton:checked{{border:2px solid #1f2937;}}"
            )
            if c == DEFAULT_COLOR:
                b.setChecked(True)
            self.color_group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _, col=c: self.overlay.set_color(col))

        self._sep(lay)

        # sizes S/M/L
        self.size_group = QButtonGroup(self); self.size_group.setExclusive(True)
        for i, label in enumerate(["S", "M", "L"]):
            b = QToolButton(); b.setText(label); b.setCheckable(True)
            b.setFixedSize(24, 24)
            b.setStyleSheet("QToolButton{font-weight:600;color:#374151;}")
            if i == DEFAULT_SIZE:
                b.setChecked(True)
            self.size_group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _, idx=i: self.overlay.set_size(idx))

        self._sep(lay)

        b_undo = QToolButton(); b_undo.setIcon(_icon(_ic_undo)); b_undo.setIconSize(QSize(20, 20))
        b_undo.setToolTip("Undo (Ctrl+Z)"); b_undo.clicked.connect(self.overlay.undo)
        lay.addWidget(b_undo)

        b_save = QToolButton(); b_save.setIcon(_icon(_ic_save)); b_save.setIconSize(QSize(20, 20))
        b_save.setToolTip("Save (Ctrl+S)"); b_save.clicked.connect(self.overlay.do_save)
        lay.addWidget(b_save)

        b_copy = QToolButton(); b_copy.setObjectName("copy")
        b_copy.setText("Copy"); b_copy.setToolTip("Copy image to clipboard (Ctrl+C)\nFor browsers, chat apps (Telegram), etc.")
        b_copy.clicked.connect(self.overlay.do_copy)
        lay.addWidget(b_copy)

        b_copypath = QToolButton(); b_copypath.setObjectName("copypath")
        b_copypath.setText("Copy path")
        b_copypath.setToolTip("Copy file path as text (Ctrl+Shift+C)\n"
                              "For terminals — paste into Claude Code, etc.")
        b_copypath.clicked.connect(self.overlay.do_copy_path)
        lay.addWidget(b_copypath)

        b_close = QToolButton(); b_close.setIcon(_icon(_ic_close)); b_close.setIconSize(QSize(20, 20))
        b_close.setToolTip("Cancel (Esc)"); b_close.clicked.connect(self.overlay.cancel)
        lay.addWidget(b_close)

        self.adjustSize()

    def _sep(self, lay):
        line = QFrame(); line.setObjectName("sep")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet("color:#d4d4d8;")
        line.setFixedHeight(24)
        lay.addWidget(line)


# ----------------------------------------------------------------------------
# Overlay
# ----------------------------------------------------------------------------
HANDLE = 8  # px hit radius for resize handles


class Overlay(QWidget):
    def __init__(self, image: QImage, geo: QRect):
        super().__init__()
        self.orig = image
        self.geo = geo
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setGeometry(geo)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # display pixmap scaled to logical overlay size
        self.display = QPixmap.fromImage(image).scaled(
            geo.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.scale_x = image.width() / max(1, geo.width())
        self.scale_y = image.height() / max(1, geo.height())

        self.selection: QRect | None = None
        self.annotations: list[Annotation] = []
        self.current: Annotation | None = None

        self.tool = "move"
        self.color = DEFAULT_COLOR
        self._size_idx = DEFAULT_SIZE

        # interaction state
        self._making = False          # drawing initial selection
        self._start = QPoint()
        self._drag_mode = None        # 'move' | 'resize-xx'
        self._drag_off = QPoint()
        self._sel_at_press = None

        self.text_edit: QLineEdit | None = None
        self.toolbar = Toolbar(self)
        self.toolbar.hide()

        # shortcuts
        QShortcut(QKeySequence("Escape"), self, activated=self.cancel)
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self.do_copy)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, activated=self.do_copy_path)
        QShortcut(QKeySequence("Return"), self, activated=self.do_copy)
        QShortcut(QKeySequence("Enter"), self, activated=self.do_copy)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.do_save)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)

    # -- tool setters ----------------------------------------------------
    def set_tool(self, name):
        self.commit_text()
        self.tool = name
        self.setCursor(Qt.CursorShape.ArrowCursor if name == "move"
                       else (Qt.CursorShape.IBeamCursor if name == "text"
                             else Qt.CursorShape.CrossCursor))

    def set_color(self, c):
        self.color = c

    def set_size(self, i):
        self._size_idx = i

    def _pen_w(self):
        return PEN_WIDTHS[self._size_idx]

    def _marker_w(self):
        return MARKER_WIDTHS[self._size_idx]

    # -- keep background aligned to the real window size -----------------
    def resizeEvent(self, e):
        if not self.orig.isNull() and self.width() > 0 and self.height() > 0:
            if self.display.size() != self.size():
                self.display = QPixmap.fromImage(self.orig).scaled(
                    self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self.scale_x = self.orig.width() / max(1, self.width())
                self.scale_y = self.orig.height() / max(1, self.height())
        super().resizeEvent(e)

    # -- painting --------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        p.drawPixmap(0, 0, self.display)
        p.fillRect(self.rect(), QColor(0, 0, 0, 130))
        if self.selection:
            s = self.selection.normalized()
            # bright selection slice (both target and source must be QRect)
            p.drawPixmap(s, self.display, s)
            # border
            pen = QPen(QColor("#ff5a36")); pen.setWidth(2)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(s)
            # annotations clipped to selection
            p.save(); p.setClipRect(s)
            draw_annotations(p, self.annotations, self.current)
            p.restore()
            # handles (only in move mode)
            if self.tool == "move":
                p.setBrush(QBrush(QColor("#ffffff")))
                p.setPen(QPen(QColor("#ff5a36"), 1))
                for hp in self._handles(s).values():
                    p.drawRect(hp.x() - 4, hp.y() - 4, 8, 8)
            # size label
            label = f"{s.width()} × {s.height()}"
            f = QFont(); f.setPixelSize(13); f.setBold(True); p.setFont(f)
            fm = QFontMetrics(f); tw = fm.horizontalAdvance(label) + 14
            ly = s.top() - 26 if s.top() > 28 else s.top() + 6
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#ff5a36"))
            p.drawRoundedRect(s.left(), ly, tw, 20, 4, 4)
            p.setPen(QColor("white"))
            p.drawText(s.left() + 7, ly + 15, label)
        else:
            f = QFont(); f.setPixelSize(15); p.setFont(f)
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Drag to select an area   ·   Esc to cancel")

    def _handles(self, s: QRect) -> dict:
        """The 8 resize anchors (corners + edge midpoints), keyed by name."""
        cx, cy = s.center().x(), s.center().y()
        return {
            "tl": QPoint(s.left(), s.top()), "tr": QPoint(s.right(), s.top()),
            "bl": QPoint(s.left(), s.bottom()), "br": QPoint(s.right(), s.bottom()),
            "t": QPoint(cx, s.top()), "b": QPoint(cx, s.bottom()),
            "l": QPoint(s.left(), cy), "r": QPoint(s.right(), cy),
        }

    def _hit_handle(self, pos: QPoint):
        if not self.selection:
            return None
        for name, hp in self._handles(self.selection.normalized()).items():
            if abs(pos.x() - hp.x()) <= HANDLE and abs(pos.y() - hp.y()) <= HANDLE:
                return name
        return None

    # -- mouse -----------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()

        if self.selection is None:
            self._making = True
            self._start = pos
            self.selection = QRect(pos, pos)
            return

        s = self.selection.normalized()

        if self.tool == "move":
            h = self._hit_handle(pos)
            if h:
                self._drag_mode = "resize-" + h
                self._sel_at_press = QRect(s)
                return
            if s.contains(pos):
                self._drag_mode = "move"
                self._drag_off = pos - s.topLeft()
                self._sel_at_press = QRect(s)
                return
            return

        if self.tool == "text":
            if s.contains(pos):
                self.start_text(pos)
            return

        # drawing tools — only inside selection
        if not s.contains(pos):
            return
        w = self._marker_w() if self.tool == "marker" else self._pen_w()
        self.current = Annotation(self.tool, self.color, w, p1=pos, p2=pos,
                                  points=[pos])

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()

        if self._making:
            self.selection = QRect(self._start, pos)
            self.update(); return

        if self._drag_mode == "move" and self._sel_at_press:
            ns = QRect(self._sel_at_press)
            ns.moveTopLeft(pos - self._drag_off)
            self.selection = self._clamp(ns)
            self.position_toolbar(); self.update(); return

        if self._drag_mode and self._drag_mode.startswith("resize-"):
            self._resize(self._drag_mode[7:], pos)
            self.position_toolbar(); self.update(); return

        if self.current is not None:
            if self.current.kind in ("pen", "marker"):
                self.current.points.append(pos)
            else:
                self.current.p2 = pos
            self.update(); return

        # cursor feedback in move mode
        if self.tool == "move" and self.selection:
            h = self._hit_handle(pos)
            cursors = {
                "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
            }
            if h:
                self.setCursor(cursors[h])
            elif self.selection.normalized().contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._making:
            self._making = False
            self.selection = self.selection.normalized()
            if self.selection.width() < 5 or self.selection.height() < 5:
                self.selection = None
                self.update(); return
            self.toolbar.show()
            self.position_toolbar()
            self.update(); return

        if self._drag_mode:
            self._drag_mode = None; self._sel_at_press = None
            return

        if self.current is not None:
            if self.current.kind in ("pen", "marker") and len(self.current.points) > 1:
                self.annotations.append(self.current)
            elif self.current.kind in ("line", "arrow", "rect") and self.current.p2:
                self.annotations.append(self.current)
            self.current = None
            self.update()

    def _clamp(self, r: QRect) -> QRect:
        r = QRect(r)
        if r.left() < 0: r.moveLeft(0)
        if r.top() < 0: r.moveTop(0)
        if r.right() > self.width(): r.moveRight(self.width())
        if r.bottom() > self.height(): r.moveBottom(self.height())
        return r

    MIN_SIZE = 5  # px floor so a selection can't collapse to nothing

    def _resize(self, which, pos):
        s = QRect(self._sel_at_press)
        x1, y1, x2, y2 = s.left(), s.top(), s.right(), s.bottom()
        if "l" in which: x1 = min(pos.x(), x2 - self.MIN_SIZE)
        if "r" in which: x2 = max(pos.x(), x1 + self.MIN_SIZE)
        if "t" in which: y1 = min(pos.y(), y2 - self.MIN_SIZE)
        if "b" in which: y2 = max(pos.y(), y1 + self.MIN_SIZE)
        self.selection = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()

    # -- toolbar placement ----------------------------------------------
    def position_toolbar(self):
        if not self.selection:
            return
        s = self.selection.normalized()
        self.toolbar.adjustSize()
        tw, th = self.toolbar.width(), self.toolbar.height()
        x = min(max(s.left(), 4), self.width() - tw - 4)
        y = s.bottom() + 10
        if y + th > self.height() - 4:
            y = s.top() - th - 10
        if y < 4:
            y = s.bottom() - th - 10
        self.toolbar.move(x, y)
        self.toolbar.raise_()

    # -- text tool -------------------------------------------------------
    def start_text(self, pos):
        self.commit_text()
        e = QLineEdit(self)
        f = QFont(); f.setPixelSize(max(12, int(self._pen_w() * 4)))
        e.setFont(f)
        e.setStyleSheet(
            f"background:rgba(255,255,255,0.85);color:{self.color};"
            f"border:1px dashed {self.color};padding:1px 3px;")
        e.move(pos)
        e.resize(200, e.sizeHint().height())
        e.show(); e.setFocus()
        e._pos = pos
        e.returnPressed.connect(self.commit_text)
        self.text_edit = e

    def commit_text(self):
        e = self.text_edit
        if e is None:
            return
        txt = e.text().strip()
        pos = e._pos
        self.text_edit = None
        e.deleteLater()
        if txt:
            self.annotations.append(
                Annotation("text", self.color, self._pen_w(), p1=pos, text=txt))
            self.update()

    # -- actions ---------------------------------------------------------
    def undo(self):
        if self.annotations:
            self.annotations.pop()
            self.update()

    def export_image(self) -> QImage | None:
        if not self.selection:
            return None
        self.commit_text()
        s = self.selection.normalized()
        rx = QRect(int(s.x() * self.scale_x), int(s.y() * self.scale_y),
                   int(s.width() * self.scale_x), int(s.height() * self.scale_y))
        result = self.orig.copy(rx)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.scale_x, self.scale_y)
        p.translate(-s.x(), -s.y())
        draw_annotations(p, self.annotations)
        p.end()
        return result

    def _notify(self, text):
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-t", "2500", "InetShot", text])

    def _save_to_pictures(self, img) -> str | None:
        """Write the result to ~/Pictures and return its path (or None)."""
        pics = os.path.expanduser("~/Pictures")
        try:
            os.makedirs(pics, exist_ok=True)
            path = os.path.join(
                pics, "InetShot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
            img.save(path, "PNG")
            return path
        except Exception:
            return None

    def do_copy(self):
        """Copy the IMAGE to the clipboard — for GUI apps (browsers, Telegram…)."""
        img = self.export_image()
        if img is None:
            return
        path = self._save_to_pictures(img)   # stable file the URI/path point to

        # Carry several representations at once:
        #   image/png      -> browsers / chat apps (Telegram, …) paste the image
        #   text/uri-list  -> the file:// URI for file managers
        #   text/plain     -> the path text
        mime = QMimeData()
        mime.setImageData(img)
        if path:
            mime.setUrls([QUrl.fromLocalFile(path)])
            mime.setText(path)
        cb = QApplication.clipboard()
        cb.setMimeData(mime)

        self._notify("Copied image to clipboard")
        self.hide()
        # On Wayland (and X11 with no clipboard manager) a clipboard offer dies
        # with the process that owns it — GNOME only persists text/plain on exit,
        # dropping the image. So rather than quitting we keep running, invisibly,
        # holding the offer the way wl-copy / any clipboard manager does, and only
        # quit once another app takes ownership (e.g. the next copy). connect()
        # after setMimeData so our own set here doesn't trigger an immediate quit.
        cb.dataChanged.connect(self.finish)

    def _wl_copy_text(self, text) -> bool:
        """Put plain text on the Wayland clipboard via wl-copy, which forks a
        daemon that keeps owning it after we exit. Returns True on success."""
        if not (os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy")):
            return False
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"), timeout=5)
            return p.returncode == 0
        except Exception:
            return False

    def do_copy_path(self):
        """Copy only the saved file PATH as text — for terminals.
        JetBrains/PhpStorm terminals refuse to paste when the clipboard holds an
        image, so this puts text and nothing else: every terminal pastes the
        path, and Claude Code then loads the existing file as an image."""
        img = self.export_image()
        if img is None:
            return
        path = self._save_to_pictures(img)
        if not path:
            self._notify("Copy path failed (could not save file)")
            self.finish()
            return
        self.hide()        # vanish immediately so the action feels instant
        self._notify("Copied path: " + os.path.basename(path))
        if self._wl_copy_text(path):
            # wl-copy holds the text; safe to exit immediately.
            self.finish()
            return
        # X11 / no-wl-copy fallback: Qt owns the selection, so we must stay alive
        # briefly for the compositor to take ownership before we quit (quitting
        # at once drops it and the clipboard reverts to its previous contents).
        QApplication.clipboard().setText(path)
        QTimer.singleShot(1500, self.finish)

    def do_save(self):
        img = self.export_image()
        if img is None:
            return
        default = os.path.expanduser(
            "~/Pictures/InetShot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png")
        self.hide()
        path, _ = QFileDialog.getSaveFileName(None, "Save screenshot", default,
                                              "PNG image (*.png)")
        if path:
            if not path.lower().endswith(".png"):
                path += ".png"
            img.save(path, "PNG")
            self._notify("Saved: " + os.path.basename(path))
        self.finish()

    def cancel(self):
        self.finish()

    def finish(self):
        self.close()
        QApplication.quit()


# ----------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    img = capture_screen()
    if img is None or img.isNull():
        sys.stderr.write(
            "InetShot: screen capture failed. Install one of: "
            "gnome-screenshot, grim, or spectacle.\n")
        sys.exit(1)
    screen = QGuiApplication.primaryScreen()
    geo = screen.geometry()
    w = Overlay(img, geo)
    # genuine fullscreen so GNOME hides its panel and dock (otherwise they
    # float on top of a normal window -> the duplicate panel/dock you saw)
    w.showFullScreen()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
