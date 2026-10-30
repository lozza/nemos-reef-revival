"""Leave steady time between complete game frames without skipping any work."""
import time


def wait_for_frame(started,fps=30):
    remaining = 1/fps-(time.perf_counter()-started)
    if remaining > 0:
        time.sleep(remaining)
