"""Synthetic ARM smoke test for the Linux JIT core and dispatcher (no APK, no Unicorn).

Libraries are looked up in $NEMO_LINUX_NATIVE_DIR, else desktop/linux/build.
"""
import ctypes as C
import os
from pathlib import Path
import struct
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import jit_api  # noqa: E402

LIBS = Path(os.environ.get('NEMO_LINUX_NATIVE_DIR',HERE/'build'))
CORE, DISPATCH, BRIDGE = LIBS/'libjit_core.so', LIBS/'libjit_dispatch_v7.so', LIBS/'libnemo_bridge.so'
CODE, DATA, TRAP = 0x10000, 0x20000, 0x10f00
STOP_BUDGET, STOP_SVC, STOP_DATA_FAULT = 0, 2, 8
# mov r0,#0; mov r1,#10; loop: add r0,r0,r1; subs r1,r1,#1; bne loop; bx lr
SUM_LOOP = struct.pack('<6I',0xe3a00000,0xe3a0100a,0xe0800001,0xe2511001,0x1afffffc,0xe12fff1e)
SPIN = struct.pack('<I',0xeafffffe)  # b .


@unittest.skipUnless(sys.platform.startswith('linux') and CORE.exists(),'build desktop/linux first')
class LinuxCoreTests(unittest.TestCase):
    def setUp(self):
        self.lib = jit_api.load(CORE)
        self.cpu = self.lib.dp_create(0)
        self.assertTrue(self.cpu)
        self.addCleanup(self.lib.dp_destroy,self.cpu)
        self.code = (C.c_uint8*4096)()
        self.data = (C.c_uint8*4096)()
        self.assertEqual(self.lib.dp_map(self.cpu,CODE,4096,self.code),0)
        self.assertEqual(self.lib.dp_map(self.cpu,DATA,4096,self.data),0)
        self.assertEqual(self.lib.dp_add_trap(self.cpu,TRAP,4),0)
        self.lib.dp_set_cpsr(self.cpu,0x10)

    def load_code(self,blob,offset=0):
        C.memmove(C.addressof(self.code)+offset,blob,len(blob))
        self.lib.dp_invalidate(self.cpu,CODE,4096)

    def run_at(self,pc,ticks=1<<20):
        stop = jit_api.Stop()
        self.lib.dp_set_reg(self.cpu,13,DATA+4096)
        self.lib.dp_set_reg(self.cpu,14,TRAP)
        return self.lib.dp_run(self.cpu,pc,ticks,C.byref(stop)),stop

    def test_arm_bounded_loop_returns_to_trap(self):
        self.load_code(SUM_LOOP)
        reason,stop = self.run_at(CODE)
        self.assertEqual((reason,stop.pc),(jit_api.STOP_TRAP,TRAP))
        self.assertEqual(self.lib.dp_get_reg(self.cpu,0),55)
        self.assertEqual(self.lib.dp_get_reg(self.cpu,1),0)
        self.assertGreater(stop.ticks,30)

    def test_thumb16_and_thumb32(self):
        self.load_code(bytes.fromhex('07207047'))
        self.assertEqual(self.run_at(CODE|1)[0],jit_api.STOP_TRAP)
        self.assertEqual(self.lib.dp_get_reg(self.cpu,0),7)
        self.load_code(bytes.fromhex('41f234207047'),0x100)
        self.assertEqual(self.run_at((CODE+0x100)|1)[0],jit_api.STOP_TRAP)
        self.assertEqual(self.lib.dp_get_reg(self.cpu,0),0x1234)

    def test_register_block_round_trip(self):
        regs = (C.c_uint32*16)(*[0x1000+n for n in range(16)])
        ext = (C.c_uint32*64)(*[0xa0000000+n for n in range(64)])
        self.lib.dp_set_regs(self.cpu,regs); self.lib.dp_set_ext_regs(self.cpu,ext)
        self.lib.dp_set_fpscr(self.cpu,0x03000000)
        out, out_ext = (C.c_uint32*16)(), (C.c_uint32*64)()
        self.lib.dp_get_regs(self.cpu,out); self.lib.dp_get_ext_regs(self.cpu,out_ext)
        self.assertEqual(list(out),list(regs))
        self.assertEqual(list(out_ext),list(ext))
        self.assertEqual(self.lib.dp_get_fpscr(self.cpu),0x03000000)

    def test_budget_svc_and_data_fault(self):
        self.load_code(SPIN)
        reason,stop = self.run_at(CODE,1000)
        self.assertEqual(reason,STOP_BUDGET)
        self.assertGreaterEqual(stop.ticks,1000)
        self.assertLess(stop.ticks,2000)
        self.load_code(struct.pack('<2I',0xef000042,0xe12fff1e),0x200)
        reason,stop = self.run_at(CODE+0x200)
        self.assertEqual((reason,stop.detail),(STOP_SVC,0x42))
        self.load_code(struct.pack('<2I',0xe5910000,0xe12fff1e),0x300)  # ldr r0,[r1]
        self.lib.dp_set_reg(self.cpu,1,0xdead0000)
        reason,stop = self.run_at(CODE+0x300)
        self.assertTrue(reason & STOP_DATA_FAULT)
        self.assertEqual((stop.fault_addr,stop.fault_size,stop.fault_write),(0xdead0000,4,0))


BATCH = C.CFUNCTYPE(C.c_int,C.c_void_p,C.POINTER(C.c_int),C.POINTER(C.c_void_p),C.c_int)
FETCH = C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint64,C.c_void_p,C.c_size_t)
CODE_HOOK = C.CFUNCTYPE(None,C.c_void_p,C.c_uint64,C.c_uint32,C.c_void_p)


class Hook(C.Structure):
    _fields_ = [('begin',C.c_uint64),('end',C.c_uint64),('callback',C.c_void_p),('data',C.c_void_p),('full',C.c_int)]


@unittest.skipUnless(sys.platform.startswith('linux') and DISPATCH.exists(),'build desktop/linux first')
class LinuxDispatchTests(LinuxCoreTests):
    """Drives jd_run with a fake register engine in place of Unicorn."""
    EXPORTS = ('jd_init','jd_into_jit','jd_into_unicorn','jd_bind_bridge','jd_unbind_bridge',
               'jd_run','jd_stop','jd_hooks_changed')
    IDS = list(range(1000,1050))  # CPSR, R0..R15, D0..D31, FPSCR

    def setUp(self):
        super().setUp()
        lib = self.dlib = C.CDLL(str(DISPATCH))
        p = C.c_void_p
        lib.jd_init.argtypes = [p,p,p,p,p,C.POINTER(C.c_int),p]
        lib.jd_init.restype = None
        lib.jd_run.argtypes = [p,C.c_uint32,C.c_uint32,C.c_uint64,C.POINTER(Hook),C.c_uint32,C.POINTER(jit_api.Stop),C.c_double]
        lib.jd_run.restype = C.c_uint32
        self.state = {i:0 for i in self.IDS}
        self.state[1000] = 0x10
        def read(engine,ids,pointers,count):
            for i in range(count):
                C.c_uint64.from_address(pointers[i]).value = self.state[ids[i]]
            return 0
        def write(engine,ids,pointers,count):
            for i in range(count):
                value = C.c_uint64.from_address(pointers[i]).value
                self.state[ids[i]] = value if 1017 <= ids[i] < 1049 else value & 0xffffffff
            return 0
        def fetch(engine,address,output,size):
            if not CODE <= address < CODE+4096-size: return 21
            C.memmove(output,C.addressof(self.code)+address-CODE,size)
            return 0
        self.callbacks = BATCH(read),BATCH(write),FETCH(fetch)
        self.d = (C.c_uint64*512)()
        ids = (C.c_int*50)(*self.IDS)
        lib.jd_init(self.d,0x1000,self.cpu,*[C.cast(f,p) for f in self.callbacks[:2]],ids,C.cast(self.callbacks[2],p))

    def jd_run(self,begin,hooks=(),remaining=-1.0,budget=1<<50):
        rows = (Hook*max(1,len(hooks)))(*hooks)
        stop = jit_api.Stop()
        return self.dlib.jd_run(self.d,begin,TRAP,budget,rows,len(hooks),C.byref(stop),remaining)

    def test_exports(self):
        for name in self.EXPORTS:
            self.assertTrue(hasattr(self.dlib,name),name)

    def test_dispatch_loop_syncs_registers_to_engine(self):
        self.load_code(SUM_LOOP)
        self.state.update({1000+14:DATA+4096,1000+15:TRAP,1016:CODE,1017+5:0x3ff0000000000005,1049:0x03000000})
        self.assertEqual(self.jd_run(CODE),jit_api.STOP_TRAP)
        self.assertEqual((self.state[1001],self.state[1016]),(55,TRAP))
        self.assertEqual(self.state[1017+5],0x3ff0000000000005)
        self.assertEqual(self.state[1049],0x03000000)

    def test_observer_hook_requests_single_instruction_fallback(self):
        self.load_code(SUM_LOOP)
        self.lib.dp_add_trap(self.cpu,CODE+8,4)
        seen = []
        hook = CODE_HOOK(lambda engine,address,size,data: seen.append((address,size)))
        self.state.update({1015:TRAP,1016:CODE})
        reason = self.jd_run(CODE,[Hook(CODE+8,CODE+8,C.cast(hook,C.c_void_p),None,0)])
        self.assertEqual(reason,128)
        self.assertEqual(seen,[(CODE+8,4)])
        self.assertEqual((self.state[1001],self.state[1002],self.state[1016]),(0,10,CODE+8))

    def test_deadline_is_in_seconds(self):
        self.load_code(SPIN)
        self.state[1016] = CODE
        started = time.monotonic()
        self.assertEqual(self.jd_run(CODE,remaining=0.05),jit_api.STOP_HALT)
        elapsed = time.monotonic()-started
        self.assertGreaterEqual(elapsed,.03)
        self.assertLess(elapsed,.5)


@unittest.skipUnless(sys.platform.startswith('linux') and BRIDGE.exists(),'build desktop/linux first')
class LinuxBridgeExportTests(unittest.TestCase):
    def test_bridge_exports_and_layout_check(self):
        lib = C.CDLL(str(BRIDGE))
        for name in ('nemo_check_layout','nemo_create','nemo_string_heap'):
            self.assertTrue(hasattr(lib,name),name)


if __name__ == '__main__':
    unittest.main()
