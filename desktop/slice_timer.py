"""Reusable emulation watchdog; avoids a native timer thread per short slice.

Only emu_stop runs on the watchdog. All guest execution, registers, hooks and
contexts stay on the owner thread. Disarm synchronizes with stop under one lock,
so an expired generation cannot interrupt the following slice. Repeated stops
also cover a deadline expiring just before emu_start resets its stop flag.
"""
from contextlib import contextmanager
import threading
import time
import weakref


class SliceTimer:
    def __init__(self, machine):
        self.machine = weakref.ref(machine)
        self.condition = threading.Condition()
        self.deadline = None
        self.closed = False
        self.stops = 0
        self.error = None
        self.thread = threading.Thread(target=self._run, name='Nemo slice timer', daemon=True)
        self.thread.start()
        self.finalizer = weakref.finalize(machine, self.close)

    @contextmanager
    def limit(self, deadline):
        with self.condition:
            if self.closed:
                raise RuntimeError('Emulation timer is closed')
            if self.error is not None:
                raise RuntimeError('Emulation timer failed') from self.error
            if self.deadline is not None:
                raise RuntimeError('Nested emulation timer limit')
            self.deadline = deadline
            self.condition.notify()
        try:
            yield
        finally:
            with self.condition:
                self.deadline = None
                self.condition.notify()

    def _run(self):
        with self.condition:
            while not self.closed:
                if self.deadline is None:
                    self.condition.wait()
                    continue
                delay = self.deadline - time.monotonic()
                if delay > 0:
                    self.condition.wait(delay)
                    continue
                machine = self.machine()
                if machine is None:
                    return
                try:
                    machine.emu_stop()
                    self.stops += 1
                except BaseException as error:
                    self.error = error
                    return
                finally:
                    del machine
                # Do not spin if the owner has not entered the emulator yet.
                self.condition.wait(0.001)

    def close(self):
        with self.condition:
            self.closed = True
            self.deadline = None
            self.condition.notify()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
