"""Bounded snapshots of revision-controlled shadow VBOs, never guest memory."""
from collections import OrderedDict
import ctypes
import struct


class Snapshot:
    def __init__(self, source, rows):
        self.source = source
        self.rows = tuple(rows)
        self.values = {}
        self.backings = {}

    def decoded(self, count, kind):
        key = count, kind
        if key not in self.values:
            fmt = {0x1400: 'b', 0x1401: 'B', 0x1402: 'h', 0x1403: 'H', 0x1406: 'f', 0x140c: 'i'}[kind]
            decoder = struct.Struct('<' + fmt * count)
            values = tuple(decoder.unpack(row) for row in self.rows)
            if kind == 0x140c:
                values = tuple(tuple(value / 65536.0 for value in row) for row in values)
            self.values[key] = values
        return self.values[key]

    def backing(self, count, kind):
        key = count, kind
        if key not in self.backings:
            if kind == 0x140c:
                encoder = struct.Struct('<' + 'f' * count)
                raw = b''.join(encoder.pack(*row) for row in self.decoded(count, kind))
            else:
                raw = b''.join(self.rows)
            self.backings[key] = ctypes.create_string_buffer(raw)
        return self.backings[key]


class VboSnapshotCache:
    """Uploads/deletions must invalidate the buffer before its shadow changes."""
    max_entries = 2048
    max_bytes = 8 * 1024 * 1024

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0
        self.hits = self.misses = 0

    def invalidate(self, buffer):
        for key in [key for key in self.entries if key[0] == buffer]:
            _, cost = self.entries.pop(key)
            self.bytes -= cost

    def read(self, reader, buffer, source, indices, pointer, size, stride):
        if isinstance(indices, range):
            sequence = ('range', indices.start, indices.stop, indices.step)
        else:
            sequence = tuple(indices)
        key = buffer, pointer, size, stride, sequence
        cached = self.entries.get(key)
        if cached is not None and cached[0].source is source:
            self.entries.move_to_end(key)
            self.hits += 1
            return cached[0]
        self.misses += 1
        snapshot = Snapshot(source, reader.read(indices, pointer, size, stride, source))
        # Reserve for rows, decoded tuples, converted buffers, and index keys.
        cost = 256 + len(indices) * (8 * size + 256)
        if cost <= self.max_bytes:
            old = self.entries.pop(key, None)
            if old:
                self.bytes -= old[1]
            while self.entries and (len(self.entries) >= self.max_entries or self.bytes + cost > self.max_bytes):
                _, (_, removed_cost) = self.entries.popitem(last=False)
                self.bytes -= removed_cost
            self.entries[key] = snapshot, cost
            self.bytes += cost
        return snapshot
