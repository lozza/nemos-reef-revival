/* Compiled memcpy/memmove/memset/memcmp imports for the ARM32 guest.

   Mirrors desktop/native_platform.py: copy_import, set_import and the
   ordered-chain memcmp. Guest memory is reached only through api->mem_read
   and api->mem_write (Unicorn's uc_mem_read/uc_mem_write); guest pointers are
   never turned into host addresses. Each operation is one full-length read
   (or two for memcmp) and at most one full-length write, matching the Python
   call sequence, so Unicorn's error codes and no-partial-write behaviour carry
   over unchanged. Registers are untouched; the caller writes R0 and returns. */
#include <stdlib.h>
#include <string.h>
#include "bridge_api.h"

/* native_platform.HEAP_SIZE: larger counts raise RuntimeError in Python. */
#define NEMO_HEAP_SIZE 0x04000000u
/* Larger counts stay in Python; keeps temporary host memory bounded. */
#define NEMO_MEMORY_LIMIT 0x00100000u
#define NEMO_LOCAL_BYTES 2048u

/* String imports (strlen/strcmp/strncmp/strcasecmp/strncasecmp) mirror
   NativePlatform.bytestring: default limit is the live allocation count when the
   pointer starts a live heap allocation, else 0x100000; a zero limit reads nothing;
   a null pointer, an unterminated string, any Unicorn read error or a string past
   the native-only bound returns 0 (Python fallback, which raises or handles it).
   Reads stay inside one 4 KiB page each, like bytestring's chunks, so a native
   success implies Python reads the same bytes without fault. Comparisons read the
   full left string, then the full right string, then compare. Nothing is cached. */
#define NEMO_STRING_DEFAULT_LIMIT 0x00100000u
#define NEMO_STRING_COMPARE_BYTES 4096u  /* native-only bound per compared string */
#define NEMO_STRING_HEAPS 16

typedef struct StringHeap { void *engine; uint32_t base, size; const uint32_t *counts; } StringHeap;
static StringHeap string_heaps[NEMO_STRING_HEAPS];

NEMO_STRING_API int nemo_string_heap(void *engine, uint32_t base, uint32_t size, const uint32_t *counts)
{
    int i, empty = -1;
    if (engine == NULL || (counts != NULL && ((base & 15u) || !size || (uint64_t)base + size > 0x100000000ull)))
        return 0;
    for (i = 0; i < NEMO_STRING_HEAPS; ++i) {
        if (string_heaps[i].engine == engine) {
            string_heaps[i].counts = NULL;  /* never read a half-updated entry */
            string_heaps[i].engine = NULL;
            if (empty < 0) empty = i;
        } else if (string_heaps[i].engine == NULL && empty < 0) {
            empty = i;
        }
    }
    if (counts == NULL)
        return 1;
    if (empty < 0)
        return 0;
    string_heaps[empty].base = base;
    string_heaps[empty].size = size;
    string_heaps[empty].counts = counts;
    string_heaps[empty].engine = engine;
    return 1;
}

static uint32_t string_limit(const StringHeap *heap, uint32_t address)
{
    uint32_t offset = address - heap->base, count;
    if (offset < heap->size && !(offset & 15u)) {
        count = heap->counts[offset >> 4];
        if (count)
            return count - 1u;
    }
    return NEMO_STRING_DEFAULT_LIMIT;
}

/* 1 with *length set; 0 = leave it to Python. out (cap bytes) receives the string
   when non-NULL; otherwise scratch (one page) is used and only the length kept. */
static int string_read(const NemoApi *api, void *engine, uint32_t address, uint32_t limit,
                       int terminated, unsigned char *out, uint32_t cap,
                       unsigned char *scratch, uint32_t *length)
{
    uint32_t have = 0, count;
    uint64_t at;
    unsigned char *target, *end;
    if (!limit) {
        *length = 0;
        return 1;
    }
    if (!address)
        return 0;  /* "unexpected null C string" */
    while (have < limit) {
        if (have >= cap)
            return 0;  /* native-only bound */
        at = (uint64_t)address + have;
        count = 4096u - (uint32_t)(at & 4095u);
        if (count > limit - have) count = limit - have;
        if (count > cap - have) count = cap - have;
        target = out ? out + have : scratch;
        if (api->mem_read(engine, at, target, count) != 0)
            return 0;  /* Python re-reads and raises the same UcError */
        end = (unsigned char *)memchr(target, 0, count);
        if (end != NULL) {
            *length = have + (uint32_t)(end - target);
            return 1;
        }
        have += count;
    }
    if (!terminated) {
        *length = have;
        return 1;
    }
    return 0;  /* "unterminated C string in guest memory" */
}

static int string_call(const NemoApi *api, void *engine, uint32_t operation,
                       const uint32_t args[4], uint32_t *result)
{
    unsigned char left[NEMO_STRING_COMPARE_BYTES], right[NEMO_STRING_COMPARE_BYTES];
    const StringHeap *heap = NULL;
    uint32_t left_length, right_length, left_limit, right_limit, i, n;
    int bounded = operation == NEMO_STRNCMP || operation == NEMO_STRNCASECMP, cmp = 0;
    unsigned char x, y;

    if (api->mem_read == NULL || engine == NULL)
        return 0;
    for (i = 0; i < NEMO_STRING_HEAPS; ++i)
        if (string_heaps[i].engine == engine && string_heaps[i].counts != NULL)
            heap = &string_heaps[i];
    if (heap == NULL)
        return 0;
    if (operation == NEMO_STRLEN) {
        if (!string_read(api, engine, args[0], string_limit(heap, args[0]), 1, NULL,
                         NEMO_STRING_DEFAULT_LIMIT, left, &left_length))
            return 0;
        *result = left_length;
        return 1;
    }
    if (operation == NEMO_STRNCMP && args[2] > NEMO_HEAP_SIZE)
        return 0;  /* "strncmp exceeds the guest memory budget"; strncasecmp has no such check */
    left_limit = bounded ? args[2] : string_limit(heap, args[0]);
    right_limit = bounded ? args[2] : string_limit(heap, args[1]);
    if (!string_read(api, engine, args[0], left_limit, !bounded, left, sizeof left, NULL, &left_length)
        || !string_read(api, engine, args[1], right_limit, !bounded, right, sizeof right, NULL, &right_length))
        return 0;
    n = left_length < right_length ? left_length : right_length;
    if (operation == NEMO_STRCASECMP || operation == NEMO_STRNCASECMP) {
        for (i = 0; i < n && !cmp; ++i) {  /* bytes.lower(): ASCII A-Z only */
            x = left[i]; y = right[i];
            if (x >= 'A' && x <= 'Z') x = (unsigned char)(x + 32);
            if (y >= 'A' && y <= 'Z') y = (unsigned char)(y + 32);
            cmp = (int)x - (int)y;
        }
    } else if (n) {
        cmp = memcmp(left, right, n);
    }
    if (!cmp)
        cmp = left_length > right_length ? 1 : left_length < right_length ? -1 : 0;
    *result = (uint32_t)(cmp > 0 ? 1 : cmp < 0 ? -1 : 0);
    return 1;
}

int nemo_memory_call(const NemoApi *api, void *engine, uint32_t operation,
                     const uint32_t args[4], uint32_t *result, int *error)
{
    unsigned char local[NEMO_LOCAL_BYTES];
    unsigned char *buffer;
    uint32_t destination, count;
    size_t need;
    uint32_t value = 0;
    int status = 0, cmp = 0;

    if (api == NULL || args == NULL || result == NULL || error == NULL)
        return 0;
    if (operation >= NEMO_STRLEN && operation <= NEMO_STRNCASECMP)
        return string_call(api, engine, operation, args, result);  /* never -1: errors fall back */
    if (operation < NEMO_MEMCPY || operation > NEMO_MEMCMP)
        return 0;
    if (operation == NEMO_MEMSET ? api->mem_write == NULL
        : api->mem_read == NULL || (operation != NEMO_MEMCMP && api->mem_write == NULL))
        return 0;
    destination = args[0];
    count = args[2];
    if (count > NEMO_HEAP_SIZE || count > NEMO_MEMORY_LIMIT)
        return 0;  /* Python raises the budget error or handles the bulk copy */

    if ((operation == NEMO_MEMCPY || operation == NEMO_MEMMOVE) && count == 0) {
        *result = destination;  /* copy_import touches no memory for count 0 */
        return 1;
    }
    need = operation == NEMO_MEMCMP ? (size_t)count * 2 : (size_t)count;
    if (need <= sizeof local) {
        buffer = local;
    } else {
        buffer = (unsigned char *)malloc(need);
        if (buffer == NULL)
            return 0;  /* nothing touched yet; Python takes over */
    }

    switch (operation) {
    case NEMO_MEMCPY:
    case NEMO_MEMMOVE:
        /* Snapshot read then write: overlap-safe in both directions. */
        status = api->mem_read(engine, args[1], buffer, count);
        if (status == 0)
            status = api->mem_write(engine, destination, buffer, count);
        value = destination;
        break;
    case NEMO_MEMSET:
        memset(buffer, (int)(args[1] & 255u), count);
        status = api->mem_write(engine, destination, buffer, count);  /* size 0 too */
        value = destination;
        break;
    case NEMO_MEMCMP:
        status = api->mem_read(engine, args[0], buffer, count);
        if (status == 0)
            status = api->mem_read(engine, args[1], buffer + count, count);
        if (status == 0 && count)
            cmp = memcmp(buffer, buffer + count, count);  /* unsigned bytes */
        value = (uint32_t)(cmp > 0 ? 1 : cmp < 0 ? -1 : 0);
        break;
    }

    if (buffer != local)
        free(buffer);
    if (status != 0) {
        *error = status;
        return -1;  /* *result left untouched */
    }
    *result = value;
    return 1;
}
