"""Terminal widget: draws an emulator Screen, sends keys to the SSH session."""
import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (QClipboard, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QKeySequence,
                           QPainter)
from PySide6.QtWidgets import QWidget

from . import termtheme
from .emulator import BOLD, DIM, ITALIC, REVERSE, RGB, STRIKE, UNDER, Screen

MAC = sys.platform == "darwin"
# "Homebrew" look (as in the macOS Terminal profile): green text on black
BG, FG, SEL = QColor("#000000"), QColor("#28FE14"), QColor(40, 254, 20, 80)
CURSOR = QColor(60, 200, 45)
ANSI = ["#000000", "#C80000", "#00B800", "#C8C800", "#4A4AFF", "#C800C8", "#00B8C8", "#BFBFBF",
        "#666666", "#FF3030", "#00E600", "#F0F000", "#6A6AFF", "#F030F0", "#00F0F0", "#FFFFFF"]
FONTS = ["Cascadia Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Courier New"]


# Same shortcuts on every computer (Linux, Windows, macOS: "Ctrl" is the Cmd key there). Changeable in Account > Terminal shortcuts.
DEFAULT_SHORTCUTS = {"copy": "Ctrl+C", "paste": "Ctrl+V", "zoom_in": "Ctrl++", "zoom_out": "Ctrl+-"}
SHORTCUTS = dict(DEFAULT_SHORTCUTS)


def key_text(e):
    try:
        return QKeySequence(e.keyCombination()).toString(QKeySequence.PortableText)
    except Exception:  # noqa: BLE001
        return ""


def palette(i):
    if i < 16:
        return QColor(ANSI[i])
    if i < 232:
        i -= 16
        v = [0, 95, 135, 175, 215, 255]
        return QColor(v[i // 36], v[(i // 6) % 6], v[i % 6])
    g = 8 + (i - 232) * 10
    return QColor(g, g, g)


def to_color(c, default):
    if c == -1:
        return default
    if c & RGB:
        return QColor((c >> 16) & 255, (c >> 8) & 255, c & 255)
    return palette(c)


KEYS = {Qt.Key_Return: b"\r", Qt.Key_Enter: b"\r", Qt.Key_Backspace: b"\x7f", Qt.Key_Tab: b"\t", Qt.Key_Escape: b"\x1b",
        Qt.Key_Insert: b"\x1b[2~", Qt.Key_Delete: b"\x1b[3~", Qt.Key_PageUp: b"\x1b[5~", Qt.Key_PageDown: b"\x1b[6~"}
FKEYS = {Qt.Key_F1: "OP", Qt.Key_F2: "OQ", Qt.Key_F3: "OR", Qt.Key_F4: "OS", Qt.Key_F5: "[15~", Qt.Key_F6: "[17~",
         Qt.Key_F7: "[18~", Qt.Key_F8: "[19~", Qt.Key_F9: "[20~", Qt.Key_F10: "[21~", Qt.Key_F11: "[23~", Qt.Key_F12: "[24~"}


class TermView(QWidget):
    def __init__(self, session, size=None):
        super().__init__()
        self.setMouseTracking(True)
        self.session = session
        self.screen = Screen(80, 24)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setCursor(Qt.IBeamCursor)
        self.pt = size or termtheme.size()
        self.family = termtheme.family()
        self._metrics()
        self.offset = 0                 # lines scrolled back
        self.sel = None                 # ((row, col), (row, col)) in absolute line numbers
        self.anchor = None
        self.blink = True
        self._timer = QTimer(self, singleShot=True, interval=15, timeout=self.update)
        self._bt = QTimer(self, interval=530, timeout=self._blink)
        self._bt.start()
        session.data.connect(self.on_data)

    def attach(self, session):
        """use a new connection (after a reconnect) but keep what is on the screen"""
        try:
            self.session.data.disconnect(self.on_data)
        except (RuntimeError, TypeError):
            pass
        self.session = session
        session.data.connect(self.on_data)
        session.resize(self.screen.cols, self.screen.rows)

    def _metrics(self):
        self.font_n = QFont(self.family, self.pt)
        self.font_n.setStyleHint(QFont.Monospace)
        self.font_n.setFixedPitch(True)
        self.font_b = QFont(self.font_n)
        self.font_b.setBold(True)
        self.font_i = QFont(self.font_n)
        self.font_i.setItalic(True)
        # every character cell must be a whole number of pixels wide, otherwise typed text drifts away from the cursor
        self.font_n.setKerning(False)
        fm = QFontMetricsF(self.font_n)
        adv = fm.horizontalAdvance("M")
        cw = max(1, round(adv))
        self.font_n.setLetterSpacing(QFont.AbsoluteSpacing, cw - adv)
        self.font_b = QFont(self.font_n)
        self.font_b.setBold(True)
        self.font_i = QFont(self.font_n)
        self.font_i.setItalic(True)
        fm = QFontMetricsF(self.font_n)
        self.cw, self.ch, self.asc = cw, max(1, round(fm.height())), round(fm.ascent())

    def set_font(self, family, pt):
        """Settings: another font or size (colours are read when painting)"""
        self.family, self.pt = family, max(7, min(32, int(pt)))
        self._metrics()
        self.resizeEvent(None)
        self.update()

    def _blink(self):
        self.blink = not self.blink
        if self.hasFocus():
            self.update()

    # ---- data in
    def on_data(self, data):
        at_bottom = self.offset == 0
        before = len(self.screen.scroll)
        self.screen.feed(data)
        if self.screen.responses:
            for r in self.screen.responses:
                self.session.send(r)
            self.screen.responses.clear()
        if not at_bottom:
            self.offset += len(self.screen.scroll) - before
        if not self._timer.isActive():
            self._timer.start()

    # ---- size
    def resizeEvent(self, e):
        cols, rows = max(2, self.width() // self.cw), max(2, self.height() // self.ch)
        if (cols, rows) != (self.screen.cols, self.screen.rows):
            self.screen.resize(cols, rows)
            self.session.resize(cols, rows)
        super().resizeEvent(e)

    def sizes(self):
        return max(2, self.width() // self.cw), max(2, self.height() // self.ch)

    def zoom(self, d):
        self.pt = min(32, max(7, self.pt + d))
        self._metrics()
        self.resizeEvent(None)
        self.update()

    # ---- painting
    def _lines(self):
        al = self.screen.all_lines()
        total = len(al)
        first = max(0, total - self.screen.rows - self.offset)
        return al, first

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        al, first = self._lines()
        sc = self.screen
        sel = self._sel_norm()
        for r in range(sc.rows):
            idx = first + r
            if idx >= len(al):
                break
            row = al[idx]
            y = r * self.ch
            x = 0
            run_start, run_key, run_txt = 0, None, []
            cells = []
            for c, (ch, fg, bg, at) in enumerate(row[:sc.cols]):
                inv = bool(at & REVERSE)
                f = to_color(fg, FG)
                b = to_color(bg, BG)
                if at & BOLD and 0 <= fg < 8:
                    f = palette(fg + 8)
                if inv:
                    f, b = (b if bg != -1 else BG), (f if fg != -1 else FG)
                if at & DIM:
                    f = QColor(f.red() // 2 + BG.red() // 2, f.green() // 2 + BG.green() // 2, f.blue() // 2 + BG.blue() // 2)
                cells.append((ch, f, b, at))
            c = 0
            n = len(cells)
            while c < n:
                ch, f, b, at = cells[c]
                e = c + 1
                while e < n and cells[e][1] == f and cells[e][2] == b and cells[e][3] == at and not (cells[e][0] and len(cells[e][0]) > 1):
                    e += 1
                if b != BG:
                    p.fillRect(c * self.cw, y, (e - c) * self.cw, self.ch, b)
                text = "".join(x_[0] if x_[0] != "" else "" for x_ in cells[c:e])
                if text.strip(" ") != "":
                    p.setPen(f)
                    p.setFont(self.font_b if at & BOLD else self.font_i if at & ITALIC else self.font_n)
                    wide = any(len(x_[0]) == 0 or ord(x_[0][0]) > 0x2E80 for x_ in cells[c:e])
                    if wide:
                        for k in range(c, e):
                            t = cells[k][0]
                            if t.strip():
                                p.drawText(k * self.cw, y + self.asc, t)
                    else:
                        p.drawText(c * self.cw, y + self.asc, text)
                    if at & UNDER:
                        p.drawLine(c * self.cw, y + self.ch - 2, e * self.cw, y + self.ch - 2)
                    if at & STRIKE:
                        p.drawLine(c * self.cw, y + self.ch // 2, e * self.cw, y + self.ch // 2)
                c = e
            if sel:
                (r1, c1), (r2, c2) = sel
                if r1 <= idx <= r2:
                    a = c1 if idx == r1 else 0
                    z = c2 + 1 if idx == r2 else sc.cols
                    p.fillRect(a * self.cw, y, max(0, z - a) * self.cw, self.ch, SEL)
        if self.offset == 0 and sc.cursor_visible:
            x, y = sc.cx * self.cw, sc.cy * self.ch
            if self.hasFocus():
                if self.blink:
                    p.fillRect(x, y, self.cw, self.ch, QColor(CURSOR.red(), CURSOR.green(), CURSOR.blue(), 230))
                    ch = sc.lines[sc.cy][sc.cx][0]
                    if ch.strip():
                        p.setPen(BG)
                        p.setFont(self.font_n)
                        p.drawText(x, y + self.asc, ch)
            else:
                p.setPen(CURSOR)
                p.drawRect(x, y, self.cw - 1, self.ch - 1)
        p.end()

    # ---- selection / clipboard
    def _cell(self, pos):
        al, first = self._lines()
        c = min(self.screen.cols - 1, max(0, pos.x() // self.cw))
        r = min(self.screen.rows - 1, max(0, pos.y() // self.ch))
        return first + r, c

    def _sel_norm(self):
        if not self.sel:
            return None
        a, b = self.sel
        return (a, b) if a <= b else (b, a)

    def selected_text(self):
        s = self._sel_norm()
        if not s:
            return ""
        al = self.screen.all_lines()
        (r1, c1), (r2, c2) = s
        out = []
        for r in range(r1, min(r2, len(al) - 1) + 1):
            row = al[r]
            a = c1 if r == r1 else 0
            z = c2 + 1 if r == r2 else len(row)
            out.append("".join(x[0] for x in row[a:z]).rstrip())
        return "\n".join(out)

    def copy(self):
        t = self.selected_text()
        if t:
            QGuiApplication.clipboard().setText(t)

    def paste(self, text=None):
        t = QGuiApplication.clipboard().text() if text is None else text
        if not t:
            return
        t = t.replace("\r\n", "\n").replace("\n", "\r")
        if self.screen.bracketed:
            t = "\x1b[200~" + t + "\x1b[201~"
        self.session.send(t.encode("utf-8"))

    # ---- mouse reporting for programs that ask for it (vim, htop, mc, tmux ...). Hold Shift to select text instead.
    def _reporting(self, e):
        return bool(self.screen.mouse) and not (e.modifiers() & Qt.ShiftModifier) and self.offset == 0

    def _report(self, e, btn, press=True, motion=False):
        pt = e.position().toPoint()
        c = min(self.screen.cols - 1, max(0, pt.x() // self.cw)) + 1
        r = min(self.screen.rows - 1, max(0, pt.y() // self.ch)) + 1
        m = e.modifiers()
        code = btn + (4 if m & Qt.ShiftModifier else 0) + (8 if m & Qt.AltModifier else 0) + (16 if m & Qt.ControlModifier else 0)
        if motion:
            code += 32
        if self.screen.mouse_sgr:
            data = f"\x1b[<{code};{c};{r}{'M' if press else 'm'}"
        else:
            if not press and btn < 64:
                code = 3 + (code & ~3)
            data = "\x1b[M" + chr(32 + code) + chr(min(255, 32 + c)) + chr(min(255, 32 + r))
        self.session.send(data.encode("latin-1", "replace"))

    @staticmethod
    def _btn(e):
        return {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(e.button())

    def mousePressEvent(self, e):
        self.setFocus()
        if self._reporting(e) and self._btn(e) is not None:
            self._down = self._btn(e)
            self._report(e, self._down)
            return
        if e.button() == Qt.LeftButton:
            self.anchor = self._cell(e.position().toPoint())
            self.sel = None
            self.update()
        elif e.button() == Qt.MiddleButton:
            cb = QGuiApplication.clipboard()
            self.paste(cb.text(QClipboard.Selection) or cb.text())
        elif e.button() == Qt.RightButton:
            if self.sel:
                self.copy()
                self.sel = None
                self.update()
            else:
                self.paste()

    def mouseMoveEvent(self, e):
        if self._reporting(e):
            if self.screen.mouse == 1003 or (self.screen.mouse == 1002 and e.buttons()):
                b = getattr(self, "_down", 0) if e.buttons() else 3
                self._report(e, b, True, True)
            return
        if e.buttons() & Qt.LeftButton and self.anchor:
            self.sel = (self.anchor, self._cell(e.position().toPoint()))
            self.update()

    def mouseReleaseEvent(self, e):
        if self._reporting(e) and self._btn(e) is not None:
            self._report(e, self._btn(e), False)
            return
        if e.button() == Qt.LeftButton and self.sel and self.sel[0] == self.sel[1]:
            self.sel = None
            self.update()

    def mouseDoubleClickEvent(self, e):
        if self._reporting(e) and self._btn(e) is not None:
            self._report(e, self._btn(e))
            return
        r, c = self._cell(e.position().toPoint())
        row = self.screen.all_lines()[r]
        a = z = c
        while a > 0 and row[a - 1][0].strip():
            a -= 1
        while z < len(row) - 1 and row[z + 1][0].strip():
            z += 1
        self.sel = ((r, a), (r, z))
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & (Qt.ControlModifier if not MAC else Qt.MetaModifier):
            self.zoom(1 if e.angleDelta().y() > 0 else -1)
            return
        n = 3 if e.angleDelta().y() > 0 else -3
        if self._reporting(e):
            self._report(e, 64 if n > 0 else 65)
            return
        if self.screen.alt is not None:       # vim / less: wheel = arrow keys
            self.session.send((self.screen.key_bytes_cursor("A" if n > 0 else "B")) * 3)
            return
        self.offset = max(0, min(len(self.screen.scroll), self.offset + n))
        self.update()

    # ---- keyboard
    def focusInEvent(self, e):
        if self.screen.focus_rep:
            self.session.send(b"\x1b[I")
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        if self.screen.focus_rep:
            self.session.send(b"\x1b[O")
        super().focusOutEvent(e)

    def focusNextPrevChild(self, nxt):
        return False

    def inputMethodEvent(self, e):
        if e.commitString():
            self.session.send(e.commitString().encode("utf-8"))

    def keyPressEvent(self, e):
        k, mods, text = e.key(), e.modifiers(), e.text()
        ctrl = Qt.MetaModifier if MAC else Qt.ControlModifier          # real Ctrl key
        if k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr, Qt.Key_CapsLock):
            return
        sq = key_text(e)
        if sq in (SHORTCUTS["copy"], "Ctrl+Shift+C", "Ctrl+Ins"):
            if self.sel:
                self.copy()
                return
            if not (sq == "Ctrl+C" and not MAC):      # nothing selected: Ctrl+C is the normal "interrupt" key
                return
        elif sq in (SHORTCUTS["paste"], "Ctrl+Shift+V", "Shift+Ins"):
            self.paste()
            return
        elif sq in (SHORTCUTS["zoom_in"], "Ctrl++", "Ctrl+=", "Ctrl+Shift++"):
            self.zoom(1)
            return
        elif sq in (SHORTCUTS["zoom_out"], "Ctrl+-"):
            self.zoom(-1)
            return
        self.offset = 0
        data = None
        arrows = {Qt.Key_Up: "A", Qt.Key_Down: "B", Qt.Key_Right: "C", Qt.Key_Left: "D", Qt.Key_Home: "H", Qt.Key_End: "F"}
        if k in arrows:
            if mods & (ctrl | Qt.ShiftModifier | Qt.AltModifier):
                m = 1 + (1 if mods & Qt.ShiftModifier else 0) + (2 if mods & Qt.AltModifier else 0) + (4 if mods & ctrl else 0)
                data = f"\x1b[1;{m}{arrows[k]}".encode()
            else:
                data = self.screen.key_bytes_cursor(arrows[k])
        elif k in FKEYS:
            data = ("\x1b" + FKEYS[k]).encode()
        elif k in KEYS:
            data = KEYS[k]
            if k == Qt.Key_Tab and mods & Qt.ShiftModifier:
                data = b"\x1b[Z"
        elif mods & ctrl and Qt.Key_A <= k <= Qt.Key_Z:
            data = bytes([k - Qt.Key_A + 1])
        elif mods & ctrl and k in (Qt.Key_BracketLeft, Qt.Key_2, Qt.Key_Space):
            data = b"\x1b" if k == Qt.Key_BracketLeft else b"\x00"
        elif mods & ctrl and k == Qt.Key_Backslash:
            data = b"\x1c"
        elif mods & ctrl and k == Qt.Key_BracketRight:
            data = b"\x1d"
        elif text:
            data = text.encode("utf-8")
            if mods & Qt.AltModifier and not MAC:
                data = b"\x1b" + data
        if data:
            self.session.send(data)
            e.accept()
