"""Encrypted host vault: one file, kept in a folder you choose (your SyncDrive), read by every computer with the same email + password.

File = small JSON header + AES-256-GCM encrypted body. Key = scrypt(password, salt + email). Nothing readable is ever stored
in the file except the e-mail's fingerprint, so the sync folder / server can't read hosts, passwords or private keys.
"""
import base64
import hashlib
import json
import os
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

N, R, P = 2 ** 15, 8, 1
HOST_FIELDS = dict(name="", host="", port=22, user="", auth="password", password="", key="", key_pass="", group="",
                   note="", command="", jump="", forwards="")


class VaultError(Exception):
    pass


class WrongPassword(VaultError):
    pass


def _b(x):
    return base64.b64encode(x).decode()


def _u(x):
    return base64.b64decode(x.encode())


def norm_email(email):
    return (email or "").strip().lower()


def vault_path(folder, email):
    tag = hashlib.sha256(norm_email(email).encode()).hexdigest()[:12]
    return os.path.join(folder, f"digonto-ssh-{tag}.vault")


def _key(password, salt, email):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt + norm_email(email).encode(), n=N, r=R, p=P, dklen=32,
                          maxmem=128 * 1024 * 1024)


class StoreError(VaultError):
    """the place where the vault lives cannot be reached right now"""


class LocalStore:
    """vault file in a folder on this computer (for example your SyncDrive)"""

    def __init__(self, folder, email):
        self.folder, self.email = folder, email
        self.path = vault_path(folder, email)
        self.pending = False

    def describe(self):
        return self.path

    def exists(self):
        return os.path.isfile(self.path)

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()

    def stamp(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def write(self, text):
        if not os.path.isdir(self.folder):
            raise StoreError("The folder does not exist.")
        tmp = self.path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def retry(self):
        return False


class SftpStore:
    """vault file on a server you can log in to with SSH (for example your VPN server). The server only ever sees encrypted data."""

    def __init__(self, host, port, user, password, key_text, key_pass, remote_dir, email, trust):
        self.host, self.port, self.user = host, int(port or 22), user
        self.password, self.key_text, self.key_pass = password, key_text, key_pass
        self.dir = (remote_dir or "digonto-sync").rstrip("/") or "/"
        self.name = os.path.basename(vault_path("", email))
        self.trust = trust                       # trust(hostport, fingerprint, changed) -> bool ; also remembers it
        self.client = None
        self.sftp = None
        self.pending = False

    def describe(self):
        return f"{self.user}@{self.host}:{self.dir}/{self.name}"

    @property
    def rpath(self):
        return self.dir + "/" + self.name if self.dir != "/" else "/" + self.name

    def _connect(self):
        if self.sftp is not None:
            try:
                self.sftp.stat(".")
                return self.sftp
            except Exception:  # noqa: BLE001
                self.close()
        import paramiko
        from .conn import fingerprint, load_key
        store = self

        class Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = fingerprint(key)
                ok, changed = store.trust(f"{store.host}:{store.port}", fp)
                if ok:
                    return
                raise paramiko.SSHException("Server key not trusted." if not changed else "The server key has changed - not connecting.")
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(Policy())
            kw = dict(hostname=self.host, port=self.port, username=self.user, timeout=8, banner_timeout=10, auth_timeout=15,
                      look_for_keys=False, allow_agent=False)
            if self.key_text:
                kw["pkey"] = load_key(self.key_text, self.key_pass)
            else:
                kw["password"] = self.password
            c.connect(**kw)
            self.client, self.sftp = c, c.open_sftp()
            self._mkdirs(self.dir)
            return self.sftp
        except Exception as e:  # noqa: BLE001
            self.close()
            raise StoreError(f"Cannot reach the vault server {self.host}: {e}") from None

    def _mkdirs(self, path):
        parts, cur = [p for p in path.split("/") if p], "/" if path.startswith("/") else ""
        for p in parts:
            cur = (cur.rstrip("/") + "/" + p) if cur else p
            try:
                self.sftp.stat(cur)
            except IOError:
                self.sftp.mkdir(cur)

    def close(self):
        try:
            if self.client:
                self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.client = self.sftp = None

    def exists(self):
        sf = self._connect()
        try:
            sf.stat(self.rpath)
            return True
        except IOError:
            return False

    def read(self):
        sf = self._connect()
        try:
            with sf.open(self.rpath, "rb") as f:
                return f.read().decode("utf-8")
        except IOError as e:
            raise StoreError(str(e)) from None

    def stamp(self):
        try:
            st = self._connect().stat(self.rpath)
            return (st.st_mtime, st.st_size)
        except IOError:
            return None

    def write(self, text):
        sf = self._connect()
        tmp = self.rpath + ".tmp"
        try:
            self._mkdirs(self.dir)
            with sf.open(tmp, "wb") as f:
                f.write(text.encode("utf-8"))
            sf.posix_rename(tmp, self.rpath)
        except IOError as e:
            raise StoreError(str(e)) from None

    def retry(self):
        return False


class CachedStore:
    """keeps a copy on this computer: works while the VPN is down, and re-uploads the vault if the server was reinstalled"""

    def __init__(self, primary, cache_path):
        self.primary, self.cache = primary, cache_path
        self.pending = False
        self.offline = False
        self.last = None

    def describe(self):
        return self.primary.describe() + ("   [offline - using the copy on this computer]" if self.offline else "")

    def _cache_read(self):
        with open(self.cache, "r", encoding="utf-8") as f:
            return f.read()

    def _cache_write(self, text):
        os.makedirs(os.path.dirname(self.cache), exist_ok=True)
        tmp = self.cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.cache)

    def exists(self):
        try:
            e = self.primary.exists()
            self.offline = False
            if not e and os.path.isfile(self.cache):
                self.pending = True             # server lost the file (reinstalled?) - upload our copy on next save/retry
                return True
            return e
        except StoreError:
            self.offline = True
            return os.path.isfile(self.cache)

    def read(self):
        if self.pending and os.path.isfile(self.cache):
            self.retry()
            if self.pending:
                return self._cache_read()
        try:
            text = self.primary.read()
            self._cache_write(text)
            self.offline = False
            return text
        except StoreError:
            self.offline = True
            if os.path.isfile(self.cache):
                return self._cache_read()
            raise

    def stamp(self):
        try:
            st = self.primary.stamp()
            self.offline = False
            return st
        except StoreError:
            self.offline = True
            return self.last

    def write(self, text):
        self._cache_write(text)
        try:
            self.primary.write(text)
            self.pending, self.offline = False, False
        except StoreError:
            self.pending, self.offline = True, True
        self.last = self.stamp()

    def retry(self):
        """called every few seconds: push local changes once the server is reachable again"""
        if not self.pending or not os.path.isfile(self.cache):
            return False
        try:
            self.primary.write(self._cache_read())
            self.pending = self.offline = False
            return True
        except StoreError:
            return False


def new_host(**kw):
    h = dict(HOST_FIELDS)
    h.update(kw)
    h["id"] = kw.get("id") or uuid.uuid4().hex[:12]
    h["mod"] = time.time()
    return h


class Vault:
    def __init__(self, store, email, password):
        self.store, self.email, self.password = store, norm_email(email), password
        self.salt = None
        self.key = None
        self.data = {"hosts": {}, "gone": {}, "known": {}, "ktime": {}, "kgone": {}, "updated": 0}
        self.mtime = 0

    # ---- create / open
    @classmethod
    def create(cls, where, email, password):
        if not norm_email(email) or "@" not in email:
            raise VaultError("Enter a valid e-mail address.")
        if len(password) < 8:
            raise VaultError("Use a password of at least 8 characters - it protects all your saved logins.")
        store = LocalStore(where, email) if isinstance(where, str) else where
        if isinstance(store, LocalStore) and not os.path.isdir(store.folder):
            raise VaultError("The folder does not exist.")
        if store.exists():
            raise VaultError("This e-mail already has an account here. Use Sign in.")
        v = cls(store, email, password)
        v.salt = os.urandom(16)
        v.key = _key(password, v.salt, email)
        v.save()
        return v

    @classmethod
    def open(cls, where, email, password):
        store = LocalStore(where, email) if isinstance(where, str) else where
        if not store.exists():
            raise VaultError("No account for this e-mail was found there. Check the location, or create the account.")
        v = cls(store, email, password)
        v._load(first=True)
        return v

    def _read(self):
        return json.loads(self.store.read())

    def _decrypt(self, doc, key=None):
        aad = f"digonto-ssh|{doc['v']}|{doc['salt']}".encode()
        try:
            raw = AESGCM(key or self.key).decrypt(_u(doc["nonce"]), _u(doc["ct"]), aad)
        except InvalidTag:
            raise WrongPassword("Wrong password (or the file is damaged).") from None
        return json.loads(raw.decode("utf-8"))

    def _load(self, first=False):
        doc = self._read()
        if first:
            self.salt = _u(doc["salt"])
            self.key = _key(self.password, self.salt, self.email)
        self.mtime = self.store.stamp()
        self._merge(self._decrypt(doc))

    def _merge(self, other):
        """per host: the newest change wins; deleted hosts stay deleted unless re-edited later"""
        mine, gone = self.data["hosts"], self.data["gone"]
        for hid, ts in other.get("gone", {}).items():
            gone[hid] = max(ts, gone.get(hid, 0))
        for hid, h in other.get("hosts", {}).items():
            if hid not in mine or h.get("mod", 0) > mine[hid].get("mod", 0):
                mine[hid] = h
        for hid in list(mine):
            if hid in gone and gone[hid] >= mine[hid].get("mod", 0):
                del mine[hid]
        d = self.data
        for k in ("ktime", "kgone", "known"):
            d.setdefault(k, {})
        for hp, ts in other.get("kgone", {}).items():
            d["kgone"][hp] = max(ts, d["kgone"].get(hp, 0))
        ot = other.get("ktime", {})
        for hp, fp in other.get("known", {}).items():
            if hp not in d["known"] or ot.get(hp, 0) > d["ktime"].get(hp, 0):
                d["known"][hp] = fp
                d["ktime"][hp] = ot.get(hp, 0)
        for hp in list(d["known"]):
            if hp in d["kgone"] and d["kgone"][hp] >= d["ktime"].get(hp, 0):
                del d["known"][hp]
        self.data["updated"] = max(self.data["updated"], other.get("updated", 0))

    # ---- sync
    @property
    def path(self):
        return self.store.describe()

    def changed_on_disk(self):
        try:
            self.store.retry()
            st = self.store.stamp()
            return st is not None and st != self.mtime
        except (OSError, VaultError):
            return False

    def refresh(self):
        before = json.dumps(self.data, sort_keys=True)
        self._load()
        return before != json.dumps(self.data, sort_keys=True)

    def save(self):
        """merge with whatever another computer wrote meanwhile, then write atomically"""
        if self.salt is not None and self.store.exists() and self.changed_on_disk():
            try:
                self._merge(self._decrypt(self._read()))
            except (OSError, ValueError, StoreError):
                pass
        self.data["updated"] = time.time()
        nonce = os.urandom(12)
        doc = {"v": 1, "kdf": "scrypt", "n": N, "r": R, "p": P, "salt": _b(self.salt), "nonce": _b(nonce)}
        aad = f"digonto-ssh|1|{doc['salt']}".encode()
        doc["ct"] = _b(AESGCM(self.key).encrypt(nonce, json.dumps(self.data, ensure_ascii=False).encode("utf-8"), aad))
        self.store.write(json.dumps(doc))
        self.mtime = self.store.stamp()

    def change_password(self, new_password):
        if len(new_password) < 8:
            raise VaultError("Use a password of at least 8 characters.")
        self.password = new_password
        self.salt = os.urandom(16)
        self.key = _key(new_password, self.salt, self.email)
        self.save()

    # ---- hosts
    def hosts(self):
        return sorted(self.data["hosts"].values(), key=lambda h: (h.get("group", "").lower(), h.get("name", "").lower()))

    def put(self, h):
        h["mod"] = time.time()
        self.data["hosts"][h["id"]] = h
        self.data["gone"].pop(h["id"], None)
        self.save()

    def delete(self, hid):
        self.data["hosts"].pop(hid, None)
        self.data["gone"][hid] = time.time()
        self.save()

    def known(self, hostport):
        return self.data["known"].get(hostport)

    def trust(self, hostport, fingerprint):
        self.data["known"][hostport] = fingerprint
        self.data.setdefault("ktime", {})[hostport] = time.time()
        self.data.setdefault("kgone", {}).pop(hostport, None)
        self.save()

    def known_all(self):
        return sorted(self.data["known"].items())

    def forget(self, hostport):
        self.data["known"].pop(hostport, None)
        self.data.setdefault("kgone", {})[hostport] = time.time()
        self.save()
