#!/usr/bin/env python3
"""Digonto SSH - SSH terminal + SFTP files with your hosts kept in an encrypted vault on your own sync folder."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PySide6.QtCore import QSettings, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QKeySequenceEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import digonto_ui as ui
from sshkit import __version__
from sshkit.conn import Session
from sshkit.files import FilesTab
from sshkit import termtheme
from sshkit.termview import DEFAULT_SHORTCUTS, SHORTCUTS, TermView
from sshkit.vault import Vault, VaultError, WrongPassword, new_host

APP_NAME = "Digonto SSH"


def settings():
    return QSettings("Digonto", "SSH")


def link_path():
    from PySide6.QtCore import QStandardPaths
    d = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation) or os.path.expanduser("~/.digonto-ssh")
    return os.path.join(d, "server-link.json")


def load_link():
    import json
    try:
        with open(link_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_link(data):
    """the server login you chose to keep; only this user can read the file (permissions 600)"""
    import json
    p = link_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def vault_login_path():
    return os.path.join(os.path.dirname(link_path()), "vault-login.json")


def load_vault_login():
    """e-mail + vault password kept on this computer (only if you ticked the box)"""
    import json
    try:
        with open(vault_login_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_vault_login(data):
    import json
    p = vault_login_path()
    if not data:
        try:
            os.remove(p)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, p)


class Link:
    """the one live connection to your own server: stays open until you press Disconnect"""
    store = None
    cfg = None

    @classmethod
    def connected(cls):
        if cls.store is None or cls.store.sftp is None:
            return False
        try:
            cls.store.sftp.stat(".")
            return True
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def open(cls, cfg, email, trust):
        from sshkit.vault import SftpStore
        key_text = open(cfg["key_file"], encoding="utf-8").read() if cfg.get("key_file") else ""
        st = SftpStore(cfg["host"], cfg["port"], cfg["user"], cfg["srv_pw"], key_text, cfg.get("key_pass", ""), cfg["rdir"], email, trust)
        st._connect()               # logs in and creates the folder on the server
        cls.close()
        cls.store, cls.cfg = st, dict(cfg)
        return st

    @classmethod
    def close(cls):
        if cls.store is not None:
            cls.store.close()
        cls.store = None


class CheckThread(QThread):
    """tests the saved server login in the background (never asks questions)"""
    done = Signal(bool, str)

    def __init__(self, cfg, email, known):
        super().__init__()
        self.cfg, self.email, self.known = cfg, email, known

    def run(self):
        try:
            Link.open(self.cfg, self.email, lambda hp, fp: (self.known(hp) == fp, False))
            self.done.emit(True, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))


def syncdrive_guess():
    h = os.path.expanduser("~")
    for n in ("SyncDrive", "Sync Drive", "syncdrive", "Syncdrive", "Documents/SyncDrive", "Sync"):
        p = os.path.join(h, n)
        if os.path.isdir(p):
            return p
    return ""


def make_store(cfg, email, ask_trust, asker=None):
    """cfg: dict from the login form -> LocalStore, or CachedStore(SftpStore) with a copy kept on this computer"""
    from PySide6.QtCore import QStandardPaths
    from sshkit.vault import CachedStore, LocalStore, SftpStore, vault_path
    if cfg["mode"] in ("folder", "syncdrive"):
        return cfg["folder"]
    if Link.store is not None:          # the live connection made with the Connect button
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~/.digonto-ssh")
        return CachedStore(Link.store, os.path.join(base, "vault-cache", os.path.basename(vault_path("", email))))
    key_text = ""
    if cfg["key_file"]:
        key_text = open(cfg["key_file"], encoding="utf-8").read()
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~/.digonto-ssh")
    cache = os.path.join(base, "vault-cache", os.path.basename(vault_path("", email)))
    sf = SftpStore(cfg["host"], cfg["port"], cfg["user"], cfg["srv_pw"], key_text, cfg["key_pass"], cfg["rdir"], email, ask_trust)
    return CachedStore(sf, cache)


class LoginDialog(QDialog):
    def __init__(self, parent=None, note="", auto=False):
        super().__init__(parent)
        self.auto = auto
        self.setWindowTitle("Digonto SSH - sign in")
        self.setMinimumWidth(580)
        self.vault = None
        self.check = None
        s = settings()
        link = load_link()
        v = QVBoxLayout(self)
        intro = QLabel("<h3>Digonto SSH</h3>Your servers are saved in one encrypted vault. Use the same e-mail, password and vault "
                       "location on every computer to see the same servers.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        if note:
            n = QLabel(note)
            n.setStyleSheet("color:#B00020")
            v.addWidget(n)
        f = QFormLayout()
        self.email = QLineEdit(s.value("email", ""))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        f.addRow("E-mail", self.email)
        f.addRow("Vault password", self.pw)
        saved = load_vault_login()
        self.keep_pw = QCheckBox("Remember my vault password on this computer (sign in automatically)")
        self.keep_pw.setChecked(bool(saved.get("password")))
        if saved.get("password"):
            self.pw.setText(saved["password"])
            if saved.get("email") and not self.email.text():
                self.email.setText(saved["email"])
        f.addRow(self.keep_pw)
        self.mode = QComboBox()
        self.mode.addItem("My VPN / own server (over SSH)", "server")
        self.mode.addItem("My SyncDrive folder", "syncdrive")
        self.mode.addItem("Another folder (Dropbox ...)", "folder")
        saved_mode = s.value("mode", "server")
        self.mode.setCurrentIndex({"server": 0, "syncdrive": 1, "folder": 2}.get(saved_mode, 0))
        f.addRow("Vault location", self.mode)
        v.addLayout(f)

        # ---- own server: connect once, stays connected until Disconnect
        self.srv_box = QWidget()
        sv = QVBoxLayout(self.srv_box)
        sv.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        sv.addWidget(self.status)
        self.srv_fields = QWidget()
        sf = QFormLayout(self.srv_fields)
        sf.setContentsMargins(0, 0, 0, 0)
        self.host = QLineEdit(s.value("host", ""))
        self.host.setPlaceholderText("VPN address or IP of your server, e.g. 10.8.0.1")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(s.value("port", 22)))
        self.user = QLineEdit(s.value("user", ""))
        self.srv_pw = QLineEdit(link.get("password", ""))
        self.srv_pw.setEchoMode(QLineEdit.Password)
        self.srv_pw.setPlaceholderText("server password (empty = use the key file)")
        self.key_file = QLineEdit(s.value("key_file", ""))
        self.key_file.setPlaceholderText("optional: private key file instead of a password")
        self.rdir = QLineEdit(s.value("rdir", "digonto-sync"))
        self.rdir.setPlaceholderText("folder that is created on the server")
        self.remember = QCheckBox("Stay connected: remember this server on this computer (until I press Disconnect)")
        self.remember.setChecked(True)
        for lab, w in (("Server (IP / name)", self.host), ("Port", self.port), ("User", self.user), ("Password", self.srv_pw),
                       ("Key file", self.key_file), ("Folder on server", self.rdir)):
            sf.addRow(lab, w)
        sf.addRow(self.remember)
        sv.addWidget(self.srv_fields)
        hb = QHBoxLayout()
        self.b_connect = QPushButton("Connect")
        self.b_connect.clicked.connect(self.do_connect)
        self.b_disconnect = QPushButton("Disconnect")
        self.b_disconnect.clicked.connect(self.do_disconnect)
        hb.addWidget(self.b_connect)
        hb.addWidget(self.b_disconnect)
        hb.addStretch()
        sv.addLayout(hb)
        v.addWidget(self.srv_box)

        # ---- folder modes
        self.folder = QLineEdit()
        b = QPushButton("Choose ...")
        b.clicked.connect(self.pick)
        r = QHBoxLayout()
        r.addWidget(self.folder, 1)
        r.addWidget(b)
        self.folder_w = QWidget()
        self.folder_w.setLayout(r)
        ff = QFormLayout()
        ff.addRow("Folder", self.folder_w)
        self.folder_form = QWidget()
        self.folder_form.setLayout(ff)
        v.addWidget(self.folder_form)

        self.err = QLabel("")
        self.err.setStyleSheet("color:#B00020")
        self.err.setWordWrap(True)
        v.addWidget(self.err)
        h = QHBoxLayout()
        self.b_reg = QPushButton("Create account")
        self.b_reg.clicked.connect(lambda: self.go(True))
        self.b_in = QPushButton("Sign in")
        self.b_in.setDefault(True)
        self.b_in.clicked.connect(lambda: self.go(False))
        h.addWidget(self.b_reg)
        h.addStretch()
        h.addWidget(self.b_in)
        v.addLayout(h)
        foot = QLabel("<small>The vault password cannot be reset: it is the key that encrypts your logins. The server only ever "
                      "stores encrypted data, and a copy stays on this computer, so a reinstalled server just gets it back. "
                      "A kept server login is stored on this computer only, in a file that only your user can read.</small>")
        foot.setWordWrap(True)
        v.addWidget(foot)
        self.mode.currentIndexChanged.connect(self.toggle)
        self.toggle()
        if self.auto and self.keep_pw.isChecked() and self.pw.text() and not note:
            # sign in without asking; folder modes at once, server mode as soon as the server answers
            if self.mode.currentData() != "server":
                QTimer.singleShot(0, lambda: self.go(False))
            else:
                self._auto_wait = True

    # ---- helpers
    def known_fp(self, hp):
        return settings().value("vaultkey/" + hp.replace(":", "_"), "")

    def server_cfg(self):
        pw = self.srv_pw.text() or ""
        return dict(host=self.host.text().strip(), port=self.port.value(), user=self.user.text().strip(), srv_pw=pw,
                    key_file=self.key_file.text().strip(), key_pass="", rdir=self.rdir.text().strip() or "digonto-sync")

    def show_state(self, connected, text=None, error=False):
        self.srv_fields.setVisible(not connected)
        self.b_connect.setEnabled(not connected)
        self.b_disconnect.setEnabled(connected)
        if connected:
            c = Link.cfg or self.server_cfg()
            self.status.setText(f"<b style='color:#C2395C'>&#9679; Connected</b> to {c['user']}@{c['host']}"
                                f" &nbsp; (folder <tt>{c['rdir']}</tt> is ready on the server)")
        else:
            col = "#B00020" if error else "#65596E"
            self.status.setText(text or f"<b style='color:{col}'>&#9675; Not connected</b>: enter your server and press Connect.")
        self.b_in.setEnabled(connected or self.mode.currentData() != "server")
        self.b_reg.setEnabled(connected or self.mode.currentData() != "server")

    def toggle(self):
        m = self.mode.currentData()
        srv = m == "server"
        self.srv_box.setVisible(srv)
        self.folder_form.setVisible(not srv)
        self.err.setText("")
        s = settings()
        if m == "syncdrive":
            self.folder.setText(s.value("syncdrive_folder", "") or syncdrive_guess())
            self.folder.setPlaceholderText("your SyncDrive folder")
        elif m == "folder":
            self.folder.setText(s.value("folder", ""))
            self.folder.setPlaceholderText("any synced folder, e.g. Dropbox")
        if srv:
            if Link.connected():
                self.show_state(True)
            else:
                self.show_state(False, "<i>Checking the connection ...</i>" if self.has_saved() else None)
                self.auto_check()
        self.adjustSize()

    def has_saved(self):
        d = load_link()
        return bool(d.get("connected") and self.host.text().strip() and self.user.text().strip() and (d.get("password") or self.key_file.text().strip()))

    def auto_check(self):
        """when a server login was kept: test it now, in the background"""
        if not self.has_saved() or (self.check is not None and self.check.isRunning()):
            return
        cfg = self.server_cfg()
        cfg["srv_pw"] = load_link().get("password", "") or cfg["srv_pw"]
        self.check = CheckThread(cfg, self.email.text().strip(), self.known_fp)
        self.check.done.connect(self.check_done)
        self.check.start()

    def check_done(self, ok, msg):
        if self.mode.currentData() != "server":
            return
        if ok:
            self.show_state(True)
            if getattr(self, "_auto_wait", False):
                self._auto_wait = False
                self.go(False)
        else:
            self._auto_wait = False
            self.show_state(False, f"<b style='color:#B00020'>&#9675; Not connected</b>: {msg[:200]}<br>Check the details and press Connect.", True)

    def do_connect(self):
        cfg = self.server_cfg()
        if not (cfg["host"] and cfg["user"]):
            self.err.setText("Enter the server address and the user.")
            return
        if not cfg["srv_pw"] and not cfg["key_file"]:
            self.err.setText("Enter the server password (or choose a key file).")
            return
        self.err.setText("")
        self.status.setText("<i>Connecting and preparing the folder on the server ...</i>")
        self.setCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            Link.open(cfg, self.email.text().strip(), lambda hp, fp: self.ask_trust(hp, fp))
        except Exception as e:  # noqa: BLE001
            self.unsetCursor()
            self.show_state(False, f"<b style='color:#B00020'>&#9675; Not connected</b>: {str(e)[:250]}", True)
            return
        self.unsetCursor()
        st = settings()
        st.setValue("mode", "server")
        for k in ("host", "port", "user", "key_file", "rdir"):
            st.setValue(k, cfg[k])
        save_link({"connected": True, "password": cfg["srv_pw"] if self.remember.isChecked() else "", "remember": self.remember.isChecked()})
        self.show_state(True)

    def do_disconnect(self):
        Link.close()
        d = load_link()
        save_link({"connected": False, "password": "", "remember": d.get("remember", True)})
        self.srv_pw.setText("")
        self.show_state(False)
        self.err.setText("")

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "Choose your sync folder", self.folder.text() or os.path.expanduser("~"))
        if d:
            self.folder.setText(d)

    def ask_trust(self, hp, fp):
        s = settings()
        known = s.value("vaultkey/" + hp.replace(":", "_"), "")
        if known == fp:
            return True, False
        changed = bool(known)
        q = (f"<b>WARNING: the key of {hp} has changed.</b><br>Someone may be intercepting the connection, or the server was "
             f"reinstalled.<br><tt>{fp}</tt><br><br>Trust the new key?" if changed else
             f"First connection to the vault server <b>{hp}</b>.<br>Fingerprint:<br><tt>{fp}</tt><br><br>Trust this server?")
        ok = QMessageBox.question(self, "Vault server key", q, QMessageBox.Yes | QMessageBox.No,
                                  QMessageBox.No if changed else QMessageBox.Yes) == QMessageBox.Yes
        if ok:
            s.setValue("vaultkey/" + hp.replace(":", "_"), fp)
        return ok, changed

    def go(self, create):
        email, pw = self.email.text().strip(), self.pw.text()
        mode = self.mode.currentData()
        cfg = dict(mode=mode, folder=self.folder.text().strip(), host=self.host.text().strip(), port=self.port.value(), user=self.user.text().strip(),
                   srv_pw=self.srv_pw.text() or pw, key_file=self.key_file.text().strip(), key_pass="", rdir=self.rdir.text().strip() or "digonto-sync")
        try:
            if mode == "server" and not Link.connected():
                raise VaultError("Press Connect first (the server is not connected).")
            if mode != "server" and not cfg["folder"]:
                raise VaultError("Choose the folder first.")
            self.setCursor(Qt.WaitCursor)
            self.err.setText("Opening the vault ...")
            QApplication.processEvents()
            where = make_store(cfg, email, self.ask_trust)
            self.vault = Vault.create(where, email, pw) if create else Vault.open(where, email, pw)
        except VaultError as e:
            self.err.setText(str(e))
            return
        except OSError as e:
            self.err.setText(f"Cannot use this location: {e}")
            return
        finally:
            self.unsetCursor()
        s = settings()
        s.setValue("email", email)
        s.setValue("mode", mode)
        try:
            save_vault_login({"email": email, "password": pw} if self.keep_pw.isChecked() else {})
        except OSError:
            pass
        if mode == "syncdrive":
            s.setValue("syncdrive_folder", cfg["folder"])
        elif mode == "folder":
            s.setValue("folder", cfg["folder"])
        self.accept()


def load_shortcuts():
    cfg = settings()
    for k, v in DEFAULT_SHORTCUTS.items():
        val = cfg.value("shortcuts/" + k, v)
        SHORTCUTS[k] = val if isinstance(val, str) and val else v


class ShortcutDialog(QDialog):
    LABELS = (("copy", "Copy selected text"), ("paste", "Paste"), ("zoom_in", "Bigger font"), ("zoom_out", "Smaller font"))

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Terminal shortcuts")
        f = QFormLayout(self)
        f.addRow(QLabel("Click a box and press the keys you want. The same on Linux, Windows and macOS\n"
                        "(on a Mac, Ctrl means the Cmd key). Ctrl+C with nothing selected still stops a running program."))
        self.edits = {}
        for k, label in self.LABELS:
            e = QKeySequenceEdit(QKeySequence(SHORTCUTS[k]))
            e.setMaximumSequenceLength(1)
            self.edits[k] = e
            f.addRow(label, e)
        row = QHBoxLayout()
        reset = QPushButton("Defaults")
        reset.clicked.connect(lambda: [self.edits[k].setKeySequence(QKeySequence(v)) for k, v in DEFAULT_SHORTCUTS.items()])
        ok = QPushButton("Save")
        ok.setDefault(True)
        ok.clicked.connect(self.save)
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(ok)
        f.addRow(row)

    def save(self):
        cfg = settings()
        for k, e in self.edits.items():
            t = e.keySequence().toString(QKeySequence.PortableText) or DEFAULT_SHORTCUTS[k]
            SHORTCUTS[k] = t
            cfg.setValue("shortcuts/" + k, t)
        self.accept()


class HostDialog(QDialog):
    def __init__(self, parent, host=None):
        super().__init__(parent)
        self.setWindowTitle("Server")
        self.setMinimumWidth(520)
        self.h = dict(host or new_host())
        h = self.h
        f = QFormLayout(self)
        self.name = QLineEdit(h["name"])
        self.group = QLineEdit(h["group"])
        self.group.setPlaceholderText("optional, e.g. Restaurants")
        self.host = QLineEdit(h["host"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(h["port"] or 22))
        self.user = QLineEdit(h["user"])
        self.auth = QComboBox()
        self.auth.addItems(["Password", "Private key"])
        self.auth.setCurrentIndex(1 if h["auth"] == "key" else 0)
        self.pw = QLineEdit(h["password"])
        self.pw.setEchoMode(QLineEdit.Password)
        self.key = QPlainTextEdit(h["key"])
        self.key.setPlaceholderText("-----BEGIN OPENSSH PRIVATE KEY----- ...")
        self.key.setFixedHeight(90)
        kb = QPushButton("Load key file ...")
        kb.clicked.connect(self.load_key)
        self.kp = QLineEdit(h["key_pass"])
        self.kp.setEchoMode(QLineEdit.Password)
        self.cmd = QLineEdit(h["command"])
        self.cmd.setPlaceholderText("optional, runs after login, e.g. cd /var/www")
        self.note = QLineEdit(h["note"])
        f.addRow("Name", self.name)
        f.addRow("Group", self.group)
        f.addRow("Host / IP", self.host)
        f.addRow("Port", self.port)
        f.addRow("User", self.user)
        f.addRow("Login with", self.auth)
        f.addRow("Password", self.pw)
        f.addRow("Private key", self.key)
        f.addRow("", kb)
        f.addRow("Key passphrase", self.kp)
        f.addRow("Start command", self.cmd)
        self.jump = QComboBox()
        self.jump.addItem("(none - connect directly)", "")
        me = self.h.get("id")
        for x in sorted(parent.vault.hosts(), key=lambda z: z.get("name", "").lower()):
            if x["id"] != me:
                self.jump.addItem(x.get("name") or x["host"], x["id"])
        i = self.jump.findData(self.h.get("jump", ""))
        self.jump.setCurrentIndex(max(0, i))
        f.addRow("Jump host", self.jump)
        self.fwd = QPlainTextEdit(self.h.get("forwards", ""))
        self.fwd.setFixedHeight(66)
        self.fwd.setPlaceholderText("one rule per line, for example:\nL 8080:localhost:80      (this computer :8080 -> server's localhost:80)\n"
                                    "R 9000:localhost:3000   (server :9000 -> this computer :3000)\nD 1080                  (SOCKS5 proxy on this computer :1080)")
        f.addRow("Port forwarding", self.fwd)
        f.addRow("Note", self.note)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#B00020")
        f.addRow(self.err)
        r = QHBoxLayout()
        ok = QPushButton("Save")
        ok.setDefault(True)
        ok.clicked.connect(self.save)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        r.addStretch()
        r.addWidget(c)
        r.addWidget(ok)
        f.addRow(r)
        self.auth.currentIndexChanged.connect(self.toggle)
        self.toggle()

    def toggle(self):
        k = self.auth.currentIndex() == 1
        self.pw.setEnabled(not k)
        self.key.setEnabled(k)
        self.kp.setEnabled(k)

    def load_key(self):
        p, _ = QFileDialog.getOpenFileName(self, "Private key file", os.path.expanduser("~/.ssh"))
        if p:
            try:
                self.key.setPlainText(open(p, encoding="utf-8").read())
            except (OSError, UnicodeDecodeError) as e:
                self.err.setText(f"Cannot read this file: {e}")

    def save(self):
        if not self.host.text().strip():
            self.err.setText("Enter the host name or IP address.")
            return
        try:
            from sshkit import forward
            forward.parse(self.fwd.toPlainText())
        except ValueError as e:
            self.err.setText(str(e))
            return
        h = self.h
        h.update(name=self.name.text().strip() or self.host.text().strip(), group=self.group.text().strip(),
                 host=self.host.text().strip(), port=self.port.value(), user=self.user.text().strip(),
                 auth="key" if self.auth.currentIndex() == 1 else "password", password=self.pw.text(),
                 key=self.key.toPlainText().strip() + "\n" if self.key.toPlainText().strip() else "", key_pass=self.kp.text(),
                 command=self.cmd.text().strip(), note=self.note.text().strip(),
                 jump=self.jump.currentData() or "", forwards=self.fwd.toPlainText().strip())
        self.accept()


class SessionTab(QWidget):
    def __init__(self, win, host):
        super().__init__()
        self.win, self.host = win, host
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.state = QLabel("")
        self.re = QPushButton("Reconnect")
        self.re.clicked.connect(self.stop_or_connect)
        self.retrying = False
        self.was_connected = False
        self.attempts = 0
        self.retry_timer = QTimer(self, singleShot=True, timeout=self.retry)
        self.re.setVisible(False)
        top.addWidget(self.state, 1)
        top.addWidget(self.re)
        v.addLayout(top)
        self.inner = QTabWidget()
        self.inner.setTabPosition(QTabWidget.South)
        v.addWidget(self.inner, 1)
        self.files = None
        self.session = self.term = None
        self.inner.currentChanged.connect(self.tab_changed)
        self.connect_now()

    def connect_now(self):
        """manual (re)connect: fresh terminal"""
        self.retrying = False
        self.retry_timer.stop()
        if self.session:
            self.session.close()
        self.inner.clear()
        self.files = None
        self.re.setVisible(False)
        self.term = TermView(Session(self.host, self.win.vault))
        self.inner.addTab(self.term, "Terminal")
        self.inner.addTab(QLabel("  Connecting ..."), "Files (SFTP)")
        self._start("Connecting ...")

    def _start(self, text):
        s = self.session = Session(self.host, self.win.vault)
        self.term.attach(s)
        s.status.connect(self.state.setText)
        s.ask_hostkey.connect(self.hostkey)
        s.connected.connect(self.on_connected)
        s.failed.connect(self.on_failed)
        s.closed.connect(self.on_closed)
        self.state.setText(text)
        cols, rows = self.term.sizes()
        s.start(cols, rows)

    # ---- the server went away (reboot, power off, network): keep trying every half second until it is back
    def lose(self, why):
        if self.retrying:
            return
        self.retrying = True
        self.attempts = 0
        self.term.screen.feed(f"\r\n\x1b[33m[{why} - reconnecting ...]\x1b[0m\r\n".encode())
        self.term.update()
        if self.files is not None:
            self.inner.removeTab(1)
            self.inner.addTab(QLabel("  Reconnecting ..."), "Files (SFTP)")
            self.files = None
        self.re.setText("Stop trying")
        self.re.setVisible(True)
        self.retry_timer.start(500)

    def retry(self):
        if not self.retrying:
            return
        self.attempts += 1
        self.state.setText(f"Server not reachable - reconnecting ... (try {self.attempts})")
        self._start(f"Server not reachable - reconnecting ... (try {self.attempts})")

    def stop_or_connect(self):
        if self.retrying:
            self.retrying = False
            self.retry_timer.stop()
            self.re.setText("Reconnect")
            self.state.setText("Stopped. Press Reconnect to try again.")
        else:
            self.connect_now()

    def hostkey(self, hp, kind, fp, changed):
        if changed:
            q = (f"<b>WARNING: the key of {hp} has changed.</b><br>This can mean someone is intercepting the connection, "
                 f"or the server was reinstalled.<br><br>New fingerprint ({kind}):<br><tt>{fp}</tt><br><br>Connect anyway?")
        else:
            q = f"First connection to <b>{hp}</b>.<br>Server fingerprint ({kind}):<br><tt>{fp}</tt><br><br>Trust this server?"
        r = QMessageBox.question(self, "Server key", q, QMessageBox.Yes | QMessageBox.No, QMessageBox.No if changed else QMessageBox.Yes)
        self.session.answer_hostkey(r == QMessageBox.Yes)

    def on_connected(self):
        if self.retrying:
            self.term.screen.feed(b"\x1b[32m[reconnected]\x1b[0m\r\n")
        self.retrying = False
        self.was_connected = True
        self.re.setText("Reconnect")
        self.re.setVisible(False)
        self.state.setText(f"Connected: {self.host['user']}@{self.host['host']}")
        self.term.setFocus()
        self.win.fill()
        self.make_files()

    def make_files(self):
        """the Files (SFTP) tab is built as soon as the connection is up"""
        if self.files is not None:
            return
        self.files = FilesTab(self.session)
        cur = self.inner.currentIndex()
        self.inner.removeTab(1)
        self.inner.addTab(self.files, "Files (SFTP)")
        self.inner.setCurrentIndex(cur if cur == 0 else 1)

    def on_failed(self, msg):
        fatal = msg.startswith("Login failed") or "Host key was not" in msg
        if (self.retrying or self.was_connected) and not fatal:
            if self.retrying:
                self.retry_timer.start(500)       # still down: try again in half a second
            else:
                self.lose("Connection lost")
            return
        self.retrying = False
        self.retry_timer.stop()
        self.re.setText("Reconnect")
        self.state.setText(f"<span style='color:#B00020'>Could not connect: {msg}</span>")
        self.re.setVisible(True)
        if self.files is None and self.inner.count() > 1:
            self.inner.removeTab(1)
            self.inner.addTab(QLabel("  Not connected - press Reconnect."), "Files (SFTP)")

    def on_closed(self):
        if self.was_connected:
            self.lose("Connection lost")
        else:
            self.state.setText("Session ended.")
            self.re.setVisible(True)

    def tab_changed(self, i):
        if i == 1 and self.files is None and self.session and self.session.alive:
            self.make_files()

    def shutdown(self):
        self.retrying = False
        self.retry_timer.stop()
        if self.session:
            self.session.close()


class Main(QMainWindow):
    def __init__(self, vault):
        super().__init__()
        self.vault = vault
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.icon = ui.app_icon(HERE, "digonto-ssh")
        self.setWindowIcon(self.icon)
        ui.fit_window(self, 1280, 800)
        sp = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search servers ...")
        self.search.textChanged.connect(self.fill)
        lv.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.connect_item(it))
        lv.addWidget(self.tree, 1)
        self.acc_btn = None
        for text, fn in (("Connect", lambda: self.connect_item(self.tree.currentItem())),
                         ("Account", lambda: self.account_menu(self.acc_btn))):        # add / edit / delete: in the left bar
            b = QPushButton(text)
            b.clicked.connect(fn)
            if text == "Connect":
                ui.primary(b)
            if text == "Settings":
                b.setToolTip("Terminal colours, font and font size")
            if text == "Account":
                self.acc_btn = b
            lv.addWidget(b)
        self.sync = QLabel("")
        self.sync.setStyleSheet("color:#666")
        self.sync.setWordWrap(True)
        lv.addWidget(self.sync)
        self.known_tab = QWidget()
        kv = QVBoxLayout(self.known_tab)
        kv.addWidget(QLabel("Servers you trusted. Remove one and you are asked to trust it again the next time you connect."))
        kv.itemAt(0).widget().setWordWrap(True)
        self.known = QTreeWidget()
        self.known.setRootIsDecorated(False)
        self.known.setHeaderLabels(["Server (host:port)", "Fingerprint"])
        self.known.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.known.setColumnWidth(0, 160)
        kv.addWidget(self.known, 1)
        kb = QPushButton("Remove selected")
        kb.clicked.connect(self.forget_known)
        kv.addWidget(kb)
        self.lefttabs = QTabWidget()
        self.lefttabs.addTab(left, "Servers")
        self.lefttabs.addTab(self.known_tab, "Known hosts")
        sp.addWidget(self.lefttabs)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        sp.addWidget(self.tabs)
        sp.setStretchFactor(1, 1)
        sp.setSizes([360, 920])
        self.welcome = ui.StartScreen(
            "SSH terminal and SFTP files - your servers on every computer",
            [("Add server", "new", self.add, True), ("Connect", "terminal", lambda: self.connect_item(self.tree.currentItem()), False)],
            "or double-click a server on the left")
        self.tabs.addTab(self.welcome, "Start")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.rail = ui.Rail(
            [("new", "new", "Add server (Ctrl+N)", self.add),
             ("connect", "terminal", "Connect to the selected server", lambda: self.connect_item(self.tree.currentItem())),
             ("edit", "pen", "Edit the selected server", self.edit),
             ("delete", "remove", "Delete the selected server", self.delete),
             None,
             ("known", "lock", "Known hosts (servers you trusted)", lambda: self.lefttabs.setCurrentWidget(self.known_tab)),
             ("account", "server", "Account and sync", lambda: self.account_menu(self.rail.button("account")))],
            [("settings", "settings", "Settings and help", self.rail_settings)])
        body = QWidget()
        bh = QHBoxLayout(body)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(0)
        bh.addWidget(self.rail)
        bh.addWidget(sp, 1)
        ui.wrap_with_header(self, ui.make_header("Digonto SSH", "SSH terminal and SFTP files, your servers on every computer",
                                                 self.icon, self.about), body)
        ui.file_menu(self, [("Add server ...", "Ctrl+N", self.add)])
        em = self.menuBar().addMenu("Settings")
        em.addAction("Terminal colours and font ...", self.terminal_settings)
        self.fill()
        self.timer = QTimer(self, interval=15000, timeout=self.poll)
        self.timer.start()
        self.signed_out = False

    # ---- hosts
    def fill(self, *_):
        q = self.search.text().strip().lower()
        self.tree.clear()
        groups = {}
        for h in self.vault.hosts():
            if q and q not in (h["name"] + h["host"] + h["group"] + h["user"]).lower():
                continue
            g = h.get("group") or ""
            parent = self.tree.invisibleRootItem()
            if g:
                if g not in groups:
                    groups[g] = QTreeWidgetItem([g])
                    self.tree.addTopLevelItem(groups[g])
                parent = groups[g]
            it = QTreeWidgetItem([f"{h['name']}   ({h['user']}@{h['host']})" if h["user"] else f"{h['name']}   ({h['host']})"])
            it.setData(0, Qt.UserRole, h["id"])
            parent.addChild(it)
        self.tree.expandAll()
        self.known.clear()
        for hp, fp in self.vault.known_all():
            self.known.addTopLevelItem(QTreeWidgetItem([hp, fp]))
        self.lefttabs.setTabText(1, f"Known hosts ({self.known.topLevelItemCount()})")
        n = len(self.vault.data["hosts"])
        self.sync.setText(f"{n} server(s)\nVault: {self.vault.path}\nSigned in as {self.vault.email}")

    def current(self, item=None):
        it = item or self.tree.currentItem()
        hid = it.data(0, Qt.UserRole) if it else None
        return self.vault.data["hosts"].get(hid) if hid else None

    def save_guard(self, fn):
        try:
            fn()
            return True
        except WrongPassword:
            self.relogin("The password was changed on another computer. Please sign in again.")
        except (VaultError, OSError) as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save to the sync folder.\n\n{e}")
        return False

    def add(self):
        d = HostDialog(self)
        if d.exec() == QDialog.Accepted:
            self.save_guard(lambda: self.vault.put(d.h))
            self.fill()

    def edit(self):
        h = self.current()
        if not h:
            return
        d = HostDialog(self, h)
        if d.exec() == QDialog.Accepted:
            self.save_guard(lambda: self.vault.put(d.h))
            self.fill()

    def delete(self):
        h = self.current()
        if h and QMessageBox.question(self, APP_NAME, f"Delete '{h['name']}' on all your computers?") == QMessageBox.Yes:
            self.save_guard(lambda: self.vault.delete(h["id"]))
            self.fill()

    def forget_known(self):
        items = self.known.selectedItems()
        if not items:
            QMessageBox.information(self, APP_NAME, "Select one or more servers first.")
            return
        if QMessageBox.question(self, APP_NAME, f"Remove {len(items)} server(s) from known hosts on all your computers?") != QMessageBox.Yes:
            return
        self.save_guard(lambda: [self.vault.forget(i.text(0)) for i in items])
        self.fill()

    def connect_item(self, item):
        h = self.current(item)
        if not h:
            return
        tab = SessionTab(self, h)
        i = self.tabs.addTab(tab, h["name"])
        self.tabs.setCurrentIndex(i)

    def close_tab(self, i):
        w = self.tabs.widget(i)
        if isinstance(w, SessionTab):
            w.shutdown()
        self.tabs.removeTab(i)

    # ---- sync + account
    def poll(self):
        try:
            if self.vault.changed_on_disk() and self.vault.refresh():
                self.fill()
        except WrongPassword:
            self.relogin("The password was changed on another computer. Please sign in again.")
        except Exception:  # noqa: BLE001
            pass

    def rail_settings(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("Terminal colours and font …", self.terminal_settings)
        m.addSeparator()
        m.addAction("About Digonto SSH", self.about)
        b = self.rail.button("settings")
        m.exec(b.mapToGlobal(b.rect().topRight()))

    def terminal_settings(self):
        from sshkit.termsettings import TermSettingsDialog
        d = TermSettingsDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        _name, fam, pt = d.result_choice
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SessionTab) and w.term is not None:
                w.term.set_font(fam, pt)
                w.term.update()

    def account_menu(self, anchor=None):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a1 = m.addAction("Change password ...")
        a2 = m.addAction("Sign out")
        a3 = m.addAction("Show sync file location")
        a4 = m.addAction("Terminal shortcuts ...")
        r = m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()) if anchor else self.cursor().pos())
        if r == a1:
            p, ok = QInputDialog.getText(self, "Change password", "New password (min. 8 characters)\nUse it on your other computers next time:",
                                         QLineEdit.Password)
            if ok:
                try:
                    self.vault.change_password(p)
                    QMessageBox.information(self, APP_NAME, "Password changed. Other computers must sign in again with the new password.")
                except VaultError as e:
                    QMessageBox.warning(self, APP_NAME, str(e))
        elif r == a2:
            self.relogin("")
        elif r == a3:
            QMessageBox.information(self, APP_NAME, self.vault.path)
        elif r == a4:
            ShortcutDialog(self).exec()

    def relogin(self, note):
        self.timer.stop()
        for i in range(self.tabs.count() - 1, -1, -1):
            if isinstance(self.tabs.widget(i), SessionTab):
                self.close_tab(i)
        d = LoginDialog(self, note)
        if d.exec() == QDialog.Accepted:
            self.vault = d.vault
            self.fill()
            self.timer.start()
        else:
            self.signed_out = True
            self.close()

    def about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>SSH terminal and SFTP file manager for Ubuntu, macOS and Windows.</p>"
            "<p>Your servers, passwords and keys are saved in one encrypted file (AES-256-GCM, key from your password with scrypt) "
            "in the folder you chose, for example your SyncDrive. The same e-mail, password and folder on another computer shows "
            "the same servers.<br>Nothing is sent to any server of ours.</p>"
            "<p>Built with: Qt for Python (PySide6-Essentials), Paramiko (SSH/SFTP) and cryptography - all locked with SHA-256 hashes.</p>"
            "<p>Also included: mouse in terminal programs (hold Shift to select text instead), port forwarding (L/R/D) and jump hosts.</p>")

    def closeEvent(self, e):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SessionTab):
                w.shutdown()
        super().closeEvent(e)
        ui.force_exit_soon()


def main():
    if "--version" in sys.argv:
        print(__version__)
        return
    ui.mac_app_name(APP_NAME)
    app = QApplication(sys.argv)
    ui.setup_app(app, APP_NAME, "digonto-ssh", ui.app_icon(HERE, "digonto-ssh"), theme=True)
    load_shortcuts()
    termtheme.load_saved()
    d = LoginDialog(auto=True)
    if d.exec() != QDialog.Accepted:
        sys.exit(0)
    w = Main(d.vault)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
