#define WIN32_LEAN_AND_MEAN
#include "bridge_api.h"
#include "native_draw.h"
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#define API __declspec(dllexport)
#define GLAPI __stdcall
#else
#include <time.h>
#define API __attribute__((visibility("default")))
#define GLAPI
#endif

typedef void (*Fallback)(void *, uint64_t, uint32_t, void *);
typedef void (*ErrorCallback)(int);
typedef struct Entry { uint32_t operation, flags; void *function; uint64_t count, successes; } Entry;
typedef struct Event { uint32_t slot, argc, args[4]; } Event;
/* Client-array shadow updates, applied in order by NativeBridge.sync_client_state. */
#define CLIENT_QUEUE 128
typedef struct ClientEvent { uint32_t operation, args[4]; } ClientEvent;
typedef struct Bridge {
    NemoApi api;
    void *engine;
    Entry *entries;
    uint32_t length;
    uint64_t base;
    Fallback fallback;
    ErrorCallback error;
    uint64_t gl_total, fast_calls, fallback_calls, boundary_stops;
    double deadline;
    int graphics, fallback_depth;
    uint32_t matrix_mode, guest_errno;
    void *errno_function;
    Event events[4096];
    int client_enabled;
    uint32_t client_pending;
    ClientEvent client_queue[CLIENT_QUEUE];
    DrawContext draw;
} Bridge;

API int nemo_check_layout(unsigned api,unsigned entry,unsigned event) {
    return sizeof(void *) == 8 && api == sizeof(NemoApi) && entry == sizeof(Entry) && event == sizeof(Event);
}

static double now_seconds(void) {
#ifdef _WIN32
    LARGE_INTEGER value, frequency;
    QueryPerformanceCounter(&value);
    QueryPerformanceFrequency(&frequency);
    return (double)value.QuadPart / (double)frequency.QuadPart;
#else
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (double)value.tv_sec + (double)value.tv_nsec * 1e-9;
#endif
}

API void *nemo_create(const NemoApi *api, void *engine, Entry *entries,
                     uint32_t length, uint64_t base, Fallback fallback, ErrorCallback error) {
    Bridge *b = (Bridge *)calloc(1, sizeof(Bridge));
    if (!b) return NULL;
    b->api = *api; b->engine = engine; b->entries = entries; b->length = length;
    b->base = base; b->fallback = fallback; b->error = error;
    b->matrix_mode = 0x1700;
    return b;
}
API void nemo_destroy(Bridge *b) { if (b) draw_free(&b->draw); free(b); }
API void nemo_prepare(Bridge *b, double remaining, int graphics, uint32_t guest_errno) {
    b->deadline = remaining < 0 ? 0 : now_seconds() + remaining;
    b->graphics = graphics;
    b->guest_errno = guest_errno;
}
API void nemo_matrix_mode(Bridge *b, uint32_t mode) { b->matrix_mode = mode; }
API void nemo_errno_function(Bridge *b, void *function) { b->errno_function = function; }
API uint64_t nemo_counter(Bridge *b, unsigned which) {
    switch (which) {
    case 0: return b->gl_total;
    case 1: return b->fast_calls;
    case 2: return b->fallback_calls;
    case 3: return b->boundary_stops;
    default: return 0;
    }
}
API void nemo_events(Bridge *b, Event *output) {
    memcpy(output, b->events, sizeof(b->events));
}
API int *nemo_fallback_flag(Bridge *b) { return &b->fallback_depth; }
API void nemo_record(Bridge *b, uint32_t slot, const uint32_t *args, uint32_t argc) {
    Event *event = &b->events[b->gl_total % 4096];
    event->slot = slot; event->argc = argc > 4 ? 4 : argc;
    if (event->argc) memcpy(event->args,args,event->argc*4);
    ++b->gl_total;
}
API int nemo_client_layout(unsigned event, unsigned capacity) {
    return event == sizeof(ClientEvent) && capacity == CLIENT_QUEUE;
}
API void nemo_client_enable(Bridge *b, int enabled) { b->client_enabled = enabled; }
API uint32_t *nemo_client_pending(Bridge *b) { return &b->client_pending; }
/* Copies the oldest events out; anything left stays queued in order. */
API uint32_t nemo_client_drain(Bridge *b, ClientEvent *output, uint32_t capacity) {
    uint32_t count = b->client_pending < capacity ? b->client_pending : capacity;
    memcpy(output, b->client_queue, count * sizeof(ClientEvent));
    b->client_pending -= count;
    if (b->client_pending)
        memmove(b->client_queue, b->client_queue + count, b->client_pending * sizeof(ClientEvent));
    return count;
}
/* Optional all-VBO direct draws (312/313); off until nemo_draw_enable. */
API int nemo_draw_layout(unsigned state, unsigned functions, unsigned counters) {
    return state == sizeof(NemoDrawState) && functions == sizeof(DrawFunctions) && counters == DRAW_COUNTERS;
}
API void nemo_draw_configure(Bridge *b, const DrawFunctions *functions) {
    const void *const *f = (const void *const *)functions;
    unsigned i;
    b->draw.gl = *functions;
    b->draw.configured = 1;
    for (i = 0; i < sizeof(DrawFunctions) / sizeof(void *); ++i)
        if (!f[i]) b->draw.configured = 0;
}
API void nemo_draw_enable(Bridge *b, int enabled) { b->draw.enabled = enabled; if (!enabled) draw_invalidate(&b->draw); }
API int *nemo_draw_valid(Bridge *b) { return &b->draw.valid; }
API uint64_t *nemo_draw_counters(Bridge *b) { return b->draw.counters; }
/* Python's applied shadow becomes the mirror; buffers must already be current. */
API void nemo_draw_publish(Bridge *b, const NemoDrawState *state) {
    b->draw.state = *state;
    b->draw.valid = 1;
    ++b->draw.counters[DRAW_PUBLISHES];
}
API int nemo_draw_buffer(Bridge *b, uint32_t id, uint32_t known, uint64_t size, const void *data) {
    return draw_buffer(&b->draw, id, known, size, data);
}
API void nemo_draw_reset_buffers(Bridge *b) { draw_reset_buffers(&b->draw); draw_invalidate(&b->draw); }

static float as_float(uint32_t value) { float result; memcpy(&result, &value, 4); return result; }
static int graphics_call(Entry *entry, const uint32_t a[4]) {
    void *f = entry->function;
    if (!f) return 0;
    switch (entry->operation) {
    case 101: ((void (GLAPI *)(uint32_t))f)(a[0]); break;
    case 102: ((void (GLAPI *)(uint32_t,uint32_t))f)(a[0],a[1]); break;
    case 103: ((void (GLAPI *)(uint32_t,uint32_t,uint32_t))f)(a[0],a[1],a[2]); break;
    case 104: ((void (GLAPI *)(uint32_t,uint32_t,int32_t))f)(a[0],a[1],(int32_t)a[2]); break;
    case 105: ((void (GLAPI *)(uint32_t,float))f)(a[0],as_float(a[1])); break;
    case 106: ((void (GLAPI *)(float))f)(as_float(a[0])); break;
    case 107: ((void (GLAPI *)(float,float,float,float))f)(as_float(a[0]),as_float(a[1]),as_float(a[2]),as_float(a[3])); break;
    case 108: ((void (GLAPI *)(unsigned char,unsigned char,unsigned char,unsigned char))f)((unsigned char)a[0],(unsigned char)a[1],(unsigned char)a[2],(unsigned char)a[3]); break;
    case 109: ((void (GLAPI *)(unsigned char))f)((unsigned char)a[0]); break;
    case 110: ((void (GLAPI *)(uint32_t,int32_t,uint32_t))f)(a[0],(int32_t)a[1],a[2]); break;
    case 111: ((void (GLAPI *)(uint32_t,uint32_t,float))f)(a[0],a[1],as_float(a[2])); break;
    case 112: ((void (GLAPI *)(void))f)(); break;
    /* Matrix-palette enable is bridge state, never passed to desktop GL. */
    case 113:
        if (a[0] == 0x8840) return 0;
        ((void (GLAPI *)(uint32_t))f)(a[0]); break;
    default: return 0;
    }
    return 1;
}

/* Operations 301..311 mirror graphics_platform.py bind_import. Every check runs
   before GL or the queue changes, so 0 always means an untouched Python fallback. */
static int client_call(Bridge *b, Entry *entry, const uint32_t a[4]) {
    void *f = entry->function;
    uint32_t count, stride;
    ClientEvent *event;
    if (!b->graphics || !b->client_enabled || b->client_pending >= CLIENT_QUEUE) return 0;
    switch (entry->operation) {
    case 301: /* glClientActiveTexture */
        if (!f || a[0] < 0x84c0 || a[0] > 0x84df) return 0;
        ((void (GLAPI *)(uint32_t))f)(a[0]);
        break;
    case 302: case 303: /* glEnableClientState, glDisableClientState */
        switch (a[0]) {
        case 0x8074: case 0x8075: case 0x8076: case 0x8078:
            if (!f) return 0;
            ((void (GLAPI *)(uint32_t))f)(a[0]);
            break;
        case 0x8844: case 0x86ad: case 0x8b9c: break; /* bridge-emulated arrays */
        default: return 0;
        }
        break;
    case 304: case 306: case 307: case 308: case 309: /* (count,type,stride,pointer) */
        count = a[0]; stride = a[2];
        if (count < 1 || count > 4 || stride > 1000000) return 0;
        break;
    case 305: case 310: /* glNormalPointer, glPointSizePointerOES: (type,stride,pointer) */
        stride = a[1];
        if (stride > 1000000) return 0;
        break;
    case 311: /* glBindBuffer */
        if (!f || (a[0] != 0x8892 && a[0] != 0x8893)) return 0;
        ((void (GLAPI *)(uint32_t,uint32_t))f)(a[0],a[1]);
        break;
    default: return 0;
    }
    event = &b->client_queue[b->client_pending++];
    event->operation = entry->operation;
    memcpy(event->args, a, sizeof(event->args));
    draw_client_event(&b->draw, entry->operation, a);
    return 1;
}

static void failed(Bridge *b, int error) {
    b->api.emu_stop(b->engine);
    b->error(error);
}

API void nemo_hook(void *engine, uint64_t address, uint32_t size, void *opaque) {
    Bridge *b = (Bridge *)opaque;
    uint64_t offset = address - b->base;
    uint32_t a[4], result = 0, link;
    int error = 0, handled = 0;
    Entry *entry;
    unsigned i, slot;
    if (address < b->base || (offset & 3) || offset / 4 >= b->length) goto fallback;
    slot = (unsigned)(offset / 4);
    entry = &b->entries[slot];
    if (!entry->operation && !(entry->flags & 1)) goto fallback;
    for (i = 0; i < 4; ++i) {
        error = b->api.reg_read(engine, b->api.regs[i], &a[i]);
        if (error) { ++entry->count; failed(b,error); return; }
    }
    if (entry->flags & 1) {
        nemo_record(b,slot,a,4);
    }
    if (entry->operation == 114) b->matrix_mode = a[0];
    if (entry->operation == 312 || entry->operation == 313) {
        handled = b->graphics && entry->function ? draw_call(&b->draw,&b->api,engine,entry->operation,a) : 0;
    } else if (entry->operation >= 300) {
        handled = client_call(b,entry,a);
    } else if (entry->operation >= 200) {
        handled = nemo_math_call(&b->api,engine,entry->operation,a,entry->function,
                                b->errno_function,b->guest_errno,&result,&error);
        if (handled < 0) { ++entry->count; failed(b,error); return; }
    } else if (entry->operation == 115 && b->graphics && b->matrix_mode != 0x8840) {
        float matrix[16];
        error = b->api.mem_read(engine,a[0],matrix,sizeof(matrix));
        if (error) { ++entry->count; failed(b,error); return; }
        ((void (GLAPI *)(const float *))entry->function)(matrix);
        handled = 1;
    } else if (entry->operation >= 100) {
        if (b->graphics) handled = graphics_call(entry,a);
    } else if (entry->operation) {
        handled = nemo_memory_call(&b->api, engine, entry->operation, a, &result, &error);
        if (handled < 0) { ++entry->count; failed(b,error); return; }
    }
    if (!handled) goto fallback;
    ++entry->count; ++entry->successes; ++b->fast_calls;
    error = b->api.reg_read(engine, b->api.regs[4], &link);
    if (!error) error = b->api.reg_write(engine, b->api.regs[0], &result);
    if (!error) error = b->api.reg_write(engine, b->api.regs[5], &link);
    if (error) { failed(b,error); return; }
    if (b->deadline && now_seconds() >= b->deadline) {
        ++b->boundary_stops;
        b->api.emu_stop(engine);
    }
    return;
fallback:
    /* flags bit 2: Python may change draw state; the mirror is stale until republished. */
    if (address >= b->base && !(offset & 3) && offset / 4 < b->length && (b->entries[offset / 4].flags & 2))
        draw_invalidate(&b->draw);
    ++b->fallback_calls;
    ++b->fallback_depth;
    b->fallback(engine,address,size,NULL);
    --b->fallback_depth;
}
