"""ARM/Linux TCP socket ABI backed by a configured local desktop server.

Guest DNS names are recorded and resolved to the selected loopback endpoint.
The original libcurl still generates/parses HTTP and invokes its ARM callbacks.
This transport does not contact the retired services named inside the APK.
"""
import select
import socket
import struct
import time
from urllib.parse import urlsplit

import guest_errno as errno

IMPORTS = {"getaddrinfo", "freeaddrinfo", "socket", "connect", "send", "recv", "poll",
           "close", "shutdown", "fcntl", "getsockopt", "setsockopt", "getpeername", "getsockname", "gethostname",
           "inet_pton", "inet_ntop"}
WINDOWS_ERRORS = {10004: errno.EINTR, 10009: errno.EBADF, 10013: errno.EACCES,
                  10022: errno.EINVAL, 10035: errno.EAGAIN, 10036: errno.EINPROGRESS,
                  10037: errno.EALREADY, 10038: errno.ENOTSOCK, 10040: errno.EMSGSIZE,
                  10042: errno.ENOPROTOOPT, 10043: errno.EPROTONOSUPPORT,
                  10045: errno.EOPNOTSUPP, 10047: errno.EAFNOSUPPORT,
                  10048: errno.EADDRINUSE, 10049: errno.EADDRNOTAVAIL,
                  10050: errno.ENETDOWN, 10051: errno.ENETUNREACH,
                  10053: errno.ECONNABORTED, 10054: errno.ECONNRESET,
                  10055: errno.ENOBUFS, 10056: errno.EISCONN, 10057: errno.ENOTCONN,
                  10058: errno.EPIPE, 10060: errno.ETIMEDOUT,
                  10061: errno.ECONNREFUSED, 10065: errno.EHOSTUNREACH}
# Guest errnos that show the server is actually unreachable. Nonblocking readiness
# (EAGAIN/EINPROGRESS/EALREADY) and ENOTCONN from a pending connect are not failures.
TRANSPORT_FAILURES = {errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE,
                      errno.ETIMEDOUT, errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH}
CONNECTED_CACHE_SECONDS = 1.0


def error_number(error):
    if isinstance(error, TimeoutError):
        return errno.ETIMEDOUT
    code = error if isinstance(error, int) else getattr(error, "winerror", None) or getattr(error, "errno", None)
    if not code:
        return 0
    if code in WINDOWS_ERRORS:
        return WINDOWS_ERRORS[code]
    return errno.from_host(OSError(code, "socket"))


class DesktopNetwork:
    def __init__(self, origin="http://127.0.0.1:18081"):
        url = urlsplit(origin)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path not in ("", "/") or url.query or url.fragment or url.username:
            raise ValueError("desktop network requires an HTTP origin on 127.0.0.1")
        self.endpoint = ("127.0.0.1", url.port or 80)
        self.origin = f"http://127.0.0.1:{self.endpoint[1]}"
        self.sockets = {}
        self.next_descriptor = 1000
        self.addresses = {}
        self.events = []
        self.clock = time.monotonic
        self.connected_cache = None  # (checked_at, result)

    def connected(self):
        # The runtime polls this every frame; a fresh TCP probe each time costs ~20 ms.
        now = self.clock()
        if self.connected_cache:
            checked_at, result = self.connected_cache
            if 0 <= now - checked_at < CONNECTED_CACHE_SECONDS:
                return result
        self.connected_cache = None
        try:
            with socket.create_connection(self.endpoint, timeout=0.25):
                result = True
        except OSError:
            result = False
        self.connected_cache = (now, result)
        return result

    def invalidate_connected(self, mapped=None):
        if mapped is None or mapped in TRANSPORT_FAILURES:
            self.connected_cache = None

    def close_all(self):
        self.connected_cache = None
        for item in self.sockets.values():
            item.close()
        self.sockets.clear()

    @staticmethod
    def sockaddr(address):
        host, port = address[:2]
        return struct.pack("<H", 2) + struct.pack(">H", port) + socket.inet_aton(host) + bytes(8)

    def call(self, runtime, name, args):
        a, b, c, d = args[:4]
        try:
            if name in ("inet_pton", "inet_ntop"):
                if a not in (2, 10):
                    runtime.word(runtime.errno_address, errno.EAFNOSUPPORT)
                    return -1 if name == "inet_pton" else 0
                family = socket.AF_INET if a == 2 else socket.AF_INET6
                if name == "inet_pton":
                    try:
                        value = socket.inet_pton(family, runtime.string(b))
                    except OSError:
                        return 0  # Syntactically invalid address, not a DNS failure.
                    runtime.machine.mem_write(c, value)
                    return 1
                value = socket.inet_ntop(family, bytes(runtime.machine.mem_read(b, 4 if a == 2 else 16))).encode() + b"\0"
                if d < len(value):
                    runtime.word(runtime.errno_address, errno.ENOSPC)
                    return 0
                runtime.machine.mem_write(c, value)
                return c
            if name == "getaddrinfo":
                node = runtime.string(a) if a else ""
                service = runtime.string(b) if b else "0"
                flags, family, kind, protocol = struct.unpack("<4I", runtime.machine.mem_read(c, 16)) if c else (0, 0, 0, 0)
                if family not in (0, 2):
                    return 5  # Bionic EAI_FAMILY, not a negative glibc code.
                if kind not in (0, 1) or protocol not in (0, 6):
                    return 10
                address = runtime.allocate_bytes(self.sockaddr(self.endpoint))
                canonical = runtime.allocate_bytes(node.encode() + b"\0") if flags & 2 else 0
                info = runtime.allocate_bytes(struct.pack("<8I", flags, 2, 1, 6, 16, canonical, address, 0))
                self.addresses[info] = [info, address] + ([canonical] if canonical else [])
                runtime.word(d, info)
                self.events.append({"operation": "resolve", "guest_host": node, "guest_service": service,
                                    "desktop_endpoint": list(self.endpoint)})
                return 0
            if name == "freeaddrinfo":
                if a not in self.addresses:
                    raise RuntimeError("unknown guest addrinfo allocation")
                for pointer in self.addresses.pop(a):
                    runtime.heap.free(pointer)
                return 0
            if name == "gethostname":
                value = b"localhost\0"
                if b < len(value):
                    raise OSError(22, "hostname buffer is too small")
                runtime.machine.mem_write(a, value)
                return 0
            if name == "socket":
                if a != 2 or b != 1 or c not in (0, 6):
                    runtime.word(runtime.errno_address, errno.EAFNOSUPPORT)
                    return -1
                item = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                descriptor = self.next_descriptor
                self.next_descriptor += 1
                self.sockets[descriptor] = item
                return descriptor
            if name == "poll":
                return self.poll(runtime, a, b, c)
            if a not in self.sockets:
                runtime.word(runtime.errno_address, errno.EBADF)
                return -1
            item = self.sockets[a]
            if name == "connect":
                if c < 16:
                    raise OSError(22, "short guest sockaddr")
                raw = bytes(runtime.machine.mem_read(b, 16))
                if struct.unpack_from("<H", raw)[0] != 2:
                    runtime.word(runtime.errno_address, errno.EAFNOSUPPORT)
                    return -1
                # Route numeric addresses as well as DNS results to this backend.
                code = item.connect_ex(self.endpoint)
                mapped = error_number(code)
                if mapped == errno.EAGAIN:
                    mapped = errno.EINPROGRESS
                self.invalidate_connected(mapped)
                self.events.append({"operation": "connect", "descriptor": a, "error": mapped})
                if mapped:
                    runtime.word(runtime.errno_address, mapped)
                    return -1
                return 0
            if name == "fcntl":
                if b == 3:
                    return 2 | (0 if item.getblocking() else 0x800)
                if b == 4 and not c & ~(2 | 0x800):
                    item.setblocking(not bool(c & 0x800))
                    return 0
                if b == 1:
                    return int(not item.get_inheritable())
                if b == 2 and c in (0, 1):
                    item.set_inheritable(not bool(c))
                    return 0
                raise RuntimeError(f"unimplemented socket fcntl operation {b}/{c:#x}")
            if name in ("send", "recv"):
                if c > 64 * 1024 * 1024:
                    raise RuntimeError("socket transfer exceeds guest memory budget")
                if d & ~(0x4000 if name == "send" else 2):
                    raise RuntimeError(f"unimplemented {name} flags {d:#x}")
                if name == "send":
                    count = item.send(bytes(runtime.machine.mem_read(b, c)))
                else:
                    value = item.recv(c, socket.MSG_PEEK if d & 2 else 0)
                    if value:
                        runtime.machine.mem_write(b, value)
                    count = len(value)
                self.events.append({"operation": name, "descriptor": a, "bytes": count})
                return count
            if name == "close":
                item.close()
                del self.sockets[a]
                return 0
            if name == "shutdown":
                item.shutdown(b)
                return 0
            if name in ("getpeername", "getsockname"):
                value = self.sockaddr(item.getpeername() if name == "getpeername" else item.getsockname())
                capacity = runtime.word(c)
                runtime.machine.mem_write(b, value[:capacity])
                runtime.word(c, len(value))
                return 0
            if name in ("getsockopt", "setsockopt"):
                length_arg = runtime.imported_args(5)[4]
                if b == 1:
                    level = socket.SOL_SOCKET
                    options = {3: socket.SO_TYPE, 4: socket.SO_ERROR, 7: socket.SO_SNDBUF,
                               8: socket.SO_RCVBUF, 9: socket.SO_KEEPALIVE}
                elif b == 6:
                    level, options = socket.IPPROTO_TCP, {1: socket.TCP_NODELAY}
                else:
                    raise RuntimeError(f"unimplemented socket option level {b}")
                if c not in options:
                    raise RuntimeError(f"unimplemented socket option {b}/{c}")
                if name == "getsockopt":
                    if runtime.word(length_arg) < 4:
                        raise OSError(22, "short socket-option output")
                    value = item.getsockopt(level, options[c])
                    if b == 1 and c == 4:
                        value = error_number(value)
                        self.invalidate_connected(value)
                    runtime.word(d, value)
                    runtime.word(length_arg, 4)
                else:
                    if length_arg != 4:
                        raise RuntimeError("non-integer socket option not implemented")
                    item.setsockopt(level, options[c], runtime.word(d))
                return 0
            raise RuntimeError(f"unimplemented socket import {name}")
        except OSError as error:
            mapped = error_number(error)
            self.invalidate_connected(mapped)
            runtime.word(runtime.errno_address, mapped)
            return -1

    def poll(self, runtime, pointer, count, timeout):
        timeout = struct.unpack("<i", struct.pack("<I", timeout))[0]
        if count > 1024 or not 0 <= timeout <= 30_000:
            raise RuntimeError("unbounded or oversized guest poll not implemented")
        rows = []
        reads, writes, errors = [], [], []
        invalid = False
        for index in range(count):
            descriptor, events, _ = struct.unpack("<ihh", runtime.machine.mem_read(pointer + index * 8, 8))
            item = self.sockets.get(descriptor)
            revents = 0x20 if descriptor >= 0 and item is None else 0
            invalid |= bool(revents)
            rows.append((descriptor, events, item, revents))
            if item:
                if events & 1:
                    reads.append(item)
                if events & 4:
                    writes.append(item)
                errors.append(item)
        if reads or writes or errors:
            readable, writable, exceptional = select.select(reads, writes, errors, 0 if invalid else timeout / 1000)
        else:
            if not invalid:
                time.sleep(timeout / 1000)
            readable, writable, exceptional = [], [], []
        ready = 0
        for index, (descriptor, events, item, revents) in enumerate(rows):
            if item in readable:
                revents |= 1
            if item in writable:
                revents |= 4
            if item in exceptional:
                revents |= 8
            runtime.machine.mem_write(pointer + index * 8 + 6, struct.pack("<h", revents))
            ready += bool(revents)
        return ready
