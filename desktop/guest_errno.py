"""Error values for the original ARM/Linux ABI, independent of the host C runtime."""
import errno as host_errno

EPERM = 1
EINTR = 4
ENOENT = 2
EIO = 5
EBADF = 9
EAGAIN = 11
ENOMEM = 12
EACCES = 13
EBUSY = 16
EEXIST = 17
ENOTDIR = 20
EISDIR = 21
EINVAL = 22
ENOSPC = 28
EPIPE = 32
EDOM = 33
ERANGE = 34
EDEADLK = 35
EDEADLOCK = EDEADLK
ENOSYS = 38
ENOTEMPTY = 39
EOVERFLOW = 75
ENOTSOCK = 88
EMSGSIZE = 90
ENOPROTOOPT = 92
EPROTONOSUPPORT = 93
EOPNOTSUPP = 95
EAFNOSUPPORT = 97
EADDRINUSE = 98
EADDRNOTAVAIL = 99
ENETDOWN = 100
ENETUNREACH = 101
ECONNABORTED = 103
ECONNRESET = 104
ENOBUFS = 105
EISCONN = 106
ENOTCONN = 107
ETIMEDOUT = 110
ECONNREFUSED = 111
EHOSTUNREACH = 113
EALREADY = 114
EINPROGRESS = 115
EWOULDBLOCK = EAGAIN


def from_host(error):
    code = getattr(error, "errno", None)
    if code is None:
        return EINVAL
    name = host_errno.errorcode.get(code)
    if name not in globals():
        raise RuntimeError(f"host error {code} ({name}) needs an ARM errno mapping") from error
    return globals()[name]
