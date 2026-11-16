"""A compact VT100/xterm terminal emulator (no GUI): feed bytes in, read a grid of cells out.

Supports colours (16 / 256 / true colour), bold/underline/inverse, cursor movement, erase, insert/delete lines and chars,
scroll regions, alternate screen (vim, nano, htop, less), line-drawing characters (mc, tmux), bracketed paste, application
cursor keys, scrollback.
"""
import codecs
import unicodedata

DEFAULT = (-1, -1, 0)    # fg, bg, attrs ; fg/bg: -1 default, 0..255 palette, or 0x1000000|rgb
BOLD, UNDER, REVERSE, ITALIC, DIM, BLINK, STRIKE = 1, 2, 4, 8, 16, 32, 64
RGB = 0x1000000
LINE_DRAW = {"j": "┘", "k": "┐", "l": "┌", "m": "└", "n": "┼", "q": "─", "t": "├", "u": "┤", "v": "┴", "w": "┬", "x": "│",
             "a": "▒", "`": "◆", "f": "°", "g": "±", "o": "⎺", "p": "⎻", "r": "⎼", "s": "⎽", "~": "·", "0": "█", "h": "▒"}


def width(ch):
    if ch < " ":
        return 0
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, cols=80, rows=24, scrollback=5000):
        self.cols, self.rows, self.max_scroll = cols, rows, scrollback
        self.scroll = []
        self.alt = None
        self.reset()

    def reset(self):
        self.main_lines = [self._blank_line() for _ in range(self.rows)]
        self.lines = self.main_lines
        self.alt = None
        self.cx = self.cy = 0
        self.pen = DEFAULT
        self.top, self.bottom = 0, self.rows - 1
        self.wrap_pending = False
        self.autowrap = True
        self.origin = False
        self.insert_mode = False
        self.cursor_visible = True
        self.app_cursor = False
        self.bracketed = False
        self.mouse = 0          # 0 off, 1000 clicks, 1002 drag, 1003 all motion
        self.mouse_sgr = False
        self.focus_rep = False
        self.title = ""
        self.saved = (0, 0, DEFAULT)
        self.g0_draw = False
        self.tabs = set(range(0, self.cols, 8))
        self.dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.state = "n"
        self.buf = ""
        self.responses = []      # bytes to send back to the server (cursor position reports ...)
        self.dirty = True

    # ---- helpers
    def _blank(self, pen=None):
        return (" ", DEFAULT[0], (pen or self.pen)[1], 0)

    def _blank_line(self):
        return [(" ", -1, -1, 0)] * self.cols

    def resize(self, cols, rows):
        cols, rows = max(2, cols), max(2, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        def fit(ls, keep_cursor):
            ls = [(l + [(" ", -1, -1, 0)] * (cols - len(l)))[:cols] for l in ls]
            while len(ls) > rows:
                if len(ls) - 1 > self.cy and self._line_empty(ls[-1]):
                    ls.pop()
                elif keep_cursor:
                    self.scroll.append(ls.pop(0))
                    self.cy = max(0, self.cy - 1)
                else:
                    ls.pop()
            while len(ls) < rows:
                ls.append([(" ", -1, -1, 0)] * cols)
            return ls
        self.main_lines = fit(self.main_lines, True)
        if self.alt is not None:
            self.alt = fit(self.alt, False)
        self.cols, self.rows = cols, rows
        self.lines = self.alt if self.alt is not None else self.main_lines
        self.top, self.bottom = 0, rows - 1
        self.cx, self.cy = min(self.cx, cols - 1), min(self.cy, rows - 1)
        self.tabs = set(range(0, cols, 8))
        self.wrap_pending = False
        self.dirty = True

    @staticmethod
    def _line_empty(l):
        return all(c[0] == " " for c in l)

    def _scroll_up(self, n=1):
        for _ in range(n):
            line = self.lines.pop(self.top)
            if self.alt is None and self.top == 0:
                self.scroll.append(line)
                if len(self.scroll) > self.max_scroll:
                    del self.scroll[:len(self.scroll) - self.max_scroll]
            self.lines.insert(self.bottom, self._blank_line_pen())

    def _scroll_down(self, n=1):
        for _ in range(n):
            self.lines.pop(self.bottom)
            self.lines.insert(self.top, self._blank_line_pen())

    def _blank_line_pen(self):
        return [(" ", -1, self.pen[1], 0)] * self.cols

    def _lf(self):
        if self.cy == self.bottom:
            self._scroll_up()
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _put(self, ch):
        w = width(ch)
        if w == 0:
            if self.cx > 0 or self.wrap_pending:
                x = self.cx - (0 if self.wrap_pending else 1)
                c = self.lines[self.cy][x]
                self.lines[self.cy][x] = (c[0] + ch, c[1], c[2], c[3])
            return
        if self.wrap_pending and self.autowrap:
            self.cx = 0
            self._lf()
        self.wrap_pending = False
        if self.cx + w > self.cols:
            if self.autowrap:
                self.cx = 0
                self._lf()
            else:
                self.cx = self.cols - w
        row = self.lines[self.cy]
        if self.insert_mode:
            for _ in range(w):
                row.insert(self.cx, self._blank())
                row.pop()
        row[self.cx] = (ch, self.pen[0], self.pen[1], self.pen[2])
        if w == 2:
            row[self.cx + 1] = ("", self.pen[0], self.pen[1], self.pen[2])
        self.cx += w
        if self.cx >= self.cols:
            self.cx = self.cols - 1
            self.wrap_pending = True

    # ---- input
    def feed(self, data):
        text = self.dec.decode(data)
        for ch in text:
            self._char(ch)
        self.dirty = True

    def _char(self, ch):
        st = self.state
        o = ord(ch)
        if st == "n":
            if ch == "\x1b":
                self.state, self.buf = "e", ""
            elif o < 32 or o == 127:
                self._ctrl(ch)
            else:
                if self.g0_draw and ch in LINE_DRAW:
                    ch = LINE_DRAW[ch]
                self._put(ch)
        elif st == "e":
            if ch == "[":
                self.state, self.buf = "c", ""
            elif ch == "]":
                self.state, self.buf = "o", ""
            elif ch in "()*+":
                self.state, self.buf = "cs", ch
            elif ch in "P^_X":
                self.state, self.buf = "s", ""     # string sequences: swallow until ST
            elif ch == "#":
                self.state = "h"
            else:
                self.state = "n"
                self._esc(ch)
        elif st == "c":
            if "0" <= ch <= "?":
                self.buf += ch
            elif " " <= ch <= "/":
                self.buf += ch
            elif ch == "\x1b":
                self.state = "e"
            elif o < 32:
                self._ctrl(ch)
            else:
                self.state = "n"
                self._csi(ch, self.buf)
        elif st == "o":
            if ch == "\x07":
                self._osc(self.buf)
                self.state = "n"
            elif ch == "\x1b":
                self._osc(self.buf)
                self.state = "st"
            else:
                self.buf += ch
        elif st == "st":
            self.state = "n"
        elif st == "s":
            if ch == "\x07":
                self.state = "n"
            elif ch == "\x1b":
                self.state = "st"
        elif st == "cs":
            if self.buf == "(":
                self.g0_draw = ch == "0"
            self.state = "n"
        elif st == "h":
            if ch == "8":      # DECALN
                self.lines[:] = [[("E", -1, -1, 0)] * self.cols for _ in range(self.rows)]
            self.state = "n"

    def _ctrl(self, ch):
        if ch == "\n" or ch == "\x0b" or ch == "\x0c":
            self._lf()
            self.wrap_pending = False
        elif ch == "\r":
            self.cx = 0
            self.wrap_pending = False
        elif ch == "\b" or ch == "\x7f" and False:
            self.cx = max(0, self.cx - 1)
            self.wrap_pending = False
        elif ch == "\t":
            nxt = [t for t in sorted(self.tabs) if t > self.cx]
            self.cx = nxt[0] if nxt else self.cols - 1
        elif ch == "\x0e":
            self.g0_draw = True
        elif ch == "\x0f":
            self.g0_draw = False
        elif ch == "\x07":
            self.bell = True

    def _esc(self, ch):
        if ch == "7":
            self.saved = (self.cx, self.cy, self.pen)
        elif ch == "8":
            self.cx, self.cy, self.pen = self.saved
            self.wrap_pending = False
        elif ch == "D":
            self._lf()
        elif ch == "E":
            self.cx = 0
            self._lf()
        elif ch == "M":
            if self.cy == self.top:
                self._scroll_down()
            elif self.cy > 0:
                self.cy -= 1
        elif ch == "H":
            self.tabs.add(self.cx)
        elif ch == "c":
            self.reset()
        elif ch == "=" or ch == ">":
            pass

    def _osc(self, s):
        if s[:2] in ("0;", "2;"):
            self.title = s[2:]

    # ---- CSI
    def _params(self, s, default=0):
        s = s.lstrip("?>!= ").rstrip(" !\"'$")
        out = []
        for p in s.split(";"):
            try:
                out.append(int(p.split(":")[0]) if p else default)
            except ValueError:
                out.append(default)
        return out or [default]

    def _csi(self, ch, buf):
        private = buf[:1] in "?>!=" and buf != ""
        p = self._params(buf)
        n = p[0] if p[0] > 0 else 1
        L = self.lines
        if ch in "Hf":
            r = (p[0] or 1) - 1
            c = ((p[1] if len(p) > 1 else 0) or 1) - 1
            self.cy = min(self.rows - 1, max(0, r + (self.top if self.origin else 0)))
            self.cx = min(self.cols - 1, max(0, c))
            self.wrap_pending = False
        elif ch == "A":
            self.cy = max(self.top if self.cy >= self.top else 0, self.cy - n)
            self.wrap_pending = False
        elif ch in "Be":
            self.cy = min(self.bottom if self.cy <= self.bottom else self.rows - 1, self.cy + n)
            self.wrap_pending = False
        elif ch in "Ca":
            self.cx = min(self.cols - 1, self.cx + n)
            self.wrap_pending = False
        elif ch == "D":
            self.cx = max(0, self.cx - n)
            self.wrap_pending = False
        elif ch == "E":
            self.cx, self.cy = 0, min(self.rows - 1, self.cy + n)
        elif ch == "F":
            self.cx, self.cy = 0, max(0, self.cy - n)
        elif ch in "G`":
            self.cx = min(self.cols - 1, n - 1)
            self.wrap_pending = False
        elif ch == "d":
            self.cy = min(self.rows - 1, n - 1)
        elif ch == "J":
            m = p[0]
            if m == 0:
                self._erase_line(self.cy, self.cx, self.cols)
                for y in range(self.cy + 1, self.rows):
                    self._erase_line(y, 0, self.cols)
            elif m == 1:
                for y in range(0, self.cy):
                    self._erase_line(y, 0, self.cols)
                self._erase_line(self.cy, 0, self.cx + 1)
            else:
                for y in range(self.rows):
                    self._erase_line(y, 0, self.cols)
                if m == 3:
                    self.scroll.clear()
        elif ch == "K":
            m = p[0]
            if m == 0:
                self._erase_line(self.cy, self.cx, self.cols)
            elif m == 1:
                self._erase_line(self.cy, 0, self.cx + 1)
            else:
                self._erase_line(self.cy, 0, self.cols)
        elif ch == "L":
            if self.top <= self.cy <= self.bottom:
                for _ in range(min(n, self.bottom - self.cy + 1)):
                    L.pop(self.bottom)
                    L.insert(self.cy, self._blank_line_pen())
        elif ch == "M":
            if self.top <= self.cy <= self.bottom:
                for _ in range(min(n, self.bottom - self.cy + 1)):
                    L.pop(self.cy)
                    L.insert(self.bottom, self._blank_line_pen())
        elif ch == "@":
            row = L[self.cy]
            for _ in range(n):
                row.insert(self.cx, self._blank())
                row.pop()
        elif ch == "P":
            row = L[self.cy]
            for _ in range(min(n, self.cols - self.cx)):
                row.pop(self.cx)
                row.append(self._blank())
        elif ch == "X":
            self._erase_line(self.cy, self.cx, min(self.cols, self.cx + n))
        elif ch == "S":
            self._scroll_up(n)
        elif ch == "T":
            self._scroll_down(n)
        elif ch == "r":
            t = (p[0] or 1) - 1
            b = ((p[1] if len(p) > 1 else 0) or self.rows) - 1
            if 0 <= t < b < self.rows:
                self.top, self.bottom = t, b
            self.cx, self.cy = 0, self.top if self.origin else 0
        elif ch == "m" and not private:
            self._sgr(p)
        elif ch in "hl":
            on = ch == "h"
            for m in p:
                self._mode(m, on, private)
        elif ch == "n" and not private:
            if p[0] == 5:
                self.responses.append(b"\x1b[0n")
            elif p[0] == 6:
                self.responses.append(f"\x1b[{self.cy + 1};{self.cx + 1}R".encode())
        elif ch == "c":
            self.responses.append(b"\x1b[?1;2c" if buf[:1] != ">" else b"\x1b[>0;95;0c")
        elif ch == "s" and not buf:
            self.saved = (self.cx, self.cy, self.pen)
        elif ch == "u" and not buf:
            self.cx, self.cy, self.pen = self.saved
        elif ch == "g":
            if p[0] == 3:
                self.tabs.clear()
            elif p[0] == 0:
                self.tabs.discard(self.cx)

    def _erase_line(self, y, a, b):
        blank = (" ", -1, self.pen[1], 0)
        row = self.lines[y]
        for x in range(a, min(b, self.cols)):
            row[x] = blank

    def _mode(self, m, on, private):
        if not private:
            if m == 4:
                self.insert_mode = on
            return
        if m == 1:
            self.app_cursor = on
        elif m == 6:
            self.origin = on
            self.cx, self.cy = 0, self.top if on else 0
        elif m == 7:
            self.autowrap = on
        elif m == 25:
            self.cursor_visible = on
        elif m == 2004:
            self.bracketed = on
        elif m in (9, 1000, 1002, 1003):
            self.mouse = (1000 if m == 9 else m) if on else 0
        elif m == 1006:
            self.mouse_sgr = on
        elif m == 1004:
            self.focus_rep = on
        elif m in (47, 1047, 1049):
            if on and self.alt is None:
                if m == 1049:
                    self.saved = (self.cx, self.cy, self.pen)
                self.alt = [self._blank_line() for _ in range(self.rows)]
                self.lines = self.alt
                self.top, self.bottom = 0, self.rows - 1
            elif not on and self.alt is not None:
                self.alt = None
                self.lines = self.main_lines
                self.top, self.bottom = 0, self.rows - 1
                if m == 1049:
                    self.cx, self.cy, self.pen = self.saved
        elif m == 1049 and not on:
            pass

    def _sgr(self, p):
        fg, bg, at = self.pen
        i = 0
        while i < len(p):
            c = p[i]
            if c == 0:
                fg, bg, at = -1, -1, 0
            elif c == 1:
                at |= BOLD
            elif c == 2:
                at |= DIM
            elif c == 3:
                at |= ITALIC
            elif c == 4:
                at |= UNDER
            elif c == 5 or c == 6:
                at |= BLINK
            elif c == 7:
                at |= REVERSE
            elif c == 9:
                at |= STRIKE
            elif c == 22:
                at &= ~(BOLD | DIM)
            elif c == 23:
                at &= ~ITALIC
            elif c == 24:
                at &= ~UNDER
            elif c == 27:
                at &= ~REVERSE
            elif c == 29:
                at &= ~STRIKE
            elif 30 <= c <= 37:
                fg = c - 30
            elif c == 39:
                fg = -1
            elif 40 <= c <= 47:
                bg = c - 40
            elif c == 49:
                bg = -1
            elif 90 <= c <= 97:
                fg = c - 90 + 8
            elif 100 <= c <= 107:
                bg = c - 100 + 8
            elif c in (38, 48):
                col = None
                if i + 1 < len(p) and p[i + 1] == 5 and i + 2 < len(p):
                    col = p[i + 2] & 255
                    i += 2
                elif i + 1 < len(p) and p[i + 1] == 2 and i + 4 < len(p):
                    col = RGB | ((p[i + 2] & 255) << 16) | ((p[i + 3] & 255) << 8) | (p[i + 4] & 255)
                    i += 4
                if col is not None:
                    if c == 38:
                        fg = col
                    else:
                        bg = col
            i += 1
        self.pen = (fg, bg, at)

    # ---- output
    def all_lines(self):
        """scrollback + screen (scrollback hidden while the alternate screen is active)"""
        return (self.scroll if self.alt is None else []) + self.lines

    def key_bytes_cursor(self, key):
        pre = "\x1bO" if self.app_cursor else "\x1b["
        return (pre + key).encode()
