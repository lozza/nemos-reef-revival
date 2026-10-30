"""Gather client vertex arrays with bounded, per-draw guest memory reads.

No data survives a draw: original code can update client arrays at any time.
Sparse arrays and spans containing unmapped padding retain individual reads.
"""
from smoke_original import uc


class VertexReader:
    max_span = 4 * 1024 * 1024

    def __init__(self, machine):
        self.machine = machine
        self.enabled = True
        self.reads = 0
        self.batches = 0
        self.fallbacks = 0

    def read(self, indices, pointer, size, stride, source=None):
        if not indices:
            return []
        if source is not None:
            # Shadow VBOs already live in Python; avoid a redundant span copy.
            rows = []
            for index in indices:
                offset = pointer + index * stride
                if offset < 0 or offset + size > len(source):
                    raise ValueError('vertex buffer access out of bounds')
                rows.append(bytes(source[offset:offset + size]))
            return rows
        if self.enabled and len(indices) > 1:
            if isinstance(indices, range):
                first, last = min(indices[0], indices[-1]), max(indices[0], indices[-1])
            else:
                first, last = min(indices), max(indices)
            span = (last - first) * stride + size
            if 0 < span <= min(self.max_span, len(indices) * size * 4):
                try:
                    self.reads += 1
                    raw = bytes(self.machine.mem_read(pointer + first * stride, span))
                except uc.UcError:
                    # Only padding may be unmapped; preserve valid sparse rows.
                    self.fallbacks += 1
                else:
                    self.batches += 1
                    return [raw[(index - first) * stride:(index - first) * stride + size]
                            for index in indices]
        rows = []
        for index in indices:
            self.reads += 1
            rows.append(bytes(self.machine.mem_read(pointer + index * stride, size)))
        return rows
