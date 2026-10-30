"""Retain raw graphics arguments; format diagnostics only when consumed."""
from collections import deque


class GraphicsEvents(deque):
    def record(self, name, args):
        super().append((name, tuple(args)))

    def __iter__(self):
        for name, args in super().__iter__():
            yield {'name': name, 'arguments': [hex(value) for value in args]}
