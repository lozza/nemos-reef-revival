"""Revival gameplay changes applied to original client memory on both desktop OSes."""
from smoke_original import BASE

# Same original 1.8.1 constructor instruction as the Android patcher. This only
# disables distance-based fading; it neither grants fish nor alters save files.
FISH_VISIBILITY = (0x2a5384, bytes.fromhex('a354'), bytes.fromhex('c046'))


def install(runtime):
    if getattr(runtime, 'gameplay_compatibility', None):
        return
    at, old, new = FISH_VISIBILITY
    address = BASE + at
    if bytes(runtime.machine.mem_read(address, len(old))) != old:
        raise RuntimeError('Fish visibility patch requires the original Android 1.8.1 client')
    runtime.machine.mem_write(address, new)
    runtime.machine.ctl_remove_cache(address, address + len(new))
    runtime.gameplay_compatibility = {'fish_visible_when_zoomed_out': True,
                                     'patches': [hex(address)]}
