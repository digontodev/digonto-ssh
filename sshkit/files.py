"""SFTP file manager: local folder on the left, server folder on the right."""
import os
import posixpath
import stat
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

_keep = set()


class Task(QObject):
    done = Signal(object)
    err = Signal(str)
    progress = Signal(str, int, int)

    def __init__(self, fn, on_done=None, on_err=None, on_progress=None):
        super().__init__()
        self.fn = fn
        if on_done:
            self.done.connect(on_done)
        if on_err:
            self.err.connect(on_err)
        if on_progress:
            self.progress.connect(on_progress)
        self.cancel = False
        self.done.connect(lambda *_: _keep.discard(self))
        self.err.connect(lambda *_: _keep.discard(self))
        _keep.add(self)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:  # noqa: BLE001
            self.err.emit(str(e) or type(e).__name__)


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


class Panel(QWidget):
    changed = Signal()

    def __init__(self, title, remote):
        super().__init__()
        self.remote = remote
        self.path = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel(f"<b>{title}</b>"))
        r = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.returnPressed.connect(lambda: self.go(self.edit.text()))
        up = QPushButton("Up")
        up.clicked.connect(self.up)
        rf = QPushButton("Refresh")
        rf.clicked.connect(lambda: self.go(self.path))
        for w, st in ((up, 0), (self.edit, 1), (rf, 0)):
            r.addWidget(w, st)
        v.addLayout(r)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setColumnWidth(0, 210)
        self.tree.setColumnWidth(1, 80)
        self.tree.itemDoubleClicked.connect(self.open_item)
        v.addWidget(self.tree, 1)
        r = QHBoxLayout()
        for text, fn in (("New folder", self.mkdir), ("Rename", self.rename), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r.addWidget(b)
        r.addStretch()
        v.addLayout(r)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#B00020")
        v.addWidget(self.status)

    # entries are (name, is_dir, size, mtime)
    def fill(self, entries):
        self.tree.clear()
        entries = sorted(entries, key=lambda e: (not e[1], e[0].lower()))
        for name, isdir, size, mt in entries:
            it = QTreeWidgetItem([("[ ] " if False else "") + name + ("/" if isdir else ""), "" if isdir else human(size),
                                  time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else ""])
            it.setData(0, Qt.UserRole, (name, isdir))
            it.setTextAlignment(1, Qt.AlignRight)
            self.tree.addTopLevelItem(it)

    def selected(self):
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]

    def join(self, name):
        return posixpath.join(self.path, name) if self.remote else os.path.join(self.path, name)

    def open_item(self, it, _col=0):
        name, isdir = it.data(0, Qt.UserRole)
        if isdir:
            self.go(self.join(name))

    def up(self):
        self.go(posixpath.dirname(self.path.rstrip("/")) or "/" if self.remote else os.path.dirname(self.path))


class LocalPanel(Panel):
    def __init__(self):
        super().__init__("This computer", False)
        self.go(os.path.expanduser("~"))

    def go(self, path):
        path = os.path.abspath(os.path.expanduser(path or "~"))
        try:
            ents = []
            with os.scandir(path) as it:
                for e in it:
                    try:
                        s = e.stat()
                        ents.append((e.name, e.is_dir(), s.st_size, s.st_mtime))
                    except OSError:
                        pass
        except OSError as e:
            self.status.setText(str(e))
            return
        self.status.setText("")
        self.path = path
        self.edit.setText(path)
        self.fill(ents)

    def mkdir(self):
        n, ok = QInputDialog.getText(self, "New folder", "Name")
        if ok and n:
            try:
                os.mkdir(self.join(n))
            except OSError as e:
                self.status.setText(str(e))
            self.go(self.path)

    def rename(self):
        s = self.selected()
        if len(s) != 1:
            return
        n, ok = QInputDialog.getText(self, "Rename", "New name", text=s[0][0])
        if ok and n and n != s[0][0]:
            try:
                os.rename(self.join(s[0][0]), self.join(n))
            except OSError as e:
                self.status.setText(str(e))
            self.go(self.path)

    def delete(self):
        s = self.selected()
        if not s:
            return
        if QMessageBox.question(self, "Delete", f"Move {len(s)} item(s) to the trash?") != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile
        for name, _d in s:
            if not QFile.moveToTrash(self.join(name)):
                self.status.setText(f"Could not move {name} to the trash.")
        self.go(self.path)


class RemotePanel(Panel):
    def __init__(self, session):
        super().__init__("Server", True)
        self.session = session
        self.setAcceptDrops(True)
        self.sftp = None
        self.drop_cb = None
        Task(lambda t: (session.sftp(), session.sftp().normalize(".")), self._start, self._fail)

    def _fail(self, msg):
        self.status.setText(msg)

    def _start(self, res):
        self.sftp = res[0]
        self.go(res[1])

    def go(self, path):
        if self.sftp is None:
            return
        path = path or "/"
        self.status.setText("Loading ...")

        def work(t):
            ents = []
            for a in self.sftp.listdir_attr(path):
                ents.append((a.filename, stat.S_ISDIR(a.st_mode or 0) or (stat.S_ISLNK(a.st_mode or 0) and self._is_dir_link(path, a.filename)),
                             a.st_size or 0, a.st_mtime or 0))
            return self.sftp.normalize(path), ents

        def done(r):
            self.status.setText("")
            self.path = r[0]
            self.edit.setText(self.path)
            self.fill(r[1])
        Task(work, done, self._fail)

    def _is_dir_link(self, base, name):
        try:
            return stat.S_ISDIR(self.sftp.stat(posixpath.join(base, name)).st_mode)
        except OSError:
            return False

    def mkdir(self):
        n, ok = QInputDialog.getText(self, "New folder", "Name")
        if ok and n:
            Task(lambda t: self.sftp.mkdir(self.join(n)), lambda _: self.go(self.path), self._fail)

    def rename(self):
        s = self.selected()
        if len(s) != 1:
            return
        n, ok = QInputDialog.getText(self, "Rename", "New name", text=s[0][0])
        if ok and n and n != s[0][0]:
            Task(lambda t: self.sftp.rename(self.join(s[0][0]), self.join(n)), lambda _: self.go(self.path), self._fail)

    def delete(self):
        s = self.selected()
        if not s:
            return
        if QMessageBox.question(self, "Delete on the server",
                                f"Permanently delete {len(s)} item(s) on the server?\nThis cannot be undone.") != QMessageBox.Yes:
            return
        base = self.path

        def rmtree(sf, p):
            for a in sf.listdir_attr(p):
                q = posixpath.join(p, a.filename)
                if stat.S_ISDIR(a.st_mode or 0):
                    rmtree(sf, q)
                else:
                    sf.remove(q)
            sf.rmdir(p)

        def work(t):
            for name, isdir in s:
                p = posixpath.join(base, name)
                if isdir:
                    rmtree(self.sftp, p)
                else:
                    self.sftp.remove(p)
        Task(work, lambda _: self.go(self.path), lambda m: (self._fail(m), self.go(self.path)))

    # drag files from the computer's file manager onto the server list
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths and self.drop_cb:
            self.drop_cb(paths)


class FilesTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        v = QVBoxLayout(self)
        sp = QSplitter()
        self.local = LocalPanel()
        self.remote = RemotePanel(session)
        self.remote.drop_cb = self.upload
        mid = QWidget()
        m = QVBoxLayout(mid)
        m.addStretch()
        up = QPushButton("Upload  >>")
        up.clicked.connect(lambda: self.upload([self.local.join(n) for n, _d in self.local.selected()]))
        dn = QPushButton("<<  Download")
        dn.clicked.connect(self.download)
        m.addWidget(up)
        m.addWidget(dn)
        m.addStretch()
        sp.addWidget(self.local)
        sp.addWidget(mid)
        sp.addWidget(self.remote)
        sp.setStretchFactor(0, 1)
        sp.setStretchFactor(2, 1)
        sp.setSizes([420, 110, 420])
        v.addWidget(sp, 1)
        r = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        self.msg = QLabel("Tip: you can also drag files from your file manager onto the server list.")
        self.cancel = QPushButton("Cancel")
        self.cancel.setVisible(False)
        r.addWidget(self.msg, 1)
        r.addWidget(self.bar, 1)
        r.addWidget(self.cancel)
        v.addLayout(r)
        self.task = None

    def _begin(self, text, fn, refresh):
        if self.task is not None:
            self.msg.setText("A transfer is already running.")
            return
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.cancel.setVisible(True)
        self.msg.setText(text)

        def prog(label, done, total):
            self.msg.setText(label)
            self.bar.setValue(int(1000 * done / total) if total else 0)

        def finish(res):
            self.task = None
            self.bar.setVisible(False)
            self.cancel.setVisible(False)
            self.msg.setText(res if isinstance(res, str) else "Done.")
            refresh()

        def fail(m):
            self.task = None
            self.bar.setVisible(False)
            self.cancel.setVisible(False)
            self.msg.setText("Error: " + m)
            refresh()
        self.task = Task(fn, finish, fail, prog)
        self.cancel.clicked.connect(lambda: setattr(self.task, "cancel", True) if self.task else None)

    def _walk_local(self, paths):
        """-> list of (local file, relative remote path) and list of remote dirs to create"""
        files, dirs = [], []
        for p in paths:
            base = os.path.dirname(p.rstrip(os.sep))
            if os.path.isdir(p):
                for root, ds, fs in os.walk(p):
                    rel = os.path.relpath(root, base).replace(os.sep, "/")
                    dirs.append(rel)
                    for f in fs:
                        files.append((os.path.join(root, f), posixpath.join(rel, f)))
            elif os.path.isfile(p):
                files.append((p, os.path.basename(p)))
        return files, dirs

    def upload(self, paths):
        if not paths or self.remote.sftp is None:
            return
        files, dirs = self._walk_local(paths)
        dest = self.remote.path
        total = sum(os.path.getsize(f) for f, _r in files) or 1

        def work(t):
            sf = self.session.client.open_sftp()
            try:
                for d in dirs:
                    try:
                        sf.mkdir(posixpath.join(dest, d))
                    except OSError:
                        pass
                done = 0
                for src, rel in files:
                    if t.cancel:
                        return "Cancelled."
                    size = os.path.getsize(src)
                    base = done

                    def cb(sent, _tot, base=base, rel=rel):
                        t.progress.emit(f"Uploading {rel}", base + sent, total)
                    sf.put(src, posixpath.join(dest, rel), callback=cb)
                    done += size
                return f"Uploaded {len(files)} file(s)."
            finally:
                sf.close()
        self._begin("Uploading ...", work, lambda: self.remote.go(self.remote.path))

    def download(self):
        sel = self.remote.selected()
        if not sel:
            return
        dest, base = self.local.path, self.remote.path

        def collect(sf, rp, rel, files, dirs):
            for a in sf.listdir_attr(rp):
                q, r = posixpath.join(rp, a.filename), posixpath.join(rel, a.filename)
                if stat.S_ISDIR(a.st_mode or 0):
                    dirs.append(r)
                    collect(sf, q, r, files, dirs)
                else:
                    files.append((q, r, a.st_size or 0))

        def work(t):
            sf = self.session.client.open_sftp()
            try:
                files, dirs = [], []
                for name, isdir in sel:
                    if isdir:
                        dirs.append(name)
                        collect(sf, posixpath.join(base, name), name, files, dirs)
                    else:
                        files.append((posixpath.join(base, name), name, sf.stat(posixpath.join(base, name)).st_size))
                for d in dirs:
                    os.makedirs(os.path.join(dest, *d.split("/")), exist_ok=True)
                total = sum(f[2] for f in files) or 1
                done = 0
                for rp, rel, size in files:
                    if t.cancel:
                        return "Cancelled."
                    b0 = done

                    def cb(sent, _tot, b0=b0, rel=rel):
                        t.progress.emit(f"Downloading {rel}", b0 + sent, total)
                    sf.get(rp, os.path.join(dest, *rel.split("/")), callback=cb)
                    done += size
                return f"Downloaded {len(files)} file(s)."
            finally:
                sf.close()
        self._begin("Downloading ...", work, lambda: self.local.go(self.local.path))
