/* Isolated Dynarmic A32 probe: tiny C ABI for benchmarking against Unicorn.
 * Not part of the production bridge. */
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#ifdef DP_BUILD
#define DP_API __declspec(dllexport)
#else
#define DP_API __declspec(dllimport)
#endif
#else /* ELF: libjit_core.so exports only DP_API symbols (built with -fvisibility=hidden) */
#define DP_API __attribute__((visibility("default")))
#endif

/* dp_create flags */
#define DP_FLAG_PRECISE_DATA_FAULTS 1u /* check_halt_on_memory_access: fault PC = faulting insn */
#define DP_FLAG_NO_OPTIMIZATIONS 2u    /* debugging: disable IR opts / block linking */

/* dp_run stop reasons (bitmask, mirrors what Dynarmic halted with) */
#define DP_STOP_BUDGET 0u          /* tick budget exhausted (no halt reason) */
#define DP_STOP_TRAP 1u            /* fetch from a trap range: hook/import/return sentinel */
#define DP_STOP_SVC 2u             /* SVC executed; detail = immediate */
#define DP_STOP_HALT 4u            /* dp_halt() from another thread (wall-clock deadline) */
#define DP_STOP_DATA_FAULT 8u      /* load/store to unmapped page */
#define DP_STOP_EXEC_FAULT 16u     /* fetch from unmapped page */
#define DP_STOP_EXCEPTION 32u      /* undefined/unpredictable/bkpt; detail = exception code */
#define DP_STOP_UNSUPPORTED 64u    /* Dynarmic wanted the interpreter fallback; PC = that insn */

typedef struct dp_stop {
    uint32_t reasons;      /* DP_STOP_* bits */
    uint32_t pc;           /* trap/fault/fallback instruction address */
    uint32_t fault_addr;   /* data fault address */
    uint32_t fault_size;   /* bytes */
    uint32_t fault_write;  /* 1 if write */
    uint32_t detail;       /* SVC imm or exception code */
    uint64_t ticks;        /* instructions retired in this dp_run */
} dp_stop;

typedef struct dp_cpu dp_cpu;

DP_API uint32_t dp_version(void);
DP_API dp_cpu* dp_create(uint32_t flags);
DP_API void dp_destroy(dp_cpu* cpu);

/* Maps caller-owned host memory (must outlive mapping). addr/size must be 4 KiB aligned. */
DP_API int dp_map(dp_cpu* cpu, uint32_t addr, uint32_t size, void* host);
DP_API int dp_unmap(dp_cpu* cpu, uint32_t addr, uint32_t size);
/* Fetches from [addr, addr+size) stop with DP_STOP_TRAP (page granular is not required). */
DP_API int dp_add_trap(dp_cpu* cpu, uint32_t addr, uint32_t size);
DP_API void dp_invalidate(dp_cpu* cpu, uint32_t addr, uint32_t size);
DP_API void dp_clear_cache(dp_cpu* cpu);

DP_API void dp_get_regs(dp_cpu* cpu, uint32_t out[16]);
DP_API void dp_set_regs(dp_cpu* cpu, const uint32_t in[16]);
DP_API uint32_t dp_get_reg(dp_cpu* cpu, uint32_t index);
DP_API void dp_set_reg(dp_cpu* cpu, uint32_t index, uint32_t value);
DP_API void dp_get_ext_regs(dp_cpu* cpu, uint32_t out[64]);
DP_API void dp_set_ext_regs(dp_cpu* cpu, const uint32_t in[64]);
DP_API uint32_t dp_get_cpsr(dp_cpu* cpu);
DP_API void dp_set_cpsr(dp_cpu* cpu, uint32_t value);
DP_API uint32_t dp_get_fpscr(dp_cpu* cpu);
DP_API void dp_set_fpscr(dp_cpu* cpu, uint32_t value);

/* Sets PC (bit0 selects Thumb, like BX) and runs until a stop or max_ticks instructions. */
DP_API uint32_t dp_run(dp_cpu* cpu, uint32_t pc, uint64_t max_ticks, dp_stop* out);
/* Thread-safe: requests dp_run to return with DP_STOP_HALT at the next block boundary. */
DP_API void dp_halt(dp_cpu* cpu);

/* Benchmark helper: repeatedly call a guest function at pc whose return lands in a trap,
 * entirely in C (measures entry/exit cost without ctypes). Returns calls completed. */
DP_API uint32_t dp_call_loop(dp_cpu* cpu, uint32_t pc, uint32_t return_trap, uint32_t sp, uint32_t calls,
                             uint32_t r0);

#ifdef __cplusplus
}
#endif
