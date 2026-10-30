"""A virtual APK path plus a separate desktop data directory for guest stdio."""
from dataclasses import dataclass, field
import errno
from pathlib import Path
import struct
import zipfile

APK_PATH = "/desktop/nemo.apk"
DATA_PATH = "/desktop/data"
CACHE_PATH = DATA_PATH + "/.cache"


@dataclass
class Stream:
    file: object
    path: str
    eof: bool = False
    error: int = 0
    pushback: bytearray = field(default_factory=bytearray)


class GuestFiles:
    def __init__(self, apk, data):
        self.apk = Path(apk).resolve()
        self.data = Path(data).resolve()
        (self.data / ".cache" / "install").mkdir(parents=True, exist_ok=True)
        self.streams = {}
        self.next_stream = 1
        self.events = []
        self.asset_archive = None

    def read_asset(self, name):
        """Read a desktop platform asset without reparsing the APK each time."""
        if self.asset_archive is None:
            self.asset_archive = zipfile.ZipFile(self.apk)
        return self.asset_archive.read('assets/' + name)

    def resolve(self, name, writable=False):
        if name == APK_PATH:
            if writable:
                raise PermissionError(errno.EACCES, "APK is read-only", name)
            return self.apk
        if name != DATA_PATH and not name.startswith(DATA_PATH + "/"):
            raise FileNotFoundError(errno.ENOENT, "guest path is not mounted", name)
        if not writable and name.startswith(DATA_PATH + '/.cache/cache/music/'):
            # The original engine checks cached file existence before it asks
            # MediaPlayer to play music. Expose the external soundtrack here
            # read-only, without copying it into saves or changing the APK.
            from desktop_music import resolve_music_asset
            music = resolve_music_asset(name)
            if music is not None:
                return music
        relative = name[len(DATA_PATH):].lstrip("/")
        result = (self.data / relative).resolve()
        if not result.is_relative_to(self.data):
            raise PermissionError(errno.EACCES, "guest path leaves the desktop data mount", name)
        return result

    def open(self, name, mode):
        allowed = {"r", "rb", "r+", "rb+", "r+b", "w", "wb", "w+", "wb+", "w+b",
                   "a", "ab", "a+", "ab+", "a+b"}
        if mode not in allowed:
            raise OSError(errno.EINVAL, "unsupported stdio mode", mode)
        writable = mode[0] != "r" or "+" in mode
        path = self.resolve(name, writable)
        binary_mode = mode[0] + "b" + ("+" if "+" in mode else "")
        file = path.open(binary_mode)
        handle = self.next_stream
        self.next_stream += 1
        self.streams[handle] = Stream(file, name)
        self.events.append({"operation": "open", "path": name, "mode": mode})
        return handle

    def stream(self, handle):
        if handle not in self.streams:
            raise OSError(errno.EBADF, "unknown or closed guest stream")
        return self.streams[handle]

    def close(self, handle):
        stream = self.stream(handle)
        stream.file.close()
        del self.streams[handle]
        self.events.append({"operation": "close", "path": stream.path})

    def close_all(self):
        for handle in list(self.streams):
            self.close(handle)
        if self.asset_archive is not None:
            self.asset_archive.close()
            self.asset_archive = None

    def mkdir(self, name, mode):
        if name.rstrip("/") in ("", "/desktop"):
            raise FileExistsError(errno.EEXIST, "virtual directory already exists", name)
        self.resolve(name, writable=True).mkdir(mode=mode)

    def stat_arm32(self, name):
        """Legacy Bionic ARM struct stat: 104 bytes, kernel stat64 layout.

        Source layout: AOSP android-4.4_r1 libc/include/sys/stat.h (non-MIPS).
        ARM aligns the 64-bit size, block count and final inode to eight bytes.
        """
        self.events.append({"operation": "stat", "path": name})
        virtual = name.rstrip("/") in ("", "/desktop")
        value = self.data.stat() if virtual else self.resolve(name).stat()
        data = bytearray(104)
        inode = value.st_ino & 0xffffffffffffffff
        struct.pack_into("<Q", data, 0, value.st_dev & 0xffffffffffffffff)
        struct.pack_into("<5I", data, 12, inode & 0xffffffff, value.st_mode,
                         value.st_nlink & 0xffffffff, getattr(value, "st_uid", 0), getattr(value, "st_gid", 0))
        struct.pack_into("<Q", data, 32, getattr(value, "st_rdev", 0))
        struct.pack_into("<q", data, 48, value.st_size)
        struct.pack_into("<I", data, 56, getattr(value, "st_blksize", 4096))
        struct.pack_into("<Q", data, 64, getattr(value, "st_blocks", (value.st_size + 511) // 512))
        for offset, nanoseconds in ((72, value.st_atime_ns), (80, value.st_mtime_ns), (88, value.st_ctime_ns)):
            struct.pack_into("<II", data, offset, (nanoseconds // 1_000_000_000) & 0xffffffff, nanoseconds % 1_000_000_000)
        struct.pack_into("<Q", data, 96, inode)
        return bytes(data)
