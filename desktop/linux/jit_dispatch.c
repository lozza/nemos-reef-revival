/* Private Dynarmic/Unicorn callback dispatcher; never bundled in the game. */
/* Linux/ELF portable copy of work/jit-runtime/jit_dispatch.c: identical ABI and
   semantics; only exports, TLS and the monotonic clock (seconds) are platform code. */
#include <stdint.h>
#include <string.h>
#include "dynarmic_probe.h"
#include "../native/bridge_api.h"
#include <time.h>
#define JD_API __attribute__((visibility("default")))

/* Same unit as native_bridge.c now_seconds(): QueryPerformanceCounter seconds on Windows. */
static double now_seconds(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC,&value);
    return (double)value.tv_sec+(double)value.tv_nsec*1e-9;
}

typedef int (*uc_batch)(void*, int*, void**, int);
typedef void (*code_hook)(void*, uint64_t, uint32_t, void*);
typedef struct {
    uint64_t begin, end;
    code_hook callback;
    void *data;
    int full;
} hook;
typedef struct {
    void *engine;
    dp_cpu *cpu;
    uc_batch read, write;
    int ids[50]; /* CPSR, R0..R15, D0..D31, FPSCR */
    uint64_t values[50];
    void *pointers[50];
    uint64_t hits;
    volatile int stopped;
    NemoApi saved_api;
    void *bridge;
    code_hook saved_fallback;
    code_hook native_callback;
    NemoMemRead fetch;
    int hooks_changed;
} dispatcher;

/* Only this private owner thread enters the native bridge through Dynarmic.
   Other execution (including one-instruction Unicorn fallback) uses its
   original C API. The layout below covers the stable bridge prefix only. */
typedef struct {
    NemoApi api;
    void *engine, *entries;
    uint32_t length;
    uint64_t base;
    code_hook fallback;
} bridge_prefix;
static __thread dispatcher *active_dispatcher;
static dispatcher *bound_dispatcher;

JD_API void jd_init(dispatcher *d, void *engine, dp_cpu *cpu,
                                  uc_batch read, uc_batch write, int *ids, NemoMemRead fetch) {
    memset(d,0,sizeof(*d));
    d->engine=engine; d->cpu=cpu; d->read=read; d->write=write;
    d->fetch=fetch;
    memcpy(d->ids,ids,sizeof(d->ids));
    for(int i=0;i<50;i++) d->pointers[i]=&d->values[i];
}

JD_API int jd_into_jit(dispatcher *d) {
    uint32_t regs[16], ext[64];
    int status=d->read(d->engine,d->ids,d->pointers,50);
    if(status) return status;
    dp_set_cpsr(d->cpu,(uint32_t)d->values[0]);
    for(int i=0;i<16;i++) regs[i]=(uint32_t)d->values[i+1];
    for(int i=0;i<32;i++) {
        ext[2*i]=(uint32_t)d->values[i+17];
        ext[2*i+1]=(uint32_t)(d->values[i+17]>>32);
    }
    dp_set_regs(d->cpu,regs); dp_set_ext_regs(d->cpu,ext);
    dp_set_fpscr(d->cpu,(uint32_t)d->values[49]);
    return 0;
}

JD_API int jd_into_unicorn(dispatcher *d) {
    uint32_t regs[16], ext[64], cpsr=dp_get_cpsr(d->cpu);
    dp_get_regs(d->cpu,regs); dp_get_ext_regs(d->cpu,ext);
    d->values[0]=cpsr;
    for(int i=0;i<16;i++) d->values[i+1]=regs[i];
    d->values[16] |= (cpsr>>5)&1;
    for(int i=0;i<32;i++) d->values[i+17]=(uint64_t)ext[2*i] | ((uint64_t)ext[2*i+1]<<32);
    d->values[49]=dp_get_fpscr(d->cpu);
    return d->write(d->engine,d->ids,d->pointers,50);
}

static int scratch(dispatcher *d,int to_uc) {
    /* All normal platform contracts use R0-R3, SP, LR, PC and CPSR.
       Full CPU state is synchronized at execution boundaries and before
       callbacks which capture a guest thread context. */
    const int indices[8]={0,1,2,3,4,14,15,16};
    const int regs[8]={-1,0,1,2,3,13,14,15};
    int ids[8]; void *ptrs[8];
    for(int i=0;i<8;i++) {
        ids[i]=d->ids[indices[i]]; ptrs[i]=&d->values[indices[i]];
        if(to_uc) d->values[indices[i]]=i?dp_get_reg(d->cpu,regs[i]):dp_get_cpsr(d->cpu);
    }
    if(to_uc) {
        d->values[16]|=(d->values[0]>>5)&1;
        return d->write(d->engine,ids,ptrs,8);
    }
    int result=d->read(d->engine,ids,ptrs,8);
    if(result) return result;
    dp_set_cpsr(d->cpu,(uint32_t)d->values[0]);
    for(int i=1;i<8;i++) dp_set_reg(d->cpu,regs[i],(uint32_t)d->values[indices[i]] & (i==7?~1u:~0u));
    return 0;
}

static uint32_t finish(dispatcher *d,uint32_t reason) {
    int status=jd_into_unicorn(d);
    return status?256u+(uint32_t)status:reason;
}

static uint32_t instruction_size(dispatcher *d,uint32_t pc) {
    if(!(dp_get_cpsr(d->cpu)&32)) return 4;
    uint16_t half=0;
    if(d->fetch(d->engine,pc,&half,sizeof(half))) return 2;
    return (half&0xf800u)>=0xe800u?4:2;
}

static int proxy_read(void *engine,int id,void *output) {
    dispatcher *d=active_dispatcher;
    if(!d || engine!=d->engine)
        return bound_dispatcher->saved_api.reg_read(engine,id,output);
    for(int i=1;i<17;i++) if(d->ids[i]==id) {
        *(uint32_t*)output=dp_get_reg(d->cpu,i-1); return 0;
    }
    if(id==d->ids[0]) { *(uint32_t*)output=dp_get_cpsr(d->cpu); return 0; }
    return 15; /* UC_ERR_ARG: the native bridge contract uses only GPRs. */
}

static int proxy_write(void *engine,int id,const void *input) {
    dispatcher *d=active_dispatcher;
    if(!d || engine!=d->engine)
        return bound_dispatcher->saved_api.reg_write(engine,id,input);
    uint32_t value=*(const uint32_t*)input;
    for(int i=1;i<17;i++) if(d->ids[i]==id) {
        if(i==16) {
            uint32_t cpsr=dp_get_cpsr(d->cpu);
            dp_set_cpsr(d->cpu,(cpsr&~32u)|((value&1)<<5));
            value&=~1u;
        }
        dp_set_reg(d->cpu,i-1,value); return 0;
    }
    return 15;
}

static int proxy_stop(void *engine) {
    dispatcher *d=active_dispatcher;
    if(!d || engine!=d->engine)
        return bound_dispatcher->saved_api.emu_stop(engine);
    d->stopped=1;
    dp_halt(d->cpu);
    return 0;
}

static void proxy_fallback(void *engine,uint64_t address,uint32_t size,void *data) {
    dispatcher *d=active_dispatcher;
    if(!d || engine!=d->engine) {
        bound_dispatcher->saved_fallback(engine,address,size,data);
        return;
    }
    /* Python callbacks may capture a complete thread context. Preserve all
       registers here; native successful calls need no Unicorn synchronization. */
    int status=jd_into_unicorn(d);
    if(status) { d->stopped=1; return; }
    d->saved_fallback(engine,address,size,data);
    status=jd_into_jit(d);
    if(status) d->stopped=1;
}

JD_API int jd_bind_bridge(dispatcher *d,void *context,code_hook callback) {
    bridge_prefix *b=(bridge_prefix*)context;
    if(bound_dispatcher || !b || b->engine!=d->engine || !b->fallback || !callback) return -1;
    d->saved_api=b->api; d->saved_fallback=b->fallback;
    d->bridge=context; d->native_callback=callback;
    bound_dispatcher=d;
    b->api.reg_read=proxy_read; b->api.reg_write=proxy_write; b->api.emu_stop=proxy_stop;
    b->fallback=proxy_fallback;
    return 0;
}

JD_API void jd_unbind_bridge(dispatcher *d) {
    if(d->bridge) {
        bridge_prefix *b=(bridge_prefix*)d->bridge;
        b->api=d->saved_api; b->fallback=d->saved_fallback;
        d->bridge=0; d->native_callback=0;
        bound_dispatcher=0;
    }
}

/* Return 128 for an observer instruction requiring one-instruction fallback.
 * Return normal dp_stop reasons for unsupported code, faults or deadlines.
 * All callbacks are original Unicorn C thunks, including the native bridge. */
JD_API uint32_t jd_run(dispatcher *d, uint32_t begin, uint32_t until,
                                     uint64_t budget, hook *hooks, uint32_t count,
                                     dp_stop *out,double remaining) {
    uint32_t pc=begin;
    double deadline=remaining<0?0:now_seconds()+remaining;
    d->stopped=0;
    int status=jd_into_jit(d);
    if(status) return 256u+(uint32_t)status;
    while(!d->stopped) {
        if(deadline) {
            if(now_seconds()>=deadline) return finish(d,DP_STOP_HALT);
        }
        uint32_t reason=dp_run(d->cpu,pc,budget>100000?100000:budget,out);
        if(reason & (DP_STOP_TRAP|DP_STOP_UNSUPPORTED)) dp_set_reg(d->cpu,15,out->pc);
        pc=dp_get_reg(d->cpu,15)&~1u;
        if(reason==DP_STOP_BUDGET && pc!=until && !d->stopped) {
            if(out->ticks>=budget) return finish(d,0);
            budget-=out->ticks;
            pc|=(dp_get_cpsr(d->cpu)>>5)&1;
            continue;
        }
        if(pc==until || reason!=DP_STOP_TRAP || d->stopped) return finish(d,reason);
        if(out->ticks>=budget) return finish(d,0);
        budget-=out->ticks;
        d->hits++;
        int native_only=0, matching=0;
        for(uint32_t i=0;i<count;i++) if(hooks[i].callback && pc>=hooks[i].begin && pc<=hooks[i].end) {
            matching++;
            if(hooks[i].callback==d->native_callback && hooks[i].data==d->bridge) native_only=1;
        }
        if(native_only && matching==1) {
            active_dispatcher=d;
            d->native_callback(d->engine,pc,instruction_size(d,pc),d->bridge);
            active_dispatcher=0;
            if(d->stopped) return finish(d,DP_STOP_HALT);
            uint32_t after=dp_get_reg(d->cpu,15);
            if(d->hooks_changed) {
                d->hooks_changed=0;
                return finish(d,after==pc?128:512);
            }
            if(after==pc) return finish(d,128);
            pc=after|((dp_get_cpsr(d->cpu)>>5)&1);
            continue;
        }
        status=scratch(d,1);
        if(status) return 256u+(uint32_t)status;
        int full=0;
        for(uint32_t i=0;i<count;i++)
            if(pc>=hooks[i].begin && pc<=hooks[i].end && hooks[i].full) full=1;
        if(full) jd_into_unicorn(d);
        for(uint32_t i=0;i<count;i++) {
            if(hooks[i].callback && pc>=hooks[i].begin && pc<=hooks[i].end)
                hooks[i].callback(d->engine,pc,instruction_size(d,pc),hooks[i].data);
        }
        status=full?jd_into_jit(d):scratch(d,0);
        if(status) return 256u+(uint32_t)status;
        if(d->stopped) return finish(d,DP_STOP_HALT);
        uint32_t after=dp_get_reg(d->cpu,15);
        if(d->hooks_changed) {
            d->hooks_changed=0;
            return finish(d,after==pc?128:512);
        }
        if(after==pc) return finish(d,128);
        pc=after | ((dp_get_cpsr(d->cpu)>>5)&1);
    }
    return finish(d,DP_STOP_HALT);
}

JD_API void jd_stop(dispatcher *d) {
    d->stopped=1;
    dp_halt(d->cpu);
}

JD_API void jd_hooks_changed(dispatcher *d) { d->hooks_changed=1; }
