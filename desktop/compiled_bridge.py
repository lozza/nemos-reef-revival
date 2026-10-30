"""Optional compiled import hook, with an unchanged Python fallback."""
import ctypes as C
import os
from pathlib import Path
import sys
import time

from graphics_events import GraphicsEvents
from smoke_original import IMPORTS, arm, uc
from unicorn.unicorn_py3.unicorn import uclib, HOOK_CODE_CFUNC


class Api(C.Structure):
    _fields_ = [(name,C.c_void_p) for name in
                ('reg_read','reg_write','mem_read','mem_write','emu_stop')] + [('regs',C.c_int*6)]


class Entry(C.Structure):
    _fields_ = [('operation',C.c_uint32),('flags',C.c_uint32),
                ('function',C.c_void_p),('count',C.c_uint64),('successes',C.c_uint64)]


class Event(C.Structure):
    _fields_ = [('slot',C.c_uint32),('argc',C.c_uint32),('args',C.c_uint32*4)]


GL_OPERATIONS = {
    'glActiveTexture':101, 'glBindTexture':102, 'glBlendFunc':102,
    'glClearColor':107, 'glColor4f':107, 'glColorMask':108,
    'glCullFace':101, 'glDepthMask':109, 'glEnable':113, 'glDisable':113,
    'glFinish':112, 'glFogf':105, 'glFrontFace':101, 'glLineWidth':106,
    'glAlphaFunc':105, 'glStencilFunc':110, 'glStencilOp':103,
    'glTexEnvf':111, 'glTexEnvi':104, 'glTexParameteri':104,
    'glMatrixMode':114, 'glLoadMatrixf':115,
}
GL_TYPES = {101:(C.c_uint,),102:(C.c_uint,C.c_uint),
            103:(C.c_uint,C.c_uint,C.c_uint),104:(C.c_uint,C.c_uint,C.c_int),
            105:(C.c_uint,C.c_float),106:(C.c_float,),107:(C.c_float,)*4,
            108:(C.c_ubyte,)*4,109:(C.c_ubyte,),110:(C.c_uint,C.c_int,C.c_uint),
            111:(C.c_uint,C.c_uint,C.c_float),112:(),113:(C.c_uint,),
            114:(C.c_uint,),115:(C.c_void_p,)}
MEMORY_OPERATIONS = {'memcpy':1,'memmove':2,'memset':3,'memcmp':4}
# Native string reads (native_memory.c); only with DLLs exporting nemo_string_heap.
STRING_OPERATIONS = {'strlen':5,'strcmp':6,'strncmp':7,'strcasecmp':8,'strncasecmp':9}
MATH_OPERATIONS = {name:201+n for n,name in enumerate((
    'acos','acosf','asinf','ceilf','cos','cosf','floor','floorf','log','roundf',
    'sin','sinf','sqrt','sqrtf','tanf','truncf','fmax','fmaxf','fmin','fmodf','pow'))}
ERROR_CALLBACK = C.CFUNCTYPE(None,C.c_int)
# Native client-array setup; shadow state is queued and applied by sync_client_state.
CLIENT_OPERATIONS = {
    'glClientActiveTexture':301, 'glEnableClientState':302, 'glDisableClientState':303,
    'glVertexPointer':304, 'glNormalPointer':305, 'glColorPointer':306,
    'glTexCoordPointer':307, 'glMatrixIndexPointerOES':308, 'glWeightPointerOES':309,
    'glPointSizePointerOES':310, 'glBindBuffer':311,
}
CLIENT_GL_TYPES = {301:(C.c_uint,),302:(C.c_uint,),303:(C.c_uint,),311:(C.c_uint,C.c_uint)}
CLIENT_POINTER_TOKENS = {304:0x8074,305:0x8075,306:0x8076,307:0x8078,308:0x8844,309:0x86ad,310:0x8b9c}
CLIENT_QUEUE = 128
# Optional native all-VBO draws (native_draw.c); off by default, Python path unchanged.
DRAW_OPERATIONS = {'glDrawArrays':312, 'glDrawElements':313}
# Python-handled imports that may change draw state: their fallback invalidates the C mirror.
DRAW_STATE_NAMES = frozenset(CLIENT_OPERATIONS) | {
    'glEnable', 'glDisable', 'glGenBuffers', 'glDeleteBuffers', 'glBufferData', 'glBufferSubData'}
DRAW_ATTRS = 38
DRAW_COUNTER_NAMES = ('native_draws','native_arrays','native_elements','fallback_state',
                      'fallback_unsupported','fallback_bounds','invalidations','publishes')
DRAW_FUNCTIONS = (('glBindBuffer',(C.c_uint,C.c_uint)), ('glClientActiveTexture',(C.c_uint,)),
                  ('glVertexPointer',(C.c_int,C.c_uint,C.c_int,C.c_void_p)),
                  ('glNormalPointer',(C.c_uint,C.c_int,C.c_void_p)),
                  ('glColorPointer',(C.c_int,C.c_uint,C.c_int,C.c_void_p)),
                  ('glTexCoordPointer',(C.c_int,C.c_uint,C.c_int,C.c_void_p)),
                  ('glDrawArrays',(C.c_uint,C.c_int,C.c_int)),
                  ('glDrawElements',(C.c_uint,C.c_int,C.c_uint,C.c_void_p)))


class DrawAttr(C.Structure):
    _fields_ = [(name,C.c_uint32) for name in ('enabled','present','count','kind','stride','pointer','buffer')]


class DrawState(C.Structure):
    _fields_ = [(name,C.c_uint32) for name in ('client_texture','palette','array_binding','element_binding')] + [
        ('attrs',DrawAttr*DRAW_ATTRS)]


class DrawFunctions(C.Structure):
    _fields_ = [(name,C.c_void_p) for name in ('bind_buffer','client_active_texture','vertex_pointer',
                'normal_pointer','color_pointer','tex_coord_pointer','draw_arrays','draw_elements')]


def draw_index(key):
    """Mirror slot for a GraphicsPlatform attribute key, or None if C cannot represent it."""
    token, unit = key
    if token == 0x8078:
        return 6 + unit - 0x84c0 if 0x84c0 <= unit <= 0x84df else None
    return {0x8074:0,0x8075:1,0x8076:2,0x8844:3,0x86ad:4,0x8b9c:5}.get(token) if unit == 0 else None


class ClientEvent(C.Structure):
    _fields_ = [('operation',C.c_uint32),('args',C.c_uint32*4)]


def library_path():
    name = 'nemo_bridge.dll' if sys.platform == 'win32' else 'libnemo_bridge.so'
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent)) / 'native' / name


def load_library(path=None):
    library = C.CDLL(str(path or library_path()))
    library.nemo_check_layout.argtypes = (C.c_uint,)*3
    library.nemo_check_layout.restype = C.c_int
    if not library.nemo_check_layout(C.sizeof(Api),C.sizeof(Entry),C.sizeof(Event)):
        raise RuntimeError('Compiled bridge ABI does not match this runtime')
    signatures = {
        'nemo_create':(C.c_void_p,(C.POINTER(Api),C.c_void_p,C.POINTER(Entry),C.c_uint32,
                                  C.c_uint64,C.c_void_p,C.c_void_p)),
        'nemo_destroy':(None,(C.c_void_p,)),
        'nemo_prepare':(None,(C.c_void_p,C.c_double,C.c_int,C.c_uint32)),
        'nemo_matrix_mode':(None,(C.c_void_p,C.c_uint32)),
        'nemo_errno_function':(None,(C.c_void_p,C.c_void_p)),
        'nemo_counter':(C.c_uint64,(C.c_void_p,C.c_uint)),
        'nemo_events':(None,(C.c_void_p,C.POINTER(Event))),
        'nemo_fallback_flag':(C.POINTER(C.c_int),(C.c_void_p,)),
        'nemo_record':(None,(C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.c_uint32)),
    }
    for name,(result,args) in signatures.items():
        function = getattr(library,name)
        function.restype, function.argtypes = result,args
    # Older DLLs lack the queue; they keep every existing opcode and never see 301+.
    library.nemo_client_state = False
    if hasattr(library,'nemo_client_drain'):
        library.nemo_client_layout.argtypes = (C.c_uint,C.c_uint)
        library.nemo_client_layout.restype = C.c_int
        if not library.nemo_client_layout(C.sizeof(ClientEvent),CLIENT_QUEUE):
            raise RuntimeError('Compiled client-state queue ABI does not match this runtime')
        for name,(result,args) in {
                'nemo_client_enable':(None,(C.c_void_p,C.c_int)),
                'nemo_client_pending':(C.POINTER(C.c_uint32),(C.c_void_p,)),
                'nemo_client_drain':(C.c_uint32,(C.c_void_p,C.POINTER(ClientEvent),C.c_uint32))}.items():
            function = getattr(library,name)
            function.restype, function.argtypes = result,args
        library.nemo_client_state = True
    # Older DLLs lack native draws; glDrawArrays/glDrawElements then keep operation 0.
    library.nemo_native_draw = False
    if library.nemo_client_state and hasattr(library,'nemo_draw_layout'):
        library.nemo_draw_layout.argtypes = (C.c_uint,)*3
        library.nemo_draw_layout.restype = C.c_int
        if not library.nemo_draw_layout(C.sizeof(DrawState),C.sizeof(DrawFunctions),len(DRAW_COUNTER_NAMES)):
            raise RuntimeError('Compiled native-draw ABI does not match this runtime')
        for name,(result,args) in {
                'nemo_draw_configure':(None,(C.c_void_p,C.POINTER(DrawFunctions))),
                'nemo_draw_enable':(None,(C.c_void_p,C.c_int)),
                'nemo_draw_valid':(C.POINTER(C.c_int),(C.c_void_p,)),
                'nemo_draw_counters':(C.POINTER(C.c_uint64),(C.c_void_p,)),
                'nemo_draw_publish':(None,(C.c_void_p,C.POINTER(DrawState))),
                'nemo_draw_buffer':(C.c_int,(C.c_void_p,C.c_uint32,C.c_uint32,C.c_uint64,C.c_void_p)),
                'nemo_draw_reset_buffers':(None,(C.c_void_p,))}.items():
            function = getattr(library,name)
            function.restype, function.argtypes = result,args
        library.nemo_native_draw = True
    # Older DLLs lack native strings; strlen/strcmp/... then keep operation 0.
    library.nemo_native_strings = hasattr(library,'nemo_string_heap')
    if library.nemo_native_strings:
        library.nemo_string_heap.argtypes = (C.c_void_p,C.c_uint32,C.c_uint32,C.c_void_p)
        library.nemo_string_heap.restype = C.c_int
    return library


class StringHeapMirror:
    """C-readable GuestHeap.live counts, so native strings use bytestring's exact limit.

    counts[(p-base)>>4] is live[p][0]+1 for each live allocation start p, else 0.
    The heap instance's malloc/free/realloc are wrapped to keep it current; close()
    unregisters the table in C before removing the wrappers. Only allocation sizes
    are mirrored, never string contents.
    """
    NAMES = ('malloc','free','realloc')

    def __init__(self,library,runtime):
        import mmap
        heap = runtime.heap
        self.library, self.engine, self.heap = library, runtime.machine._uch, heap
        self.base, self.size = heap.base, heap.size
        if self.base & 15 or not 0 < self.size or self.base + self.size > 1 << 32:
            raise ValueError('guest heap shape unsupported by native strings')
        self.memory = mmap.mmap(-1,(self.size >> 4)*4)  # zero pages, committed on touch
        self.counts = memoryview(self.memory).cast('I')
        self.wrappers = {}
        base, size, counts = self.base, self.size, self.counts

        def sync(pointer):
            offset = pointer - base
            if 0 <= offset < size and not offset & 15:
                allocation = heap.live.get(pointer)
                counts[offset >> 4] = allocation[0] + 1 if allocation else 0
        malloc, free, realloc = (getattr(heap,name) for name in self.NAMES)

        def malloc_mirror(count):
            pointer = malloc(count)
            if pointer:
                sync(pointer)
            return pointer

        def free_mirror(pointer):
            try:
                free(pointer)
            finally:
                if pointer:
                    sync(pointer)

        def realloc_mirror(pointer,count):
            try:
                result = realloc(pointer,count)
            finally:
                if pointer:
                    sync(pointer)
            if result:
                sync(result)
            return result
        self.wrappers = dict(zip(self.NAMES,(malloc_mirror,free_mirror,realloc_mirror)))
        for name,function in self.wrappers.items():
            setattr(heap,name,function)
        for pointer in list(heap.live):
            sync(pointer)
        self.registered = bool(library.nemo_string_heap(self.engine,base,size,self.address()))

    def address(self):
        return C.addressof(C.c_char.from_buffer(self.memory))

    def close(self):
        self.library.nemo_string_heap(self.engine,0,0,None)  # C stops reading first
        self.registered = False
        ours = all(self.heap.__dict__.get(name) is function for name,function in self.wrappers.items())
        if ours:
            for name in self.wrappers:
                del self.heap.__dict__[name]
        # A wrapper someone else captured still writes here; keep the memory alive then.
        if ours:
            self.counts.release()
            try:
                self.memory.close()
            except BufferError:
                pass


def flush_translations(machine):
    flush = getattr(machine,'ctl_flush_tb',None)
    if flush is not None:
        flush()


class NativeEvents:
    maxlen = 4096

    def __init__(self,bridge,previous):
        self.bridge, self.previous = bridge,previous

    def __len__(self):
        return min(self.maxlen,len(self.previous)+self.bridge.gl_total)

    def __iter__(self):
        bridge = self.bridge
        count = bridge.gl_total
        if count < self.maxlen:
            yield from list(self.previous)[-(self.maxlen-count):]
        records = (Event*self.maxlen)()
        bridge.library.nemo_events(bridge.context,records)
        for number in range(max(0,count-self.maxlen),count):
            event = records[number%self.maxlen]
            yield {'name':bridge.names[event.slot], 'arguments':[hex(v) for v in event.args[:event.argc]]}


class NativeBridge:
    def __init__(self,runtime,library=None):
        self.runtime = runtime
        self.library = library if isinstance(library,C.CDLL) else load_library(library)
        self.context = None
        self.client_state_supported = getattr(self.library,'nemo_client_state',False)
        self.client_state_enabled = False  # Default off until full-game A/B.
        self.client_pending = None
        self.client_events = (ClientEvent*CLIENT_QUEUE)()
        self.draw_supported = bool(self.client_state_supported and getattr(self.library,'nemo_native_draw',False))
        self.draw_enabled = False  # Default off until game/GPU A/B.
        self.draw_valid = self.draw_counters = None
        self.strings_supported = bool(getattr(self.library,'nemo_native_strings',False)
                                      and os.environ.get('NEMO_NATIVE_STRINGS') != '0')
        self.string_mirror = None
        self.draw_state = DrawState()
        self.draw_functions = DrawFunctions()
        self.dirty_buffers = set()
        self.seen_draws = 0
        self.draw_totals = [0]*len(DRAW_COUNTER_NAMES)
        self.installed = False
        self.native_handle = None
        self.original_callback = None
        self.previous_boundary_stops = 0
        self.seen_counts = {}
        self.names = {int((address-IMPORTS)//4):name for address,name in runtime.import_names.items()}
        self.slots = {name:slot for slot,name in self.names.items()}
        self.entries = (Entry*(max(self.names,default=0)+1))()
        self.fast_slots = []
        self.retained_functions = []
        from guest_math import MathBindings
        if runtime.math is None:
            runtime.math = MathBindings()
        errno_name = '_errno' if sys.platform=='win32' else '__errno_location'
        self.errno_function = getattr(runtime.math.library,errno_name)
        for slot,name in self.names.items():
            entry = self.entries[slot]
            entry.flags = int(name.startswith('gl'))
            if name in MEMORY_OPERATIONS:
                entry.operation = MEMORY_OPERATIONS[name]
            elif name in STRING_OPERATIONS and self.strings_supported:
                entry.operation = STRING_OPERATIONS[name]
            elif name in MATH_OPERATIONS:
                entry.operation = MATH_OPERATIONS[name]
                function = getattr(runtime.math.library,name)
                self.retained_functions.append(function)
                entry.function = C.cast(function,C.c_void_p).value
            elif name in GL_OPERATIONS:
                entry.operation = GL_OPERATIONS[name]
                function = runtime.graphics.function(name,None,*GL_TYPES[entry.operation])
                self.retained_functions.append(function)
                entry.function = C.cast(function,C.c_void_p).value
            elif name in CLIENT_OPERATIONS and self.client_state_supported:
                entry.operation = CLIENT_OPERATIONS[name]
                if entry.operation in CLIENT_GL_TYPES:
                    function = runtime.graphics.function(name,None,*CLIENT_GL_TYPES[entry.operation])
                    self.retained_functions.append(function)
                    entry.function = C.cast(function,C.c_void_p).value
            elif name in DRAW_OPERATIONS and self.draw_supported:
                entry.operation = DRAW_OPERATIONS[name]
            if self.draw_supported and name in DRAW_STATE_NAMES:
                entry.flags |= 2
            if entry.operation:
                self.fast_slots.append(slot)
        if self.draw_supported:
            for field,(name,types) in zip(DrawFunctions._fields_,DRAW_FUNCTIONS):
                function = runtime.graphics.function(name,None,*types)
                self.retained_functions.append(function)
                setattr(self.draw_functions,field[0],C.cast(function,C.c_void_p).value)
            for name,operation in DRAW_OPERATIONS.items():
                if name in self.slots:
                    self.entries[self.slots[name]].function = getattr(self.draw_functions,
                        'draw_arrays' if operation == 312 else 'draw_elements')
        self.api = Api()
        for field in ('reg_read','reg_write','mem_read','mem_write','emu_stop'):
            setattr(self.api,field,C.cast(getattr(uclib,'uc_'+field),C.c_void_p).value)
        self.api.regs[:] = (arm.UC_ARM_REG_R0,arm.UC_ARM_REG_R1,arm.UC_ARM_REG_R2,
                            arm.UC_ARM_REG_R3,arm.UC_ARM_REG_LR,arm.UC_ARM_REG_PC)

        def error(code):
            runtime.machine._hook_exception = uc.UcError(code)
        self.error_callback = ERROR_CALLBACK(error)

    @property
    def gl_total(self):
        return self.library.nemo_counter(self.context,0) if self.context else 0

    def install(self):
        if self.installed:
            return
        runtime = self.runtime
        if getattr(runtime,'_native_bridge',None) is not None:
            raise RuntimeError('A compiled bridge is already installed on this engine')
        self.original_callback = runtime.machine._callbacks[runtime.import_hook]
        self.context = self.library.nemo_create(C.byref(self.api),runtime.machine._uch,self.entries,
                len(self.entries),IMPORTS,C.cast(self.original_callback,C.c_void_p),
                C.cast(self.error_callback,C.c_void_p))
        if not self.context:
            raise MemoryError('compiled bridge allocation failed')
        if self.strings_supported:
            # Unregistered engines make every string call an untouched Python fallback.
            try:
                self.string_mirror = StringHeapMirror(self.library,runtime)
            except ValueError as error:
                runtime.native_strings_error = str(error)
        self.fallback_flag = self.library.nemo_fallback_flag(self.context)
        self.library.nemo_matrix_mode(self.context,runtime.palette.matrix_mode)
        self.library.nemo_errno_function(self.context,C.cast(self.errno_function,C.c_void_p))
        callback = C.cast(self.library.nemo_hook,HOOK_CODE_CFUNC)
        handle = C.c_size_t()
        add = uclib['uc_hook_add']
        add.restype = C.c_int
        status = add(runtime.machine._uch,C.byref(handle),C.c_int(uc.UC_HOOK_CODE),
                     C.cast(callback,C.c_void_p),C.c_void_p(self.context),
                     C.c_uint64(IMPORTS),C.c_uint64(IMPORTS+0xfffff))
        if status:
            if self.string_mirror is not None:
                self.string_mirror.close()
                self.string_mirror = None
            self.library.nemo_destroy(self.context)
            self.context = None
            raise uc.UcError(status)
        runtime.machine._callbacks[handle.value] = callback
        self.native_handle = handle.value
        runtime.machine.hook_del(runtime.import_hook)
        runtime.import_hook = handle.value
        # Already-translated blocks keep their old hook list until retranslated.
        flush_translations(runtime.machine)
        self.previous_events = runtime.gl_events
        runtime.gl_events = NativeEvents(self,self.previous_events)
        runtime._native_bridge = self
        if self.client_state_supported:
            self.client_pending = self.library.nemo_client_pending(self.context)
            self.library.nemo_client_enable(self.context,int(self.client_state_enabled))
        if self.draw_supported:
            self.draw_valid = self.library.nemo_draw_valid(self.context)
            self.draw_counters = self.library.nemo_draw_counters(self.context)
            self.seen_draws = 0
            self.library.nemo_draw_configure(self.context,C.byref(self.draw_functions))
        self.installed = True
        if self.draw_enabled:
            self._start_draws()
        self.prepare_slice(0)

    def set_client_state_enabled(self,enabled):
        """Toggle native client-array setup; queued shadow state is applied first."""
        enabled = bool(enabled)
        if enabled and not self.client_state_supported:
            raise RuntimeError('Loaded compiled bridge has no client-state queue')
        self.sync_client_state()
        self.client_state_enabled = enabled
        if self.context and self.client_state_supported:
            self.library.nemo_client_enable(self.context,int(enabled))

    def set_native_draw_enabled(self,enabled):
        """Toggle native all-VBO draws; Python state is published to C before the first one."""
        enabled = bool(enabled)
        if enabled and not self.draw_supported:
            raise RuntimeError('Loaded compiled bridge has no native draw path')
        self.sync_client_state()
        self.draw_enabled = enabled
        if self.context and self.draw_supported:
            if enabled:
                self._start_draws()
            else:
                self.library.nemo_draw_enable(self.context,0)

    def _start_draws(self):
        self.library.nemo_draw_reset_buffers(self.context)
        self.dirty_buffers = set(self.runtime.buffers)
        self.library.nemo_draw_enable(self.context,1)
        self.publish_draw_state()

    def buffer_changed(self,buffer):
        """GraphicsPlatform calls this for every Python-side buffer create/upload/delete."""
        self.dirty_buffers.add(buffer)

    def publish_draw_state(self):
        """Copy the applied Python shadow into the C mirror; False keeps it invalid (draws fall back)."""
        if not (self.context and self.draw_enabled and self.draw_supported):
            return False
        self.sync_client_state()
        runtime = self.runtime
        library, context = self.library, self.context
        buffers = runtime.buffers
        for buffer in self.dirty_buffers:
            data = buffers.get(buffer)
            if data is None:
                library.nemo_draw_buffer(context,buffer,0,0,None)
            else:
                backing = (C.c_ubyte*len(data)).from_buffer(data) if data else None
                library.nemo_draw_buffer(context,buffer,1,len(data),backing)
                del backing  # releases the bytearray export
        self.dirty_buffers.clear()
        texture = runtime.client_texture
        if not runtime.direct_vertices or not 0x84c0 <= texture <= 0x84df:
            return False
        state = self.draw_state
        C.memset(C.byref(state),0,C.sizeof(state))
        state.client_texture = texture
        state.palette = int(bool(runtime.palette.enabled))
        state.array_binding = runtime.buffer_bindings[0x8892]
        state.element_binding = runtime.buffer_bindings[0x8893]
        for key in runtime.enabled_attributes:
            index = draw_index(key)
            if index is None:
                return False
            state.attrs[index].enabled = 1
        for key,(count,kind,stride,pointer,buffer) in runtime.attributes.items():
            index = draw_index(key)
            if index is not None:
                attr = state.attrs[index]
                attr.present, attr.count, attr.kind, attr.stride, attr.pointer, attr.buffer = (
                    1,count,kind,stride,pointer,buffer)
        library.nemo_draw_publish(context,C.byref(state))
        return True

    def sync_draws(self):
        """Native draws count as runtime.draws as soon as Python can observe anything."""
        counters = self.draw_counters
        if counters is not None and counters[0] != self.seen_draws:
            delta = counters[0]-self.seen_draws
            self.seen_draws = counters[0]
            runtime = self.runtime
            runtime.draws += delta
            runtime.native_vertex_draws = getattr(runtime,'native_vertex_draws',0)+delta

    def draw_summary(self):
        counters = self.draw_counters
        current = [counters[i] for i in range(len(DRAW_COUNTER_NAMES))] if counters is not None else [0]*len(DRAW_COUNTER_NAMES)
        return dict(zip(DRAW_COUNTER_NAMES,(a+b for a,b in zip(self.draw_totals,current))),
                    supported=self.draw_supported,enabled=self.draw_enabled)

    def sync_client_state(self):
        """Apply queued native client-array updates to the Python shadow, in order."""
        self.sync_draws()
        pending = self.client_pending
        if pending is None or not pending[0]:
            return 0
        runtime = self.runtime
        events = self.client_events
        bindings = runtime.buffer_bindings
        applied = 0
        while pending[0]:
            count = self.library.nemo_client_drain(self.context,events,CLIENT_QUEUE)
            for index in range(count):
                event = events[index]
                operation, args = event.operation, event.args
                if operation == 301:
                    runtime.client_texture = args[0]
                elif operation == 302:
                    runtime.enabled_attributes.add(runtime.attribute_key(args[0]))
                elif operation == 303:
                    runtime.enabled_attributes.discard(runtime.attribute_key(args[0]))
                elif operation == 311:
                    target, value = args[0], args[1]
                    bindings[target] = value
                    if value:
                        runtime.buffers.setdefault(value,bytearray())
                else:
                    shape = ((3,args[0],args[1],args[2]) if operation == 305 else
                             (1,args[0],args[1],args[2]) if operation == 310 else
                             (args[0],args[1],args[2],args[3]))
                    key = runtime.attribute_key(CLIENT_POINTER_TOKENS[operation])
                    runtime.attributes[key] = (*shape,bindings[0x8892])
            applied += count
        return applied

    def prepare_slice(self,deadline):
        remaining = max(0,deadline-time.monotonic()) if deadline else -1
        self.library.nemo_prepare(self.context,remaining,int(not self.runtime.graphics.check_each_call),
                                  self.runtime.errno_address)

    @property
    def in_fallback(self):
        return bool(self.fallback_flag[0])

    def record(self,name,args):
        if name not in self.slots:
            slot = max(self.names,default=0)+1
            self.slots[name],self.names[slot] = slot,name
        values = (C.c_uint32*4)(*args)
        self.library.nemo_record(self.context,self.slots[name],values,len(args))
        if name=='glMatrixMode':
            self.library.nemo_matrix_mode(self.context,args[0])

    def sync_counts(self):
        if not self.context:
            return
        self.sync_client_state()
        runtime = self.runtime
        for slot in self.fast_slots:
            count = self.entries[slot].count
            previous = self.seen_counts.get(slot,0)
            if count != previous:
                runtime.import_counts[self.names[slot]] += count-previous
                if self.entries[slot].successes:
                    runtime.bindings_called.add(self.names[slot])
                self.seen_counts[slot] = count
        stops = self.library.nemo_counter(self.context,3)
        runtime.boundary_preemptions += stops-self.previous_boundary_stops
        self.previous_boundary_stops = stops
        self.sync_client_state()

    def summary(self):
        if not self.context:
            return dict(getattr(self,'last_summary',{}),active=False)
        self.sync_counts()
        return {'active':self.installed,'handled':self.library.nemo_counter(self.context,1),
                'native_strings_supported':self.strings_supported,
                'native_strings_registered':bool(self.string_mirror and self.string_mirror.registered),
                'client_state_supported':bool(self.client_state_supported),
                'client_state_enabled':bool(self.client_state_enabled),
                'native_draw':self.draw_summary(),
                'fallbacks':self.library.nemo_counter(self.context,2),
                'graphics_calls':self.gl_total,
                'boundary_stops':self.library.nemo_counter(self.context,3),
                'imports':{self.names[s]:self.entries[s].count for s in self.fast_slots if self.entries[s].count}}

    def close(self):
        if not self.installed:
            return
        runtime = self.runtime
        self.sync_client_state()
        self.sync_counts()
        self.last_summary = self.summary()
        total = self.gl_total
        records = list(runtime.gl_events)
        runtime.machine.hook_del(self.native_handle)
        runtime.import_hook = runtime.machine.hook_add(uc.UC_HOOK_CODE,runtime.import_call,
                                                       begin=IMPORTS,end=IMPORTS+0xfffff)
        # No cached block may keep calling the native hook after its context is freed.
        flush_translations(runtime.machine)
        self.client_pending = None
        self.draw_totals = [v for k,v in self.last_summary['native_draw'].items() if k in DRAW_COUNTER_NAMES]
        self.draw_valid = self.draw_counters = None
        events = GraphicsEvents(maxlen=4096)
        for event in records:
            events.record(event['name'],[int(v,16) for v in event['arguments']])
        runtime.gl_events = events
        runtime._gl_events_total += total
        runtime._native_bridge = None
        if self.string_mirror is not None:
            self.string_mirror.close()
            self.string_mirror = None
        self.library.nemo_destroy(self.context)
        self.context = None
        self.installed = False
        for slot in self.fast_slots:
            self.entries[slot].count = 0
            self.entries[slot].successes = 0
        self.seen_counts.clear()
        self.previous_boundary_stops = 0


def install_if_requested(runtime):
    setting = os.environ.get('NEMO_COMPILED_BRIDGE')
    if setting == '0':
        return None
    if setting != '1' and (sys.platform != 'win32' or not library_path().exists()):
        return None
    try:
        bridge = NativeBridge(runtime)
    except (OSError,AttributeError,RuntimeError) as error:
        if setting == '1':
            raise
        runtime.compiled_bridge_error = str(error)
        return None
    bridge.install()
    if bridge.client_state_supported and os.environ.get('NEMO_NATIVE_CLIENT_STATE') != '0':
        bridge.set_client_state_enabled(True)
    if bridge.draw_supported and os.environ.get('NEMO_NATIVE_DRAW') == '1':  # opt-in only
        bridge.set_native_draw_enabled(True)
    return bridge
