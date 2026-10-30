#include "native_draw.h"
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#define GLAPI __stdcall
#else
#define GLAPI
#endif

typedef void (GLAPI *Bind)(uint32_t, uint32_t);
typedef void (GLAPI *Unit)(uint32_t);
typedef void (GLAPI *Pointer)(int32_t, uint32_t, int32_t, const void *);
typedef void (GLAPI *NormalPointer)(uint32_t, int32_t, const void *);
typedef void (GLAPI *Arrays)(uint32_t, int32_t, int32_t);
typedef void (GLAPI *Elements)(uint32_t, int32_t, uint32_t, const void *);

static int attr_index(uint32_t token, uint32_t texture) {
    switch (token) {
    case 0x8074: return 0;
    case 0x8075: return 1;
    case 0x8076: return 2;
    case 0x8844: return 3;
    case 0x86ad: return 4;
    case 0x8b9c: return 5;
    case 0x8078: return texture >= 0x84c0 && texture <= 0x84df ? 6 + (int)(texture - 0x84c0) : -1;
    default: return -1;
    }
}

static uint32_t type_size(uint32_t kind) {
    switch (kind) {
    case 0x1400: case 0x1401: return 1;
    case 0x1402: case 0x1403: return 2;
    case 0x1406: return 4;
    default: return 0; /* GL_FIXED and unknown types keep the Python conversion */
    }
}

/* Combinations desktop GL 2.1 accepts; anything else stays on the Python path. */
static int desktop_shape(int index, uint32_t count, uint32_t kind) {
    if (!type_size(kind)) return 0;
    if (index == 0) return count >= 2 && count <= 4 && (kind == 0x1402 || kind == 0x1406);
    if (index == 1) return count == 3 && (kind == 0x1400 || kind == 0x1402 || kind == 0x1406);
    if (index == 2) return count == 3 || count == 4;
    return count >= 1 && count <= 4 && (kind == 0x1402 || kind == 0x1406);
}

static DrawBuffer *lookup(DrawContext *d, uint32_t id) {
    DrawBuffer *buffer = id < d->buffer_capacity ? &d->buffers[id] : NULL;
    return buffer && buffer->known ? buffer : NULL;
}

static DrawBuffer *slot(DrawContext *d, uint32_t id) {
    uint32_t capacity;
    DrawBuffer *grown;
    if (id >= DRAW_BUFFER_IDS) return NULL;
    if (id >= d->buffer_capacity) {
        capacity = d->buffer_capacity ? d->buffer_capacity : 256;
        while (capacity <= id) capacity *= 2;
        grown = (DrawBuffer *)realloc(d->buffers, capacity * sizeof(DrawBuffer));
        if (!grown) return NULL;
        memset(grown + d->buffer_capacity, 0, (capacity - d->buffer_capacity) * sizeof(DrawBuffer));
        d->buffers = grown; d->buffer_capacity = capacity;
    }
    return &d->buffers[id];
}

/* Mirrors GraphicsPlatform.buffers[id]; returns 0 if the entry could not be stored,
   in which case it stays unknown and every draw using it falls back. */
int draw_buffer(DrawContext *d, uint32_t id, uint32_t known, uint64_t size, const void *data) {
    DrawBuffer *buffer = slot(d, id);
    if (!buffer) return 0;
    free(buffer->data);
    memset(buffer, 0, sizeof(*buffer));
    if (!known) return 1;
    if (size <= DRAW_BUFFER_CONTENT && size && data) {
        buffer->data = (unsigned char *)malloc((size_t)size);
        if (buffer->data) { memcpy(buffer->data, data, (size_t)size); buffer->has_data = 1; }
    } else if (!size) {
        buffer->has_data = 1;
    }
    buffer->size = size; buffer->known = 1;
    return 1;
}

void draw_reset_buffers(DrawContext *d) {
    uint32_t i;
    for (i = 0; i < d->buffer_capacity; ++i) free(d->buffers[i].data);
    free(d->buffers);
    d->buffers = NULL; d->buffer_capacity = 0;
}

void draw_free(DrawContext *d) {
    draw_reset_buffers(d);
    free(d->scratch);
    d->scratch = NULL;
}

void draw_invalidate(DrawContext *d) {
    if (d->valid) ++d->counters[DRAW_INVALIDATIONS];
    d->valid = 0;
}

void draw_client_event(DrawContext *d, uint32_t operation, const uint32_t a[4]) {
    NemoDrawState *s = &d->state;
    DrawAttr *attr;
    DrawBuffer *buffer;
    int index;
    static const uint32_t tokens[] = {0x8074,0x8075,0x8076,0x8078,0x8844,0x86ad,0x8b9c};
    switch (operation) {
    case 301: s->client_texture = a[0]; return;
    case 302: case 303:
        index = attr_index(a[0], s->client_texture);
        if (index < 0) { draw_invalidate(d); return; }
        s->attrs[index].enabled = operation == 302;
        return;
    case 311:
        if (a[0] == 0x8892) s->array_binding = a[1]; else s->element_binding = a[1];
        if (a[1] && !lookup(d, a[1])) { /* GraphicsPlatform: buffers.setdefault(value, bytearray()) */
            buffer = slot(d, a[1]);
            if (buffer) { buffer->known = 1; buffer->has_data = 1; }
        }
        return;
    default:
        if (operation < 304 || operation > 310) { draw_invalidate(d); return; }
        index = attr_index(tokens[operation - 304], s->client_texture);
        if (index < 0) { draw_invalidate(d); return; }
        attr = &s->attrs[index];
        attr->present = 1; attr->buffer = s->array_binding;
        if (operation == 305 || operation == 310) {
            attr->count = operation == 305 ? 3 : 1;
            attr->kind = a[0]; attr->stride = a[1]; attr->pointer = a[2];
        } else {
            attr->count = a[0]; attr->kind = a[1]; attr->stride = a[2]; attr->pointer = a[3];
        }
    }
}

typedef struct Resolved { int index; uint32_t count, kind, stride, pointer, buffer; } Resolved;

#define REFUSE(which) do { ++d->counters[which]; return 0; } while (0)

int draw_call(DrawContext *d, const NemoApi *api, void *engine, uint32_t operation, const uint32_t a[4]) {
    NemoDrawState *s = &d->state;
    DrawFunctions *gl = &d->gl;
    Resolved resolved[DRAW_ATTRS];
    DrawBuffer *buffer;
    const unsigned char *source = NULL;
    uint32_t mode = a[0], count, first = 0, kind = 0, offset = 0, current, texture, i, index_size;
    uint64_t low = 0, high = 0, size, step;
    int used = 0, index;
    if (!d->enabled || !d->configured) return 0;
    if (!d->valid) REFUSE(DRAW_FALLBACK_STATE);
    if (mode > 6 || s->palette || (mode == 0 && s->attrs[5].enabled)) REFUSE(DRAW_FALLBACK_UNSUPPORTED);
    if (!s->attrs[0].enabled) REFUSE(DRAW_FALLBACK_STATE); /* Python raises the guest-visible error */
    if (operation == 312) {
        first = a[1]; count = a[2];
        if (count > 1000000 || first > 0x7fffffff) REFUSE(DRAW_FALLBACK_BOUNDS);
        low = first; high = (uint64_t)first + count - 1;
    } else {
        count = a[1]; kind = a[2]; offset = a[3];
        if (count > 1000000 || (kind != 0x1401 && kind != 0x1403)) REFUSE(DRAW_FALLBACK_BOUNDS);
        index_size = kind == 0x1401 ? 1 : 2;
        size = (uint64_t)count * index_size;
        if (s->element_binding) {
            buffer = lookup(d, s->element_binding);
            if (!buffer || !buffer->has_data) REFUSE(DRAW_FALLBACK_UNSUPPORTED);
            if ((uint64_t)offset + size > buffer->size) REFUSE(DRAW_FALLBACK_BOUNDS);
            source = buffer->data ? buffer->data + offset : NULL;
        } else {
            if (!d->scratch && !(d->scratch = (unsigned char *)malloc(DRAW_SCRATCH)))
                REFUSE(DRAW_FALLBACK_UNSUPPORTED);
            if (size && api->mem_read(engine, offset, d->scratch, (size_t)size))
                REFUSE(DRAW_FALLBACK_BOUNDS); /* Python reports the Unicorn error */
            source = d->scratch;
        }
        if (count) {
            low = 0xffffffffu; high = 0;
            for (i = 0; i < count; ++i) {
                uint32_t value = index_size == 1 ? source[i] : (uint32_t)(source[2*i] | source[2*i+1] << 8);
                if (value < low) low = value;
                if (value > high) high = value;
            }
        }
    }
    for (index = 0; index < DRAW_ATTRS; ++index) {
        DrawAttr *attr = &s->attrs[index];
        if (!attr->enabled || (index >= 3 && index <= 5)) continue; /* bridge-emulated arrays */
        if (!attr->present) REFUSE(DRAW_FALLBACK_STATE);
        if (!desktop_shape(index, attr->count, attr->kind) || !attr->buffer)
            REFUSE(DRAW_FALLBACK_UNSUPPORTED); /* GL_FIXED, client memory, odd shapes */
        buffer = lookup(d, attr->buffer);
        if (!buffer) REFUSE(DRAW_FALLBACK_STATE); /* deleted or unmirrored buffer */
        size = (uint64_t)attr->count * type_size(attr->kind);
        step = attr->stride ? attr->stride : size;
        if (count && attr->pointer + high * step + size > buffer->size) REFUSE(DRAW_FALLBACK_BOUNDS);
        resolved[used].index = index; resolved[used].count = attr->count; resolved[used].kind = attr->kind;
        resolved[used].stride = attr->stride; resolved[used].pointer = attr->pointer;
        resolved[used].buffer = attr->buffer; ++used;
    }
    (void)low;
    /* Same host sequence as direct_vertex_draw.try_draw: per-attribute binds, then restore. */
    current = s->array_binding; texture = s->client_texture;
    for (i = 0; i < (uint32_t)used; ++i) {
        Resolved *r = &resolved[i];
        const void *pointer = (const void *)(uintptr_t)r->pointer;
        if (r->buffer != current) { ((Bind)gl->bind_buffer)(0x8892, r->buffer); current = r->buffer; }
        if (r->index >= 6 && 0x84c0 + (uint32_t)(r->index - 6) != texture) {
            texture = 0x84c0 + (uint32_t)(r->index - 6);
            ((Unit)gl->client_active_texture)(texture);
        }
        switch (r->index) {
        case 0: ((Pointer)gl->vertex_pointer)((int32_t)r->count, r->kind, (int32_t)r->stride, pointer); break;
        case 1: ((NormalPointer)gl->normal_pointer)(r->kind, (int32_t)r->stride, pointer); break;
        case 2: ((Pointer)gl->color_pointer)((int32_t)r->count, r->kind, (int32_t)r->stride, pointer); break;
        default: ((Pointer)gl->tex_coord_pointer)((int32_t)r->count, r->kind, (int32_t)r->stride, pointer); break;
        }
    }
    if (operation == 312) {
        ((Arrays)gl->draw_arrays)(mode, (int32_t)first, (int32_t)count);
        ++d->counters[DRAW_ARRAYS];
    } else {
        /* Element binding is never changed here: a bound buffer takes the offset, else host scratch. */
        ((Elements)gl->draw_elements)(mode, (int32_t)count, kind,
                                      s->element_binding ? (const void *)(uintptr_t)offset : (const void *)d->scratch);
        ++d->counters[DRAW_ELEMENTS];
    }
    if (current != s->array_binding) ((Bind)gl->bind_buffer)(0x8892, s->array_binding);
    if (texture != s->client_texture) ((Unit)gl->client_active_texture)(s->client_texture);
    ++d->counters[DRAW_NATIVE];
    return 1;
}
