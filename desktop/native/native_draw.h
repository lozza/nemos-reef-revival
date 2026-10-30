#ifndef NEMO_NATIVE_DRAW_H
#define NEMO_NATIVE_DRAW_H
#include "bridge_api.h"

/* Mirror of GraphicsPlatform client-array state for all-VBO direct draws.
   Attribute slots: 0 vertex, 1 normal, 2 color, 3 matrix index, 4 weight,
   5 point size, 6+u texcoord unit GL_TEXTURE0+u (u < 32). */
#define DRAW_ATTRS 38
#define DRAW_BUFFER_IDS 65536u        /* larger ids are never mirrored: draws fall back */
#define DRAW_BUFFER_CONTENT (1u << 20) /* only buffers this small keep bytes for index scans */
#define DRAW_SCRATCH (2u * 1000000u)

typedef struct DrawAttr { uint32_t enabled, present, count, kind, stride, pointer, buffer; } DrawAttr;
typedef struct NemoDrawState {
    uint32_t client_texture, palette, array_binding, element_binding;
    DrawAttr attrs[DRAW_ATTRS];
} NemoDrawState;
typedef struct DrawFunctions {
    void *bind_buffer, *client_active_texture, *vertex_pointer, *normal_pointer,
         *color_pointer, *tex_coord_pointer, *draw_arrays, *draw_elements;
} DrawFunctions;
typedef struct DrawBuffer { uint32_t known, has_data; uint64_t size; unsigned char *data; } DrawBuffer;

enum {
    DRAW_NATIVE, DRAW_ARRAYS, DRAW_ELEMENTS, DRAW_FALLBACK_STATE, DRAW_FALLBACK_UNSUPPORTED,
    DRAW_FALLBACK_BOUNDS, DRAW_INVALIDATIONS, DRAW_PUBLISHES, DRAW_COUNTERS
};

typedef struct DrawContext {
    int enabled, valid, configured;
    NemoDrawState state;
    DrawFunctions gl;
    DrawBuffer *buffers;
    uint32_t buffer_capacity;
    unsigned char *scratch;
    uint64_t counters[DRAW_COUNTERS];
} DrawContext;

/* Opcodes: 312 glDrawArrays, 313 glDrawElements. 1 = drawn; 0 = untouched fallback. */
int draw_call(DrawContext *d, const NemoApi *api, void *engine, uint32_t operation, const uint32_t a[4]);
/* Applies one natively handled client op (301..311) to the mirror. */
void draw_client_event(DrawContext *d, uint32_t operation, const uint32_t a[4]);
void draw_invalidate(DrawContext *d);
int draw_buffer(DrawContext *d, uint32_t id, uint32_t known, uint64_t size, const void *data);
void draw_reset_buffers(DrawContext *d);
void draw_free(DrawContext *d);
#endif
