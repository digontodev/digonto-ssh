"""Port forwarding for one SSH connection (like ssh -L, -R and -D).

One rule per line:
    L 8080:localhost:80            this computer :8080  ->  through the server  ->  localhost:80 (as seen from the server)
    L 0.0.0.0:8080:db.local:5432   same, listening on all network cards
    R 9000:localhost:3000          the server :9000     ->  through this computer ->  localhost:3000
    D 1080                         a SOCKS5 proxy on this computer :1080 (browsers can use it to go out through the server)
Lines starting with # are ignored.
"""
import select
import socket
import struct
import threading


class RuleError(ValueError):
    pass


def parse(text):
    rules = []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        kind = parts[0].upper()
        spec = parts[1].strip() if len(parts) > 1 else ""
        bits = spec.split(":")
        try:
            if kind == "D":
                if len(bits) == 1:
                    rules.append(("D", "127.0.0.1", int(bits[0]), "", 0))
                elif len(bits) == 2:
                    rules.append(("D", bits[0], int(bits[1]), "", 0))
                else:
                    raise ValueError
            elif kind in ("L", "R"):
                if len(bits) == 3:
                    bind, bport, host, port = ("127.0.0.1" if kind == "L" else ""), int(bits[0]), bits[1], int(bits[2])
                elif len(bits) == 4:
                    bind, bport, host, port = bits[0], int(bits[1]), bits[2], int(bits[3])
                else:
                    raise ValueError
                rules.append((kind, bind, bport, host, port))
            else:
                raise ValueError
        except ValueError:
            raise RuleError(f"Port forwarding, line {n}: \"{raw.strip()}\" is not understood. "
                            f"Examples:  L 8080:localhost:80   R 9000:localhost:3000   D 1080") from None
        for v in (rules[-1][2], rules[-1][4]):
            if not 0 <= v <= 65535:
                raise RuleError(f"Port forwarding, line {n}: port numbers go from 1 to 65535.")
    return rules


def _pipe(a, b):
    """copy data both ways between two sockets / channels until one side closes"""
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 1.0)
            if a in r:
                d = a.recv(32768)
                if not d:
                    break
                b.sendall(d)
            if b in r:
                d = b.recv(32768)
                if not d:
                    break
                a.sendall(d)
    except Exception:  # noqa: BLE001
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


class Forwarder:
    def __init__(self, client, rules, log=None):
        self.client, self.rules = client, rules
        self.log = log or (lambda m: None)
        self.socks = []
        self.stop = False

    def start(self):
        t = self.client.get_transport()
        for kind, bind, bport, host, port in self.rules:
            try:
                if kind == "R":
                    t.request_port_forward(bind, bport, handler=self._remote_handler(host, port))
                    self.log(f"Forward R: server port {bport or 'any'} -> {host}:{port}")
                else:
                    s = socket.socket()
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((bind, bport))
                    s.listen(20)
                    s.settimeout(0.5)
                    self.socks.append(s)
                    threading.Thread(target=self._accept, args=(s, kind, host, port), daemon=True).start()
                    self.log(f"Forward {kind}: {bind}:{bport}" + (f" -> {host}:{port}" if kind == "L" else " (SOCKS5)"))
            except Exception as e:  # noqa: BLE001
                self.log(f"Forward {kind} {bport} failed: {e}")

    def _remote_handler(self, host, port):
        def handler(chan, origin, server):
            def go():
                try:
                    s = socket.create_connection((host, port), timeout=8)
                except OSError:
                    chan.close()
                    return
                _pipe(chan, s)
            threading.Thread(target=go, daemon=True).start()
        return handler

    def _accept(self, srv, kind, host, port):
        while not self.stop:
            try:
                c, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c, addr, kind, host, port), daemon=True).start()

    def _open(self, host, port, addr):
        return self.client.get_transport().open_channel("direct-tcpip", (host, port), addr)

    def _serve(self, c, addr, kind, host, port):
        try:
            if kind == "L":
                ch = self._open(host, port, addr)
            else:
                ch = self._socks(c, addr)
                if ch is None:
                    c.close()
                    return
            _pipe(c, ch)
        except Exception:  # noqa: BLE001
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def _socks(self, c, addr):
        c.settimeout(10)
        hdr = c.recv(2)
        if len(hdr) < 2 or hdr[0] != 5:
            return None
        c.recv(hdr[1])
        c.sendall(b"\x05\x00")
        req = c.recv(4)
        if len(req) < 4 or req[1] != 1:
            c.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            return None
        if req[3] == 1:
            host = socket.inet_ntoa(c.recv(4))
        elif req[3] == 3:
            n = c.recv(1)[0]
            host = c.recv(n).decode("idna", "replace")
        elif req[3] == 4:
            host = socket.inet_ntop(socket.AF_INET6, c.recv(16))
        else:
            return None
        port = struct.unpack(">H", c.recv(2))[0]
        try:
            ch = self._open(host, port, addr)
        except Exception:  # noqa: BLE001
            c.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            return None
        c.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        c.settimeout(None)
        return ch

    def close(self):
        self.stop = True
        for s in self.socks:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self.socks = []
