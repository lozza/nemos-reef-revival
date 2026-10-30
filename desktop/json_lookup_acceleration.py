"""Index the bundled cJSON read APIs; original code still parses and mutates.

Every exported tree mutation/deletion entry invalidates the shared index. Keys
use Bionic C-locale byte case folding and retain the first duplicate match.
"""
import struct

from smoke_original import BASE, uc

GETTERS = {"cJSON_GetObjectItem", "cJSON_GetArrayItem", "cJSON_GetArraySize"}
MUTATIONS = ("cJSON_Add", "cJSON_Delete", "cJSON_Detach", "cJSON_Replace",
             "cJSON_Insert", "cJSON_Set", "cJSON_InitHooks")


def install(runtime):
    if hasattr(runtime, "json_lookup_hooks"):
        return
    runtime.json_lookup_acceleration = True
    runtime.json_lookup_hooks = []
    runtime.json_lookup_cache = {}
    runtime.json_lookup_stats = {"calls": 0, "builds": 0, "nodes": 0, "invalidations": 0}

    def invalidate(machine, address, size, name):
        if runtime.json_lookup_cache:
            runtime.json_lookup_cache.clear()
            runtime.json_lookup_stats["invalidations"] += 1

    def lookup(machine, address, size, name):
        if not runtime.json_lookup_acceleration:
            return
        root, argument, _, _ = runtime.imported_args()
        if not root:
            return  # Preserve this old library's NULL-root dereference.
        head = runtime.word(root + 8)
        entry = runtime.json_lookup_cache.get(root)
        if entry is None or entry["head"] != head:
            children, seen = [], set()
            node = head
            while node:
                if node in seen or len(children) >= 1_000_000:
                    raise RuntimeError("invalid or oversized cJSON child chain")
                seen.add(node)
                children.append(node)
                node = runtime.word(node)
            entry = {"head": head, "children": children, "names": None}
            runtime.json_lookup_cache[root] = entry
            runtime.json_lookup_stats["builds"] += 1
            runtime.json_lookup_stats["nodes"] += len(children)
        children = entry["children"]
        if name == "cJSON_GetArraySize":
            result = len(children)
        elif name == "cJSON_GetArrayItem":
            index = max(0, struct.unpack("<i", struct.pack("<I", argument))[0])
            result = children[index] if index < len(children) else 0
        else:
            if entry["names"] is None:
                names = {}
                for child in children:
                    pointer = runtime.word(child + 32)
                    key = runtime.bytestring(pointer).lower() if pointer else None
                    names.setdefault(key, child)
                entry["names"] = names
            key = runtime.bytestring(argument).lower() if argument else None
            result = entry["names"].get(key, 0)
        runtime.json_lookup_stats["calls"] += 1
        runtime.return_value(result)

    registered = set()
    for symbol in runtime.elf.symbols:
        name = symbol["name"]
        if not symbol["section"] or symbol["type"] != 2:
            continue
        callback = lookup if name in GETTERS else invalidate if name.startswith(MUTATIONS) else None
        if callback is None:
            continue
        address = BASE + (symbol["address"] & ~1)
        if address in registered:
            continue
        registered.add(address)
        runtime.json_lookup_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, callback,
                                                                 user_data=name, begin=address, end=address))
