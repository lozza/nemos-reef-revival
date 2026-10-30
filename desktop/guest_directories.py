"""Mounted directory iteration with Bionic's 64-bit-inode dirent fields."""
import stat
import struct

import guest_errno as errno


def call(runtime, name, args):
    if runtime.files is None:
        raise RuntimeError("directory access requires the desktop filesystem mount")
    if not hasattr(runtime, "directory_streams"):
        runtime.directory_streams = {}
    streams = runtime.directory_streams
    pointer = args[0]
    try:
        if name == "opendir":
            path_name = runtime.string(pointer)
            path = runtime.files.resolve(path_name)
            # Listing snapshots names; it does not walk symlink targets.
            entries = [(".", path), ("..", path.parent)]
            entries += [(entry.name, entry) for entry in sorted(path.iterdir())]
            handle = runtime.heap.malloc(16)
            buffer = runtime.heap.malloc(280)
            if not handle or not buffer:
                if handle:
                    runtime.heap.free(handle)
                if buffer:
                    runtime.heap.free(buffer)
                raise OSError(12, "guest directory allocation failed")
            streams[handle] = {"entries": entries, "position": 0, "buffer": buffer}
            runtime.files.events.append({"operation": name, "path": path_name})
            return handle
        if pointer not in streams:
            raise OSError(9, "unknown directory stream")
        stream = streams[pointer]
        if name == "closedir":
            runtime.heap.free(stream["buffer"])
            runtime.heap.free(pointer)
            del streams[pointer]
            return 0
        if name != "readdir":
            raise RuntimeError(f"unimplemented directory call {name}")
        if stream["position"] == len(stream["entries"]):
            return 0
        entry_name, path = stream["entries"][stream["position"]]
        encoded = entry_name.encode("utf-8")
        if len(encoded) > 255:
            raise RuntimeError("directory entry exceeds the guest name field")
        info = path.lstat()
        kind = 10 if stat.S_ISLNK(info.st_mode) else 4 if stat.S_ISDIR(info.st_mode) else 8 if stat.S_ISREG(info.st_mode) else 0
        stream["position"] += 1
        length = (19 + len(encoded) + 1 + 7) & ~7
        record = bytearray(280)
        struct.pack_into("<QqHB", record, 0, info.st_ino & ((1 << 64) - 1), stream["position"], length, kind)
        record[19:19 + len(encoded)] = encoded
        runtime.machine.mem_write(stream["buffer"], bytes(record))
        return stream["buffer"]
    except OSError as error:
        runtime.word(runtime.errno_address, errno.from_host(error))
        return -1 if name == "closedir" else 0
