#ifndef NEMO_BRIDGE_API_H
#define NEMO_BRIDGE_API_H
#include <stdint.h>
#include <stddef.h>

/* Unicorn C functions use cdecl; desktop GL is APIENTRY on Windows. */
typedef int (*NemoRegRead)(void *, int, void *);
typedef int (*NemoRegWrite)(void *, int, const void *);
typedef int (*NemoMemRead)(void *, uint64_t, void *, size_t);
typedef int (*NemoMemWrite)(void *, uint64_t, const void *, size_t);
typedef int (*NemoStop)(void *);
typedef struct NemoApi {
    NemoRegRead reg_read;
    NemoRegWrite reg_write;
    NemoMemRead mem_read;
    NemoMemWrite mem_write;
    NemoStop emu_stop;
    int regs[6]; /* R0, R1, R2, R3, LR, PC */
} NemoApi;

enum { NEMO_MEMCPY=1, NEMO_MEMMOVE=2, NEMO_MEMSET=3, NEMO_MEMCMP=4,
       NEMO_STRLEN=5, NEMO_STRCMP=6, NEMO_STRNCMP=7, NEMO_STRCASECMP=8, NEMO_STRNCASECMP=9 };
/* String operations 5..9 fall back unless the engine has a live-heap count table
   registered by nemo_string_heap (exported from native_memory.c): counts[(p-base)>>4]
   is GuestHeap.live[p][0]+1 for a live allocation start p, else 0. counts NULL
   unregisters. Returns 1 on success, 0 if the registry is full or the shape is invalid. */
#ifdef _WIN32
#define NEMO_STRING_API __declspec(dllexport)
#else
#define NEMO_STRING_API __attribute__((visibility("default")))
#endif
NEMO_STRING_API int nemo_string_heap(void *engine, uint32_t base, uint32_t size, const uint32_t *counts);
/* 1 = handled; 0 = fall back before any side effect; -1 = Unicorn error.
   Error path sets *error to a Unicorn error code; caller stops and reports it.
   Inputs and results are ARM32 unsigned register bit patterns. */
int nemo_memory_call(const NemoApi *api, void *engine, uint32_t operation,
                     const uint32_t args[4], uint32_t *result, int *error);
/* Operations 201..221 follow the name order in desktop/compiled_bridge.py.
   Same 1/0/-1 contract. math_function and errno_function are the exact host
   library functions used by guest_math.py. guest_errno is current thread TLS.
   A double result writes high word to regs[1], then returns low via *result. */
int nemo_math_call(const NemoApi *api, void *engine, uint32_t operation,
                   const uint32_t args[4], void *math_function, void *errno_function,
                   uint32_t guest_errno, uint32_t *result, int *error);
#endif
