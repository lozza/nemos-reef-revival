"""Bounded decoded audio reuse and device cleanup outside the game callback."""
from collections import OrderedDict
import hashlib
import queue
import threading


class DecodedAudioCache:
    limit = 64*1024*1024

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0

    def decode(self, source, decoder):
        key = hashlib.sha256(source).digest()
        if key in self.entries:
            self.entries.move_to_end(key)
            return self.entries[key]
        pcm = decoder(source)
        if pcm and len(pcm) <= self.limit:
            while self.entries and self.bytes+len(pcm) > self.limit:
                _, old = self.entries.popitem(last=False)
                self.bytes -= len(old)
            self.entries[key] = pcm
            self.bytes += len(pcm)
        return pcm


class AudioCleanup:
    def __init__(self):
        self.jobs = queue.Queue(maxsize=64)
        self.errors = []
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='Nemo audio cleanup', daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            device = self.jobs.get()
            try:
                if device is None:
                    return
                try:
                    device.close()
                except Exception as error:
                    self.errors.append(str(error))
                    del self.errors[:-16]
            finally:
                self.jobs.task_done()

    def release(self, device):
        if self.closed:
            raise RuntimeError('Audio cleanup is already closed')
        self.jobs.put(device)

    def finish(self):
        if not self.closed:
            self.closed = True
            self.jobs.put(None)
            self.jobs.join()
            self.thread.join()
