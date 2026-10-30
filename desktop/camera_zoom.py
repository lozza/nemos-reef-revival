"""Mouse-wheel zoom through the original bounded MapScroller camera setter."""
import math
import struct
from smoke_original import BASE

MAP_INSTANCE = '_ZN6IsoMap10m_InstanceE'
SET_ZOOM = '_ZN11MapScroller7setZoomEf'
GET_ZOOM = '_ZNK3sdr3til9IsoCamera13getZoomFactorEv'


def zoom(runtime, steps):
    if not math.isfinite(steps):
        raise ValueError('Wheel zoom must be finite')
    if not steps:
        return False
    machine = runtime.machine
    # Read the existing singleton, rather than getInstance(), which constructs
    # an uninitialized map if the player scrolls while the title screen loads.
    slot = BASE + runtime.elf.symbol(MAP_INSTANCE)['address']
    read_int = lambda address: struct.unpack('<I', machine.mem_read(address, 4))[0]
    map_pointer = read_int(slot)
    if not map_pointer:
        return False
    holder = read_int(map_pointer + 0x14c)  # IsoMap::getMapScroller, original ARM
    if not holder:
        return False
    scroller = read_int(holder)
    if not scroller or not read_int(scroller):
        return False
    camera = read_int(map_pointer + 0x98)
    if not camera:
        return False
    current = struct.unpack('<f', struct.pack('<I', runtime.call(GET_ZOOM, camera)))[0]
    if not math.isfinite(current) or current <= 0:
        return False
    target = current * 1.12 ** max(-8, min(8, steps))
    bits = struct.unpack('<I', struct.pack('<f', target))[0]
    # The original method applies its min/max limits and updates its animated
    # camera value. No synthetic finger presses or changes to touch state.
    runtime.call(SET_ZOOM, scroller, bits)
    return True
