"""Runtime-only C ABI for the desktop ARM JIT; no assembler or build dependencies."""
import ctypes as C
from pathlib import Path
import sys

STOP_TRAP, STOP_HALT, STOP_UNSUPPORTED = 1, 4, 64


def library_path(name):
    if sys.platform.startswith('linux'):
        name = {'jit_core.dll':'libjit_core.so',
                'jit_dispatch_v7.dll':'libjit_dispatch_v7.so'}.get(name,name)
    root = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent/'work/jit-runtime'))
    return root/'native'/name if getattr(sys,'frozen',False) else root/name


class Stop(C.Structure):
    _fields_ = [('reasons',C.c_uint32),('pc',C.c_uint32),('fault_addr',C.c_uint32),
                ('fault_size',C.c_uint32),('fault_write',C.c_uint32),('detail',C.c_uint32),('ticks',C.c_uint64)]


def load(path=None):
    library = C.CDLL(str(path or library_path('jit_core.dll')))
    p,u32,u64,arr = C.c_void_p,C.c_uint32,C.c_uint64,C.POINTER(C.c_uint32)
    signatures = {
        'dp_create':(p,[u32]),'dp_destroy':(None,[p]),
        'dp_map':(C.c_int,[p,u32,u32,p]),'dp_unmap':(C.c_int,[p,u32,u32]),
        'dp_add_trap':(C.c_int,[p,u32,u32]),'dp_invalidate':(None,[p,u32,u32]),
        'dp_get_regs':(None,[p,arr]),'dp_set_regs':(None,[p,arr]),
        'dp_get_reg':(u32,[p,u32]),'dp_set_reg':(None,[p,u32,u32]),
        'dp_get_ext_regs':(None,[p,arr]),'dp_set_ext_regs':(None,[p,arr]),
        'dp_get_cpsr':(u32,[p]),'dp_set_cpsr':(None,[p,u32]),
        'dp_get_fpscr':(u32,[p]),'dp_set_fpscr':(None,[p,u32]),
        'dp_run':(u32,[p,u32,u64,C.POINTER(Stop)]),'dp_halt':(None,[p])}
    for name,(result,args) in signatures.items():
        function = getattr(library,name)
        function.restype,function.argtypes = result,args
    return library
