"""Settings window: terminal colour profile, font and size, with a live preview."""
import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import termtheme as T

ANSI_NAMES = ["Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White",
              "Bright black", "Bright red", "Bright green", "Bright yellow", "Bright blue", "Bright magenta", "Bright cyan", "Bright white"]


class Preview(QWidget):
    """a small terminal drawn with the chosen colours and font"""

    def __init__(self):
        super().__init__()
        self.p = copy.deepcopy(T.PROFILES[T.DEFAULT_PROFILE])
        self.family, self.pt = "monospace", 14
        self.setMinimumHeight(190)

    def set(self, p, family, pt):
        self.p, self.family, self.pt = p, family, pt
        self.update()

    def sizeHint(self):
        return QSize(520, 200)

    def paintEvent(self, _):
        p = self.p
        q = QPainter(self)
        q.fillRect(self.rect(), QColor(p["bg"]))
        f = QFont(self.family, self.pt)
        f.setStyleHint(QFont.Monospace)
        f.setKerning(False)
        fm = QFontMetricsF(f)
        adv = fm.horizontalAdvance("M")
        cw = max(1, round(adv))
        f.setLetterSpacing(QFont.AbsoluteSpacing, cw - adv)
        fm = QFontMetricsF(f)
        ch, asc = max(1, round(fm.height())), round(fm.ascent())
        q.setFont(f)
        ansi = [QColor(c) for c in p["ansi"]]
        fg = QColor(p["fg"])
        y = 6
        x0 = 10

        def put(x, text, col):
            q.setPen(col)
            q.drawText(x0 + x * cw, y + asc, text)
            return x + len(text)
        x = put(0, "user@server", ansi[10])
        x = put(x, ":", fg)
        x = put(x, "~/projects", ansi[12])
        x = put(x, "$ ", fg)
        put(x, "ls -l", fg)
        y += ch
        x = put(0, "drwxr-xr-x  ", fg)
        x = put(x, "backup", ansi[12])
        y += ch
        x = put(0, "-rwxr-xr-x  ", fg)
        x = put(x, "deploy.sh", ansi[10])
        y += ch
        x = put(0, "-rw-r--r--  ", fg)
        x = put(x, "notes.txt", fg)
        y += ch
        x = put(0, "error: ", ansi[9])
        x = put(x, "permission denied  ", ansi[1])
        x = put(x, "warning", ansi[11])
        y += ch
        for i in range(16):
            q.fillRect(x0 + i * 2 * cw, y + 2, 2 * cw - 2, ch - 4, ansi[i])
        y += ch
        x = put(0, "user@server", ansi[10])
        x = put(x, ":", fg)
        x = put(x, "~", ansi[12])
        x = put(x, "$ ", fg)
        cur = QColor(p["cursor"])
        q.fillRect(x0 + x * cw, y, cw, ch, cur)
        q.end()


class Swatch(QPushButton):
    def __init__(self, text, color, on_change):
        super().__init__()
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.label, self.on_change = text, on_change
        self.set_color(color)
        self.setToolTip(text + " - click to change")
        self.clicked.connect(self.pick)

    def set_color(self, c):
        self.color = QColor(c).name()
        lum = QColor(self.color).lightness()
        self.setStyleSheet(f"QPushButton{{background:{self.color}; border:1px solid #8A7FA6; border-radius:5px; "
                           f"color:{'#000' if lum > 128 else '#fff'}; padding:2px 6px; font-size:11px;}}")

    def pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, self.label)
        if c.isValid():
            self.set_color(c)
            self.on_change(self)


class TermSettingsDialog(QDialog):
    """returns through .result_choice = (profile name, font family, size) after OK; the profile is saved first"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terminal settings")
        self.setMinimumSize(900, 640)
        self.custom = T.custom_profiles()
        self.name = T.profile_name()
        self.work = copy.deepcopy(T.all_profiles()[self.name])
        self.loading = False

        top = QHBoxLayout(self)
        # ---- left: profile list
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Colour profile</b>"))
        self.lst = QListWidget()
        self.lst.setMinimumWidth(210)
        self.lst.currentItemChanged.connect(self.chosen)
        left.addWidget(self.lst, 1)
        row = QHBoxLayout()
        self.b_new, self.b_ren, self.b_del = QPushButton("Save as new ..."), QPushButton("Rename"), QPushButton("Delete")
        self.b_new.clicked.connect(self.save_new)
        self.b_ren.clicked.connect(self.rename)
        self.b_del.clicked.connect(self.delete)
        for b in (self.b_new, self.b_ren, self.b_del):
            row.addWidget(b)
        left.addLayout(row)
        note = QLabel("Built-in profiles stay as they are. Change a colour and a copy of your own is made for you.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5B5278; font-size:11px;")
        left.addWidget(note)
        top.addLayout(left, 0)

        # ---- right
        right = QVBoxLayout()
        self.prev = Preview()
        right.addWidget(self.prev)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.base = {}
        for i, (key, label) in enumerate((("bg", "Background"), ("fg", "Text"), ("cursor", "Cursor"), ("sel", "Selection"))):
            s = Swatch(label, "#000000", self.colour_changed)
            s.key = key
            self.base[key] = s
            g.addWidget(QLabel(label), 0, i)
            g.addWidget(s, 1, i)
        right.addLayout(g)
        right.addWidget(QLabel("Terminal colours (used by ls, vim, htop ...)"))
        ga = QGridLayout()
        ga.setHorizontalSpacing(6)
        ga.setVerticalSpacing(6)
        self.ansi = []
        for i in range(16):
            s = Swatch(ANSI_NAMES[i], "#000000", self.colour_changed)
            s.key = i
            s.setText(str(i))
            self.ansi.append(s)
            ga.addWidget(s, i // 8, i % 8)
        right.addLayout(ga)

        form = QFormLayout()
        self.font = QComboBox()
        self.font.setMinimumWidth(260)
        self.font.currentIndexChanged.connect(self.font_changed)
        form.addRow("Font", self.font)
        self.allfonts = QCheckBox("Show all fonts of this computer (not only fixed-width ones)")
        self.allfonts.toggled.connect(lambda _on: self.fill_fonts(self.font.currentData()))
        form.addRow("", self.allfonts)
        self.pt = QSpinBox()
        self.pt.setRange(7, 32)
        self.pt.setSuffix(" pt")
        self.pt.valueChanged.connect(self.font_changed)
        form.addRow("Font size", self.pt)
        right.addLayout(form)
        hint = QLabel("Fonts marked * come with the program (Google Fonts, open source). The others are installed on this computer.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5B5278; font-size:11px;")
        right.addWidget(hint)
        right.addStretch(1)
        btns = QHBoxLayout()
        reset = QPushButton("Reset to default")
        reset.clicked.connect(self.reset)
        btns.addWidget(reset)
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept_and_save)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        right.addLayout(btns)
        top.addLayout(right, 1)

        self.fill_list()
        self.fill_fonts(T.family())
        self.loading = True
        self.pt.setValue(T.size())
        self.loading = False
        self.show_profile()

    # ---- profile list
    def fill_list(self):
        self.loading = True
        self.lst.clear()
        sep = QListWidgetItem("Built-in")
        sep.setFlags(Qt.NoItemFlags)
        sep.setForeground(QColor("#8A2150"))
        self.lst.addItem(sep)
        for n in T.PROFILES:
            it = QListWidgetItem(n)
            it.setData(Qt.UserRole, n)
            self.lst.addItem(it)
        if self.custom:
            sep2 = QListWidgetItem("My profiles")
            sep2.setFlags(Qt.NoItemFlags)
            sep2.setForeground(QColor("#8A2150"))
            self.lst.addItem(sep2)
            for n in self.custom:
                it = QListWidgetItem(n)
                it.setData(Qt.UserRole, n)
                self.lst.addItem(it)
        for i in range(self.lst.count()):
            if self.lst.item(i).data(Qt.UserRole) == self.name:
                self.lst.setCurrentRow(i)
        self.loading = False
        self.update_buttons()

    def update_buttons(self):
        mine = self.name in self.custom
        self.b_ren.setEnabled(mine)
        self.b_del.setEnabled(mine)

    def chosen(self, cur, _prev):
        if self.loading or cur is None or cur.data(Qt.UserRole) is None:
            return
        self.name = cur.data(Qt.UserRole)
        self.work = copy.deepcopy(T.all_profiles()[self.name])
        self.update_buttons()
        self.show_profile()

    def show_profile(self):
        self.loading = True
        p = self.work
        for k, s in self.base.items():
            s.set_color(p[k] if p.get(k) else p["fg"])
        for i, s in enumerate(self.ansi):
            s.set_color(p["ansi"][i])
        self.loading = False
        self.refresh()

    def colour_changed(self, sw):
        if self.loading:
            return
        if sw.key in ("bg", "fg", "cursor", "sel"):
            self.work[sw.key] = sw.color
        else:
            self.work["ansi"][sw.key] = sw.color
        if self.name not in self.custom:               # a built-in was changed: keep it as it is, work on a copy
            base = self.name
            n = f"{base} (mine)"
            k = 2
            while n in T.PROFILES or n in self.custom:
                n = f"{base} (mine {k})"
                k += 1
            self.name = n
            self.custom[n] = self.work
            self.fill_list()
        else:
            self.custom[self.name] = self.work
        self.refresh()

    def new_name(self, title, default=""):
        n, ok = QInputDialog.getText(self, title, "Name of the profile:", text=default)
        n = (n or "").strip()
        if not ok or not n:
            return ""
        if n in T.PROFILES or (n in self.custom and n != default):
            QMessageBox.warning(self, "Terminal settings", "A profile with this name exists already.")
            return ""
        return n

    def save_new(self):
        n = self.new_name("Save as new profile", f"{self.name} copy")
        if not n:
            return
        self.custom[n] = copy.deepcopy(self.work)
        self.name = n
        self.work = self.custom[n]
        self.fill_list()
        self.show_profile()

    def rename(self):
        if self.name not in self.custom:
            return
        n = self.new_name("Rename profile", self.name)
        if not n or n == self.name:
            return
        self.custom = {(n if k == self.name else k): v for k, v in self.custom.items()}
        self.name = n
        self.fill_list()

    def delete(self):
        if self.name not in self.custom:
            return
        if QMessageBox.question(self, "Terminal settings", f"Delete the profile '{self.name}'?") != QMessageBox.Yes:
            return
        del self.custom[self.name]
        self.name = T.DEFAULT_PROFILE
        self.work = copy.deepcopy(T.PROFILES[self.name])
        self.fill_list()
        self.show_profile()

    # ---- fonts
    def fill_fonts(self, keep=None):
        self.loading = True
        self.font.clear()
        bundled = T.bundled_families()
        for f in bundled:
            self.font.addItem(f + " *", f)
        if bundled:
            self.font.insertSeparator(self.font.count())
        for f in T.installed_families(self.allfonts.isChecked()):
            self.font.addItem(f, f)
        i = self.font.findData(keep) if keep else -1
        self.font.setCurrentIndex(max(0, i))
        self.loading = False
        self.refresh()

    def font_changed(self, *_):
        if not self.loading:
            self.refresh()

    def refresh(self):
        self.prev.set(self.work, self.font.currentData() or "monospace", self.pt.value())

    def reset(self):
        self.name = T.DEFAULT_PROFILE
        self.work = copy.deepcopy(T.PROFILES[self.name])
        self.fill_list()
        self.show_profile()
        fam = next((f for f in ("JetBrains Mono", "DejaVu Sans Mono", "Menlo", "Consolas") if self.font.findData(f) >= 0), None)
        if fam:
            self.font.setCurrentIndex(self.font.findData(fam))
        self.pt.setValue(T.DEFAULT_SIZE)

    def accept_and_save(self):
        T.save_custom(self.custom)
        fam = self.font.currentData() or "monospace"
        T.save_choice(self.name, fam, self.pt.value())
        T.apply_profile(T.all_profiles()[self.name])
        self.result_choice = (self.name, fam, self.pt.value())
        self.accept()
