// Linux/ELF portable copy of work/jit-runtime/jit_core.cpp (x64 backend). See dynarmic_probe.h.
#define DP_BUILD
#include "dynarmic_probe.h"
#include "dynarmic/frontend/A32/a32_ir_emitter.h"
#include "dynarmic/ir/terminal.h"

#include <array>
#include <cstdint>
#include <cstring>
#include <memory>
#include <optional>
#include <utility>
#include <vector>

#include "dynarmic/interface/A32/a32.h"
#include "dynarmic/interface/A32/config.h"

using Dynarmic::HaltReason;
using Dynarmic::A32::Exception;
using Dynarmic::A32::VAddr;

namespace {

constexpr HaltReason HR_TRAP = HaltReason::UserDefined1;
constexpr HaltReason HR_SVC = HaltReason::UserDefined2;
constexpr HaltReason HR_EXTERNAL = HaltReason::UserDefined3;
constexpr HaltReason HR_EXEC_FAULT = HaltReason::UserDefined4;
constexpr HaltReason HR_EXCEPTION = HaltReason::UserDefined5;
constexpr HaltReason HR_UNSUPPORTED = HaltReason::UserDefined6;
constexpr HaltReason HR_ALL = HaltReason::MemoryAbort | HR_TRAP | HR_SVC | HR_EXTERNAL | HR_EXEC_FAULT |
                              HR_EXCEPTION | HR_UNSUPPORTED;

constexpr std::uint32_t PAGE = 1u << Dynarmic::A32::UserConfig::PAGE_BITS;
using PageTable = std::array<std::uint8_t*, Dynarmic::A32::UserConfig::NUM_PAGE_TABLE_ENTRIES>;

}  // namespace

struct dp_cpu final : Dynarmic::A32::UserCallbacks {
    std::unique_ptr<PageTable> pages = std::make_unique<PageTable>();
    std::vector<std::pair<std::uint32_t, std::uint32_t>> traps;  // [start, end)
    std::unique_ptr<Dynarmic::A32::Jit> jit;
    std::uint64_t ticks_left = 0;
    std::uint64_t ticks_done = 0;
    dp_stop stop{};

    explicit dp_cpu(std::uint32_t flags) {
        Dynarmic::A32::UserConfig config{};
        config.callbacks = this;
        config.page_table = pages.get();
        // Straddling accesses fall back to the byte-wise callbacks so separate host blocks stay safe.
        config.detect_misaligned_access_via_page_table = 16 | 32 | 64;
        config.only_detect_misalignment_via_page_table_on_page_boundary = true;
        config.check_halt_on_memory_access = (flags & DP_FLAG_PRECISE_DATA_FAULTS) != 0;
        config.always_little_endian = true;
        config.define_unpredictable_behaviour = true;
        config.code_cache_size = 64 * 1024 * 1024;
        if (flags & DP_FLAG_NO_OPTIMIZATIONS)
            config.optimizations = Dynarmic::no_optimizations;
        jit = std::make_unique<Dynarmic::A32::Jit>(config);
    }

    bool InTrap(VAddr vaddr) const {
        for (const auto& [start, end] : traps)
            if (vaddr >= start && vaddr < end)
                return true;
        return false;
    }

    std::uint8_t* Host(VAddr vaddr) const {
        std::uint8_t* page = (*pages)[vaddr >> 12];
        return page ? page + (vaddr & (PAGE - 1)) : nullptr;
    }

    void DataFault(VAddr vaddr, std::uint32_t size, bool write) {
        if (!(stop.reasons & DP_STOP_DATA_FAULT)) {
            stop.reasons |= DP_STOP_DATA_FAULT;
            stop.fault_addr = vaddr;
            stop.fault_size = size;
            stop.fault_write = write ? 1 : 0;
        }
        jit->HaltExecution(HaltReason::MemoryAbort);
    }

    template<typename T>
    T Read(VAddr vaddr) {
        T value{};
        auto* bytes = reinterpret_cast<std::uint8_t*>(&value);
        for (std::uint32_t i = 0; i < sizeof(T); i++) {
            const std::uint8_t* host = Host(vaddr + i);
            if (!host) {
                DataFault(vaddr, sizeof(T), false);
                return 0;
            }
            bytes[i] = *host;
        }
        return value;
    }

    template<typename T>
    void Write(VAddr vaddr, T value) {
        const auto* bytes = reinterpret_cast<const std::uint8_t*>(&value);
        for (std::uint32_t i = 0; i < sizeof(T); i++) {
            auto* host=Host(vaddr+i);
            if(!host) { DataFault(vaddr+i,sizeof(T),true); return; }
            *host=bytes[i];
        }
    }

    std::optional<std::uint32_t> MemoryReadCode(VAddr vaddr) override {
        // Translation-time: trap and unmapped words become NoExecuteFault at that instruction.
        if (!Host(vaddr) || !Host(vaddr + 3))
            return std::nullopt;
        std::uint32_t value;
        std::memcpy(&value, Host(vaddr), 4);
        return value;
    }

    bool PreCodeReadHook(bool thumb, VAddr pc, Dynarmic::A32::IREmitter& ir) override {
        if (!InTrap(pc)) {
            // CP15 operations need the original Unicorn coprocessor model.
            // Stop before translation: an absent Dynarmic coprocessor can abort
            // the process rather than return an interpreter fallback.
            std::uint32_t instruction=0;
            bool available=true;
            for(unsigned i=0;i<4;i++) {
                const auto* p=Host(pc+i);
                if(!p) { available=false; break; }
                instruction|=std::uint32_t(*p)<<(i*8);
            }
            const bool wide=!thumb || (instruction&0xf800u)>=0xe800u;
            if(thumb) instruction=(instruction<<16)|(instruction>>16);
            if(available && wide && (instruction&0x0c000f00u)==0x0c000f00u) {
                ir.SetTerm(Dynarmic::IR::Term::Interpret(ir.current_location));
                return false;
            }
            return true;
        }
        ir.UpdateUpperLocationDescriptor();
        ir.BranchWritePC(ir.Imm32(pc));
        ir.ExceptionRaised(Exception::NoExecuteFault);
        ir.SetTerm(Dynarmic::IR::Term::CheckHalt{Dynarmic::IR::Term::ReturnToDispatch{}});
        return false;
    }

    std::uint8_t MemoryRead8(VAddr a) override { return Read<std::uint8_t>(a); }
    std::uint16_t MemoryRead16(VAddr a) override { return Read<std::uint16_t>(a); }
    std::uint32_t MemoryRead32(VAddr a) override { return Read<std::uint32_t>(a); }
    std::uint64_t MemoryRead64(VAddr a) override { return Read<std::uint64_t>(a); }
    void MemoryWrite8(VAddr a, std::uint8_t v) override { Write(a, v); }
    void MemoryWrite16(VAddr a, std::uint16_t v) override { Write(a, v); }
    void MemoryWrite32(VAddr a, std::uint32_t v) override { Write(a, v); }
    void MemoryWrite64(VAddr a, std::uint64_t v) override { Write(a, v); }

    // Single-core prototype: exclusives always succeed after doing the store.
    bool MemoryWriteExclusive8(VAddr a, std::uint8_t v, std::uint8_t) override { Write(a, v); return true; }
    bool MemoryWriteExclusive16(VAddr a, std::uint16_t v, std::uint16_t) override { Write(a, v); return true; }
    bool MemoryWriteExclusive32(VAddr a, std::uint32_t v, std::uint32_t) override { Write(a, v); return true; }
    bool MemoryWriteExclusive64(VAddr a, std::uint64_t v, std::uint64_t) override { Write(a, v); return true; }

    void InterpreterFallback(VAddr pc, std::size_t) override {
        // Dynarmic set PC to this instruction before calling; hand it back to the host.
        stop.reasons |= DP_STOP_UNSUPPORTED;
        stop.pc = pc;
        jit->HaltExecution(HR_UNSUPPORTED);
    }

    void CallSVC(std::uint32_t swi) override {
        stop.reasons |= DP_STOP_SVC;
        stop.detail = swi;
        stop.pc = jit->Regs()[15];
        jit->HaltExecution(HR_SVC);
    }

    void ExceptionRaised(VAddr pc, Exception exception) override {
        stop.pc = pc;
        if (exception == Exception::NoExecuteFault) {
            const bool trap = InTrap(pc);
            stop.reasons |= trap ? DP_STOP_TRAP : DP_STOP_EXEC_FAULT;
            jit->HaltExecution(trap ? HR_TRAP : HR_EXEC_FAULT);
            return;
        }
        stop.reasons |= DP_STOP_EXCEPTION;
        stop.detail = static_cast<std::uint32_t>(exception);
        jit->HaltExecution(HR_EXCEPTION);
    }

    void AddTicks(std::uint64_t ticks) override {
        ticks_done += ticks;
        ticks_left = ticks > ticks_left ? 0 : ticks_left - ticks;
    }
    std::uint64_t GetTicksRemaining() override { return ticks_left > 100000 ? 100000 : ticks_left; }

    std::uint32_t Run(std::uint32_t pc, std::uint64_t max_ticks) {
        stop = {};
        std::uint32_t cpsr = jit->Cpsr();
        cpsr = (pc & 1) ? (cpsr | 0x20u) : (cpsr & ~0x20u);
        jit->SetCpsr(cpsr);
        jit->Regs()[15] = (pc & 1) ? (pc & ~1u) : (pc & ~3u);
        ticks_left = max_ticks;
        ticks_done = 0;
        HaltReason hr{}; do { hr = jit->Run(); } while (!hr && ticks_left);
        if (Dynarmic::Has(hr, HR_EXTERNAL))
            stop.reasons |= DP_STOP_HALT;
        if (Dynarmic::Has(hr, HaltReason::MemoryAbort))
            stop.pc = jit->Regs()[15];  // precise only with DP_FLAG_PRECISE_DATA_FAULTS
        jit->ClearHalt(HR_ALL);
        stop.ticks = ticks_done;
        return stop.reasons;
    }
};

extern "C" {

DP_API std::uint32_t dp_version(void) { return 1; }

DP_API dp_cpu* dp_create(std::uint32_t flags) {
    try {
        return new dp_cpu(flags);
    } catch (...) {
        return nullptr;
    }
}

DP_API void dp_destroy(dp_cpu* cpu) { delete cpu; }

DP_API int dp_map(dp_cpu* cpu, std::uint32_t addr, std::uint32_t size, void* host) {
    if ((addr | size) & (PAGE - 1) || !host)
        return -1;
    auto* base = static_cast<std::uint8_t*>(host);
    for (std::uint64_t offset = 0; offset < size; offset += PAGE)
        (*cpu->pages)[(addr + offset) >> 12] = base + offset;
    cpu->jit->InvalidateCacheRange(addr, size);
    return 0;
}

DP_API int dp_unmap(dp_cpu* cpu, std::uint32_t addr, std::uint32_t size) {
    if ((addr | size) & (PAGE - 1))
        return -1;
    for (std::uint64_t offset = 0; offset < size; offset += PAGE)
        (*cpu->pages)[(addr + offset) >> 12] = nullptr;
    cpu->jit->InvalidateCacheRange(addr, size);
    return 0;
}

DP_API int dp_add_trap(dp_cpu* cpu, std::uint32_t addr, std::uint32_t size) {
    cpu->traps.emplace_back(addr, addr + size);
    cpu->jit->InvalidateCacheRange(addr, size);
    return 0;
}

DP_API void dp_invalidate(dp_cpu* cpu, std::uint32_t addr, std::uint32_t size) { cpu->jit->InvalidateCacheRange(addr, size); }
DP_API void dp_clear_cache(dp_cpu* cpu) { cpu->jit->ClearCache(); }

DP_API void dp_get_regs(dp_cpu* cpu, std::uint32_t out[16]) { std::memcpy(out, cpu->jit->Regs().data(), 64); }
DP_API void dp_set_regs(dp_cpu* cpu, const std::uint32_t in[16]) { std::memcpy(cpu->jit->Regs().data(), in, 64); }
DP_API std::uint32_t dp_get_reg(dp_cpu* cpu, std::uint32_t i) { return cpu->jit->Regs()[i & 15]; }
DP_API void dp_set_reg(dp_cpu* cpu, std::uint32_t i, std::uint32_t v) { cpu->jit->Regs()[i & 15] = v; }
DP_API void dp_get_ext_regs(dp_cpu* cpu, std::uint32_t out[64]) { std::memcpy(out, cpu->jit->ExtRegs().data(), 256); }
DP_API void dp_set_ext_regs(dp_cpu* cpu, const std::uint32_t in[64]) { std::memcpy(cpu->jit->ExtRegs().data(), in, 256); }
DP_API std::uint32_t dp_get_cpsr(dp_cpu* cpu) { return cpu->jit->Cpsr(); }
DP_API void dp_set_cpsr(dp_cpu* cpu, std::uint32_t v) { cpu->jit->SetCpsr(v); }
DP_API std::uint32_t dp_get_fpscr(dp_cpu* cpu) { return cpu->jit->Fpscr(); }
DP_API void dp_set_fpscr(dp_cpu* cpu, std::uint32_t v) { cpu->jit->SetFpscr(v); }

DP_API std::uint32_t dp_run(dp_cpu* cpu, std::uint32_t pc, std::uint64_t max_ticks, dp_stop* out) {
    const std::uint32_t reasons = cpu->Run(pc, max_ticks);
    if (out)
        *out = cpu->stop;
    return reasons;
}

DP_API void dp_halt(dp_cpu* cpu) { cpu->jit->HaltExecution(HR_EXTERNAL); }

DP_API std::uint32_t dp_call_loop(dp_cpu* cpu, std::uint32_t pc, std::uint32_t return_trap, std::uint32_t sp,
                                  std::uint32_t calls, std::uint32_t r0) {
    for (std::uint32_t i = 0; i < calls; i++) {
        auto& regs = cpu->jit->Regs();
        regs[0] = r0;
        regs[13] = sp;
        regs[14] = return_trap;
        if (cpu->Run(pc, ~0ull) != DP_STOP_TRAP || cpu->stop.pc != (return_trap & ~1u))
            return i;
    }
    return calls;
}

}  // extern "C"
