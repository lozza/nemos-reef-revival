"""Index the original libzip directory for lookups on unmodified archives.

The original parser and decompressor still read the APK. Changed entries use the
original search; directory changes and archive destruction invalidate indexes.
"""
import struct
from smoke_original import BASE, uc


def has_directory_changes(raw):
    """Check both complete 32-bit fields without a Python loop per ZIP entry."""
    words = memoryview(raw).cast('I')
    return any(words[::5]) or any(words[2::5])


def install(runtime):
    if hasattr(runtime, 'zip_lookup_hooks'):
        return
    runtime.zip_lookup_acceleration = True
    runtime.zip_lookup_cache = {}
    runtime.zip_lookup_stats = {'calls': 0, 'indexes': 0, 'invalidations': 0,
                                'temporary_dirents': 0}
    runtime.zip_lookup_hooks = []

    def invalidate(machine, address, size, context):
        cache = runtime.zip_lookup_cache
        if context in ('_zip_dirent_read', '_zip_dirent_finalize'):
            # These functions also operate on temporary local-file headers.
            # Only a dirent overlapping a cached central directory changes
            # that index. Clearing every index for a temporary header made
            # ordinary asset reads repeatedly rebuild thousands of names.
            pointer = runtime.imported_args(1)[0]
            affected = [key for key in cache
                        if pointer < key[2] + key[3] * 60 and pointer + 60 > key[2]]
            if not affected:
                runtime.zip_lookup_stats['temporary_dirents'] += 1
                return
            for key in affected:
                del cache[key]
        else:
            if not cache:
                return
            cache.clear()
        runtime.zip_lookup_stats['invalidations'] += 1

    def lookup(machine, address, size, context):
        if not runtime.zip_lookup_acceleration:
            return
        archive, query, flags, error = runtime.imported_args()
        if not archive or not query or flags & ~11:
            return
        directory = runtime.word(archive + 28)
        if not directory:
            return
        entries, count = struct.unpack('<II', machine.mem_read(directory, 8))
        if not 0 < count <= 100_000 or runtime.word(archive + 40) != count:
            return
        if not flags & 8:
            changes = runtime.word(archive + 48)
            if not changes:
                return
            raw = bytes(machine.mem_read(changes, count * 20))
            # Nonzero state or renamed entries retain all original side effects.
            if has_directory_changes(raw):
                return
        key = (archive, directory, entries, count, flags & 3)
        index = runtime.zip_lookup_cache.get(key)
        if index is None:
            index = {}
            raw = bytes(machine.mem_read(entries, count * 60))
            for n in range(count):
                pointer = struct.unpack_from('<I', raw, n * 60 + 24)[0]
                if not pointer:
                    continue
                name = runtime.bytestring(pointer)
                if flags & 2:
                    name = name.rsplit(b'/', 1)[-1]
                if flags & 1:
                    name = name.lower()
                index.setdefault(name, n)
            runtime.zip_lookup_cache[key] = index
            runtime.zip_lookup_stats['indexes'] += 1
        name = runtime.bytestring(query)
        if flags & 1:
            name = name.lower()
        result = index.get(name, -1)
        if result == -1 and error:
            # _zip_error_set(error, ZIP_ER_NOENT, 0), preserving its string field.
            machine.mem_write(error, struct.pack('<II', 9, 0))
        runtime.zip_lookup_stats['calls'] += 1
        runtime.return_value(result & 0xffffffff)

    address = BASE + (runtime.elf.symbol('_zip_name_locate')['address'] & ~1)
    runtime.zip_lookup_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, lookup, begin=address, end=address))
    for name in ('_zip_free', '_zip_cdir_free', '_zip_cdir_grow', '_zip_dirent_read',
                 '_zip_dirent_finalize', '_zip_entry_new', '_zip_entry_free', '_zip_unchange_data'):
        address = BASE + (runtime.elf.symbol(name)['address'] & ~1)
        runtime.zip_lookup_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, invalidate,
                                        user_data=name, begin=address, end=address))
