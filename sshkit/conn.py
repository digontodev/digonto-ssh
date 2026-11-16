"""One SSH connection (shell + SFTP) running in background threads, reporting to the window through Qt signals."""
import base64
import hashlib
import io
import socket
import threading
import time

import paramiko
from PySide6.QtCore import QObject, Signal

from . import forward

KEY_CLASSES = [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def load_key(text, passphrase=None):
    last = None
    for cls in KEY_CLASSES:
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise ValueError(f"This is not a usable private key ({last}).")


class Session(QObject):
    status = Signal(str)
    connected = Signal()
    failed = Signal(str)
    data = Signal(bytes)
    closed = Signal()
    ask_hostkey = Signal(str, str, str, bool)      # host:port, key type, fingerprint, changed

    def __init__(self, host, vault):
        super().__init__()
        self.h, self.vault = host, vault
        self.client = None
        self.chan = None
        self._sftp = None
        self._sftp_lock = threading.Lock()
        self._answer = threading.Event()
        self._ok = False
        self.alive = False
        self.closing = False
        self.size = (80, 24)
        self.jumps = []          # clients of the jump hosts (kept open while this connection lives)
        self.fwd = None

    def _emit(self, name, *a):
        try:
            getattr(self, name).emit(*a)
        except RuntimeError:       # window already closed
            self.alive = False

    # ---- host key confirmation (called from the connect thread)
    def answer_hostkey(self, ok):
        self._ok = ok
        self._answer.set()

    def _policy(self, h):
        sess = self

        class Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                hp = f"{h['host']}:{h['port']}"
                fp = fingerprint(key)
                known = sess.vault.known(hp)
                if known == fp:
                    return
                sess._answer.clear()
                sess._emit('ask_hostkey', hp, key.get_name(), fp, bool(known))
                if not sess._answer.wait(120) or not sess._ok:
                    raise paramiko.SSHException("Host key was not accepted.")
                sess.vault.trust(hp, fp)
        return Policy()

    # ---- one login (also used for jump hosts)
    def _client_for(self, h, sock=None):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(self._policy(h))
        kw = dict(hostname=h["host"], port=int(h["port"] or 22), username=h["user"] or None, timeout=6,
                  banner_timeout=20, auth_timeout=25, look_for_keys=False, allow_agent=False)
        if sock is not None:
            kw["sock"] = sock
        if h.get("auth") == "key" and h.get("key"):
            kw["pkey"] = load_key(h["key"], h.get("key_pass"))
        else:
            kw["password"] = h.get("password") or None
            kw["look_for_keys"] = not h.get("password")
            kw["allow_agent"] = not h.get("password")
        try:
            c.connect(**kw)
        except paramiko.AuthenticationException:
            # some servers only offer keyboard-interactive
            t = c.get_transport()
            if t is None or not h.get("password") or not t.is_active():
                raise
            t.auth_interactive(h["user"], lambda title, instr, prompts: [h["password"] for _ in prompts])
        return c

    def _find_host(self, ref):
        ref = (ref or "").strip()
        for x in self.vault.data["hosts"].values():
            if x["id"] == ref or x.get("name") == ref:
                return x
        return None

    def _via_jump(self, h, depth=0):
        """connect through the chain of jump hosts; returns the channel to use as socket for the final login (or None)"""
        ref = h.get("jump")
        if not ref:
            return None
        if depth > 4:
            raise paramiko.SSHException("Too many jump hosts in a row.")
        j = self._find_host(ref)
        if j is None:
            raise paramiko.SSHException(f"Jump host \"{ref}\" is not in your server list any more.")
        self._emit('status', f"Connecting through {j.get('name') or j['host']} ...")
        jc = self._client_for(j, self._via_jump(j, depth + 1))
        self.jumps.append(jc)
        return jc.get_transport().open_channel("direct-tcpip", (h["host"], int(h["port"] or 22)), ("127.0.0.1", 0))

    def start(self, cols=80, rows=24):
        self.size = (cols, rows)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        h = self.h
        try:
            self._emit('status', f"Connecting to {h['host']}:{h['port']} ...")
            c = self._client_for(h, self._via_jump(h))
            c.get_transport().set_keepalive(10)
            self.client = c
            self._start_forwards()
            ch = c.invoke_shell(term="xterm-256color", width=self.size[0], height=self.size[1])
            ch.settimeout(0.5)
            self.chan = ch
            self.alive = True
            threading.Thread(target=self._watch, daemon=True).start()
            self._emit('connected')
            if h.get("command"):
                time.sleep(0.3)
                ch.send(h["command"].rstrip("\n") + "\n")
            while self.alive:
                try:
                    d = ch.recv(32768)
                except (socket.timeout, TimeoutError):
                    continue
                except Exception:  # noqa: BLE001
                    break
                if not d:
                    break
                self._emit('data', d)
            self.alive = False
            if not self.closing:
                self._emit('closed')
        except Exception as e:  # noqa: BLE001
            self.alive = False
            if self.closing:
                return
            msg = str(e) or type(e).__name__
            if isinstance(e, paramiko.AuthenticationException):
                msg = "Login failed: wrong user name, password or key."
            self._emit('failed', msg)

    def _start_forwards(self):
        text = self.h.get("forwards", "")
        if not text.strip():
            return
        try:
            rules = forward.parse(text)
        except forward.RuleError as e:
            self._emit('status', str(e))
            return
        self.fwd = forward.Forwarder(self.client, rules, lambda m: self._emit('status', m))
        self.fwd.start()

    def _watch(self):
        """notice a server that vanished (power off / reboot) within about 12 seconds, not after minutes"""
        bad = 0
        while self.alive:
            for _ in range(10):
                if not self.alive:
                    return
                time.sleep(0.5)
            try:
                t = self.client.get_transport()
                if t is None or not t.is_active():
                    raise OSError("connection closed")
                t.open_session(timeout=6).close()
                bad = 0
            except Exception:  # noqa: BLE001
                bad += 1
                if bad >= 2:
                    self.alive = False
                    try:
                        self.client.close()
                    except Exception:  # noqa: BLE001
                        pass
                    return

    def send(self, b):
        if self.chan and self.alive:
            try:
                self.chan.send(b)
            except Exception:  # noqa: BLE001
                pass

    def resize(self, cols, rows):
        self.size = (cols, rows)
        if self.chan and self.alive:
            try:
                self.chan.resize_pty(width=cols, height=rows)
            except Exception:  # noqa: BLE001
                pass

    def sftp(self):
        with self._sftp_lock:
            if self._sftp is None:
                self._sftp = self.client.open_sftp()
            return self._sftp

    def close(self):
        self.closing = True
        self.alive = False
        try:
            if self.fwd:
                self.fwd.close()
            if self._sftp:
                self._sftp.close()
            if self.client:
                self.client.close()
            for j in self.jumps:
                j.close()
        except Exception:  # noqa: BLE001
            pass
