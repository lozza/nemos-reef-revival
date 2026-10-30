"""Warm-start desktop ARM JIT adapter, activated after the initial loading frames.

Unicorn owns saved CPU contexts and Python platform callbacks. Native calls use
JIT registers directly; Python callbacks receive synchronized complete state.
"""
import ctypes as C
from pathlib import Path
import time
import struct
import unicorn as uc
from unicorn import arm_const as A
from jit_api import load, library_path, Stop, STOP_TRAP, STOP_HALT, STOP_UNSUPPORTED


def record_hooks():
    """Call before constructing the private game to retain hook ranges."""
    from unicorn.unicorn_py3.unicorn import Uc
    original_add, original_del = Uc.hook_add, Uc.hook_del
    def add(machine, htype, callback, user_data=None, begin=1, end=0, *args, **kwargs):
        def observed(*values):
            if not getattr(machine, '_jit_skip_hooks', False):
                return callback(*values)
        handle = original_add(machine, htype, observed, user_data, begin, end, *args, **kwargs)
        if not hasattr(machine, '_jit_hook_records'):
            machine._jit_hook_records = {}
        machine._jit_hook_records[handle] = (htype, begin, end, callback, user_data)
        backend = getattr(machine,'_jit_backend',None)
        if backend is not None and backend.inside:
            backend.hooks_changed()
        return handle
    def remove(machine, handle):
        # A self-removing observer is still executing its ctypes trampoline.
        # Keep that thunk alive until after execution has left the callback.
        thunk = machine._callbacks.get(handle)
        if thunk is not None:
            from collections import deque
            if not hasattr(machine,'_jit_retired_thunks'):
                machine._jit_retired_thunks = deque(maxlen=4096)
            machine._jit_retired_thunks.append(thunk)
        original_del(machine, handle)
        getattr(machine, '_jit_hook_records', {}).pop(handle, None)
        backend = getattr(machine,'_jit_backend',None)
        if backend is not None and backend.inside:
            backend.hooks_changed()
    Uc.hook_add, Uc.hook_del = add, remove


class WarmJit:
    def __init__(self, runtime):
        self.runtime, self.machine = runtime, runtime.machine
        self.lib = load()
        self.handle = self.lib.dp_create(1)
        if not self.handle:
            raise RuntimeError('Could not initialize private JIT')
        self.original_start, self.original_stop = self.machine.emu_start, self.machine.emu_stop
        self.original_write = self.machine.mem_write
        self.original_map,self.original_map_ptr = self.machine.mem_map,self.machine.mem_map_ptr
        self.original_unmap,self.original_protect = self.machine.mem_unmap,self.machine.mem_protect
        self.memory = []
        self.stopped = False
        self.inside = False
        self.hits = 0
        self.fallbacks = 0
        self.gregs = [getattr(A, f'UC_ARM_REG_R{n}') for n in range(13)] + [A.UC_ARM_REG_SP, A.UC_ARM_REG_LR, A.UC_ARM_REG_PC]
        self.dregs = [getattr(A, f'UC_ARM_REG_D{n}') for n in range(32)]
        self.hooks = dict(getattr(self.machine, '_jit_hook_records', {}))
        bridge = getattr(runtime, '_native_bridge', None)
        if bridge is not None:
            self.hooks[runtime.import_hook] = (uc.UC_HOOK_CODE, 0x06000000, 0x060fffff,
                self.machine._callbacks[runtime.import_hook], bridge.context)
        self.traps = set()
        for start, end, perms in list(self.machine.mem_regions()):
            size = end-start+1
            snapshot = bytes(self.machine.mem_read(start, size))
            backing = C.create_string_buffer(snapshot, size)
            self.machine.mem_unmap(start, size)
            self.machine.mem_map_ptr(start, size, perms, C.addressof(backing))
            self.memory.append(backing)
            if start == runtime.heap.base and size == runtime.heap.size:
                runtime.heap.backing = backing
                runtime.heap.host_base = C.addressof(backing)
            if self.lib.dp_map(self.handle, start, size, C.addressof(backing)):
                raise RuntimeError('Private JIT rejected mapped region')
        self.machine._jit_memory_owners = self.memory
        self.update_hooks()
        self.machine.emu_start = self.start
        self.machine.emu_stop = self.stop
        self.machine.mem_write = self.write
        self.machine.mem_map,self.machine.mem_map_ptr = self.map,self.map_ptr
        self.machine.mem_unmap,self.machine.mem_protect = self.unmap,self.protect
        self.machine._jit_backend = self

    def map(self,address,size,perms=uc.UC_PROT_ALL):
        if (address|size)&4095 or perms!=uc.UC_PROT_ALL:
            raise ValueError('JIT mappings require aligned readable/writable/executable pages')
        backing = C.create_string_buffer(size)
        self.map_ptr(address,size,perms,C.addressof(backing))
        self.memory.append(backing)

    def map_ptr(self,address,size,perms,pointer):
        if (address|size)&4095 or perms!=uc.UC_PROT_ALL:
            raise ValueError('JIT mappings require aligned readable/writable/executable pages')
        self.original_map_ptr(address,size,perms,pointer)
        if self.lib.dp_map(self.handle,address,size,pointer):
            self.original_unmap(address,size)
            raise RuntimeError('JIT mapping failed')

    def unmap(self,address,size):
        if (address|size)&4095:
            raise ValueError('JIT unmapping requires aligned pages')
        self.original_unmap(address,size)
        self.lib.dp_unmap(self.handle,address,size)

    def protect(self,address,size,perms=uc.UC_PROT_ALL):
        if perms!=uc.UC_PROT_ALL:
            raise ValueError('Restricted JIT page permissions are unsupported')
        self.original_protect(address,size,perms)

    def update_hooks(self):
        self.hooks.update(getattr(self.machine, '_jit_hook_records', {}))
        for token, (kind, start, end, callback, data) in self.hooks.items():
            if token not in self.machine._callbacks or not kind & uc.UC_HOOK_CODE:
                continue
            if end < start:
                raise RuntimeError('Global code hooks are unsupported in private JIT')
            if (start, end) not in self.traps:
                self.lib.dp_add_trap(self.handle, start, end-start+1)
                self.traps.add((start, end))

    def into_jit(self):
        self.lib.dp_set_cpsr(self.handle, self.machine.reg_read(A.UC_ARM_REG_CPSR))
        self.lib.dp_set_regs(self.handle, (C.c_uint32*16)(*self.machine.reg_read_batch(self.gregs)))
        doubles = self.machine.reg_read_batch(self.dregs)
        words = struct.unpack('<64I', struct.pack('<32Q', *doubles))
        self.lib.dp_set_ext_regs(self.handle, (C.c_uint32*64)(*words))
        self.lib.dp_set_fpscr(self.handle, self.machine.reg_read(A.UC_ARM_REG_FPSCR))

    def into_unicorn(self):
        regs, ext = (C.c_uint32*16)(), (C.c_uint32*64)()
        self.lib.dp_get_regs(self.handle, regs)
        self.lib.dp_get_ext_regs(self.handle, ext)
        cpsr = self.lib.dp_get_cpsr(self.handle)
        self.machine.reg_write(A.UC_ARM_REG_CPSR, cpsr)
        self.machine.reg_write_batch(list(zip(self.gregs[:-1], regs[:-1])))
        self.machine.reg_write(A.UC_ARM_REG_PC, regs[15] | ((cpsr >> 5) & 1))
        doubles = struct.unpack('<32Q', bytes(ext))
        self.machine.reg_write_batch(list(zip(self.dregs, doubles)))
        self.machine.reg_write(A.UC_ARM_REG_FPSCR, self.lib.dp_get_fpscr(self.handle))

    def stop(self):
        self.stopped = True
        if hasattr(self, 'dispatcher'):
            self.dispatch_lib.jd_stop(C.byref(self.dispatcher))
        self.lib.dp_halt(self.handle)
        self.original_stop()

    def enable_dispatch(self):
        from unicorn.unicorn_py3.unicorn import uclib
        from compiled_bridge import Api
        class Dispatcher(C.Structure):
            _fields_ = [('engine',C.c_void_p),('cpu',C.c_void_p),('read',C.c_void_p),('write',C.c_void_p),
                        ('ids',C.c_int*50),('values',C.c_uint64*50),('pointers',C.c_void_p*50),
                        ('hits',C.c_uint64),('stopped',C.c_int),('saved_api',Api),
                        ('bridge',C.c_void_p),('saved_fallback',C.c_void_p),('native_callback',C.c_void_p),('fetch',C.c_void_p),('hooks_changed',C.c_int)]
        class Hook(C.Structure):
            _fields_ = [('begin',C.c_uint64),('end',C.c_uint64),('callback',C.c_void_p),('data',C.c_void_p),('full',C.c_int)]
        self.Hook = Hook
        self.dispatch_lib = C.CDLL(str(library_path('jit_dispatch_v7.dll')))
        self.dispatcher = Dispatcher()
        lib = self.dispatch_lib
        lib.jd_init.argtypes = [C.POINTER(Dispatcher),C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,C.POINTER(C.c_int),C.c_void_p]
        lib.jd_init.restype = None
        lib.jd_run.argtypes = [C.POINTER(Dispatcher),C.c_uint32,C.c_uint32,C.c_uint64,C.POINTER(Hook),C.c_uint32,C.POINTER(Stop),C.c_double]
        lib.jd_run.restype = C.c_uint32
        lib.jd_stop.argtypes = [C.POINTER(Dispatcher)]
        lib.jd_stop.restype = None
        lib.jd_hooks_changed.argtypes = [C.POINTER(Dispatcher)]
        lib.jd_hooks_changed.restype = None
        lib.jd_bind_bridge.argtypes = [C.POINTER(Dispatcher),C.c_void_p,C.c_void_p]
        lib.jd_bind_bridge.restype = C.c_int
        lib.jd_unbind_bridge.argtypes = [C.POINTER(Dispatcher)]
        lib.jd_unbind_bridge.restype = None
        lib.jd_init(C.byref(self.dispatcher),self.machine._uch,self.handle,
                    C.cast(uclib.uc_reg_read_batch,C.c_void_p),C.cast(uclib.uc_reg_write_batch,C.c_void_p),
                    (C.c_int*50)(A.UC_ARM_REG_CPSR,*self.gregs,*self.dregs,A.UC_ARM_REG_FPSCR),
                    C.cast(uclib.uc_mem_read,C.c_void_p))
        self.machine.emu_start = self.start_dispatch

    def enable_native_proxy(self):
        bridge = getattr(self.runtime,'_native_bridge',None)
        if bridge is None:
            raise RuntimeError('The private native proxy needs an installed bridge')
        callback = self.machine._callbacks[bridge.native_handle]
        status = self.dispatch_lib.jd_bind_bridge(C.byref(self.dispatcher),bridge.context,
                                                  C.cast(callback,C.c_void_p))
        if status:
            raise RuntimeError(f'Private native proxy rejected binding: {status}')

    def hooks_changed(self):
        if hasattr(self,'dispatcher'):
            self.dispatch_lib.jd_hooks_changed(C.byref(self.dispatcher))

    def start_dispatch(self, begin, until, timeout=0, count=0):
        self.inside = True
        try:
            return self.dispatch_inner(begin,until,timeout,count)
        finally:
            self.inside = False

    def dispatch_inner(self, begin, until, timeout=0, count=0):
        self.stopped = False
        self.machine._hook_exception = None
        deadline = time.monotonic()+timeout/1e6 if timeout else float('inf')
        if (until, until) not in self.traps:
            self.lib.dp_add_trap(self.handle, until, 1)
            self.traps.add((until, until))
        pc = begin
        while not self.stopped and time.monotonic() < deadline:
            self.update_hooks()
            rows = []
            for token, (kind, start, end, callback, data) in self.hooks.items():
                if kind & uc.UC_HOOK_CODE and token in self.machine._callbacks:
                    thunk = self.machine._callbacks[token]
                    userdata = data if isinstance(callback,C._CFuncPtr) else None
                    # Imports share a broad hook. Split out context-capturing
                    # pthread_create so the C dispatcher can synchronize FP state.
                    rows.append(self.Hook(start,end,C.cast(thunk,C.c_void_p),userdata,int(not isinstance(callback,C._CFuncPtr))))
            for address,name in self.runtime.import_names.items():
                if name == 'pthread_create':
                    rows.append(self.Hook(address,address,None,None,1))
            hooks = (self.Hook*len(rows))(*rows)
            stop = Stop()
            remaining = max(0,deadline-time.monotonic()) if timeout else -1
            reason = self.dispatch_lib.jd_run(C.byref(self.dispatcher),pc,until,count or 1<<50,hooks,len(rows),C.byref(stop),remaining)
            if self.machine._hook_exception:
                raise self.machine._hook_exception
            pc = self.machine.reg_read(A.UC_ARM_REG_PC)
            if self.stopped or pc == until or reason in (0,STOP_HALT):
                return
            if reason == 512:
                pc |= int(bool(self.machine.reg_read(A.UC_ARM_REG_CPSR)&32))
                continue
            if reason not in (128,STOP_UNSUPPORTED):
                raise RuntimeError(f'Private JIT dispatch stopped: reason={reason} pc={pc:#x} detail={stop.detail} fault={stop.fault_addr:#x}')
            self.fallbacks += 1
            thumb = bool(self.machine.reg_read(A.UC_ARM_REG_CPSR) & 32)
            self.machine._jit_skip_hooks = reason == 128
            try:
                self.original_start(pc | int(thumb),until,timeout=0,count=1)
            finally:
                self.machine._jit_skip_hooks = False
            pc = self.machine.reg_read(A.UC_ARM_REG_PC) | int(bool(self.machine.reg_read(A.UC_ARM_REG_CPSR)&32))

    def write(self, address, data):
        self.original_write(address, data)
        if data:
            self.lib.dp_invalidate(self.handle, address, len(data))

    def start(self, begin, until, timeout=0, count=0):
        self.stopped = False
        self.machine._hook_exception = None
        deadline = time.monotonic()+timeout/1e6 if timeout else float('inf')
        if (until, until) not in self.traps:
            self.lib.dp_add_trap(self.handle, until, 1)
            self.traps.add((until, until))
        self.into_jit()
        pc, ticks = begin, 0
        self.inside = True
        try:
            while not self.stopped and time.monotonic() < deadline:
                stop = Stop()
                reason = self.lib.dp_run(self.handle, pc, count-ticks if count else 1 << 50, C.byref(stop))
                ticks += stop.ticks
                if reason & (STOP_TRAP | STOP_UNSUPPORTED):
                    self.lib.dp_set_reg(self.handle, 15, stop.pc)
                self.into_unicorn()
                pc = self.machine.reg_read(A.UC_ARM_REG_PC)
                if pc == until or self.stopped or reason == STOP_HALT:
                    return
                if reason == STOP_TRAP:
                    self.hits += 1
                    callbacks = [(token, callback, data) for token, (kind, start, end, callback, data) in self.hooks.items()
                                 if kind & uc.UC_HOOK_CODE and start <= pc <= end and token in self.machine._callbacks]
                    thumb = bool(self.machine.reg_read(A.UC_ARM_REG_CPSR) & 32)
                    size = 2 if thumb else 4
                    for token, callback, data in callbacks:
                        if isinstance(callback, C._CFuncPtr):
                            callback(self.machine._uch, pc, size, data)
                        else:
                            callback(self.machine, pc, size, data)
                        if self.machine._hook_exception:
                            raise self.machine._hook_exception
                    after = self.machine.reg_read(A.UC_ARM_REG_PC)
                    if not self.stopped and after == pc:
                        # Observer hooks do not replace the instruction. Execute
                        # it once without invoking those same observers again.
                        self.fallbacks += 1
                        self.machine._jit_skip_hooks = True
                        try:
                            self.original_start(pc | int(thumb), until, timeout=1000000, count=1)
                        finally:
                            self.machine._jit_skip_hooks = False
                    self.update_hooks()
                elif reason == STOP_UNSUPPORTED:
                    self.fallbacks += 1
                    thumb = bool(self.machine.reg_read(A.UC_ARM_REG_CPSR) & 32)
                    self.original_start(pc | int(thumb), until, timeout=1000000, count=1)
                elif reason == 0:
                    return
                else:
                    raise RuntimeError(f'Private JIT stopped: reason={reason} pc={pc:#x} detail={stop.detail} fault={stop.fault_addr:#x}')
                if self.stopped or (count and ticks >= count):
                    return
                self.into_jit()
                pc = self.machine.reg_read(A.UC_ARM_REG_PC) | int(bool(self.machine.reg_read(A.UC_ARM_REG_CPSR) & 32))
        finally:
            self.inside = False

    def close(self):
        if self.handle is None:
            return
        if hasattr(self,'dispatcher'):
            self.dispatch_lib.jd_unbind_bridge(C.byref(self.dispatcher))
        self.machine.emu_start, self.machine.emu_stop = self.original_start, self.original_stop
        self.machine.mem_write = self.original_write
        self.machine.mem_map,self.machine.mem_map_ptr = self.original_map,self.original_map_ptr
        self.machine.mem_unmap,self.machine.mem_protect = self.original_unmap,self.original_protect
        self.machine._jit_backend = None
        self.lib.dp_destroy(self.handle)
        self.handle = None
