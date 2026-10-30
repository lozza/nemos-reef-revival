"""Host bindings used to run the original client's global initialization.

Memory, clocks and uncontended mutexes have real host implementations. Worker
creation and blocking synchronization are explicit boundaries until a guest
thread scheduler is implemented. Unknown imports never silently succeed.
"""
import bisect
import collections
import errno as host_errno
import guest_errno as errno
import struct
import threading
import time
import locale
import platform
import os
from jni_values import read_va_list
from local_services import LocalAnalytics, LocalAdvertising
from guest_math import MathBindings, SUPPORTED as MATH_IMPORTS

from smoke_original import BASE, arm, uc
from trace_startup import StartupProbe, TLS, ENV_CALLS

HEAP_BASE = 0x10000000
HEAP_SIZE = 0x04000000
ONCE_RETURN = 0x09000000
SORT_RETURN = 0x0b000000
CHARACTER_CLASSES = ("alnum", "alpha", "blank", "cntrl", "digit", "graph",
                     "lower", "print", "punct", "space", "upper", "xdigit")


def c_character_class(character, name):
    if not 0 <= character <= 127:
        return False
    alpha = 65 <= character <= 90 or 97 <= character <= 122
    digit = 48 <= character <= 57
    return {"alnum": alpha or digit, "alpha": alpha, "blank": character in (9, 32),
            "cntrl": character < 32 or character == 127, "digit": digit,
            "graph": 33 <= character <= 126, "lower": 97 <= character <= 122,
            "print": 32 <= character <= 126, "punct": 33 <= character <= 126 and not (alpha or digit),
            "space": character in (9, 10, 11, 12, 13, 32), "upper": 65 <= character <= 90,
            "xdigit": digit or 65 <= character <= 70 or 97 <= character <= 102}[name]


class UnsupportedImport(RuntimeError):
    def __init__(self, name, arguments, caller):
        self.name, self.arguments, self.caller = name, arguments, caller
        super().__init__(f"unsupported platform call {name}, caller={caller:#x}")


class GuestHeap:
    def __init__(self, machine, base=HEAP_BASE, size=HEAP_SIZE, shared=None):
        self.machine, self.base, self.size = machine, base, size
        self.backing = None
        self.host_base = None
        setting = os.environ.get('NEMO_SHARED_HEAP')
        shared = (os.name == 'nt' if setting is None else setting == '1') if shared is None else shared
        if shared:
            self._map_shared()
        else:
            machine.mem_map(base, size)
        self.free_blocks = [(base, size)]
        self.live = {}

    def _map_shared(self, snapshot=None):
        import ctypes
        import mmap
        backing = mmap.mmap(-1, self.size)
        try:
            if snapshot is not None:
                backing[:] = snapshot
            pointer = ctypes.addressof(ctypes.c_char.from_buffer(backing))
            self.machine.mem_map_ptr(self.base, self.size, uc.UC_PROT_ALL, pointer)
        except BaseException:
            backing.close()
            raise
        # Unicorn owns only the pointer, so keep its backing alive even if a
        # caller retains the machine after releasing the allocator object.
        owners = getattr(self.machine, '_nemo_shared_heaps', None)
        if owners is None:
            self.machine._nemo_shared_heaps = owners = []
        owners.append(backing)
        self.backing, self.host_base = backing, pointer

    def enable_shared(self):
        """Developer A/B helper: retain allocated bytes and CPU/allocator state."""
        if self.backing is not None:
            return
        snapshot = bytes(self.machine.mem_read(self.base, self.size))
        self.machine.mem_unmap(self.base, self.size)
        try:
            self._map_shared(snapshot)
        except BaseException:
            self.machine.mem_map(self.base, self.size)
            self.machine.mem_write(self.base, snapshot)
            raise
        self.machine.ctl_flush_tb()

    def host_pointer(self, address, length):
        """A readable contiguous pointer only inside this lifetime-owned mapping.

        No guest unmap/protect operation is supported for the heap. Code that
        changes its mapping must invalidate shared access before doing so.
        """
        if self.backing is None or not isinstance(address, int) or not isinstance(length, int):
            return None
        offset = address-self.base
        if length < 0 or offset < 0 or offset > self.size or length > self.size-offset:
            return None
        return self.host_base+offset

    def malloc(self, count):
        if not 0 <= count <= 0xffffffff:
            return 0
        span = (max(count, 1) + 15) & ~15
        for index, (pointer, available) in enumerate(self.free_blocks):
            if available < span:
                continue
            if available == span:
                del self.free_blocks[index]
            else:
                self.free_blocks[index] = (pointer + span, available - span)
            self.live[pointer] = (count, span)
            return pointer
        return 0

    def free(self, pointer):
        if not pointer:
            return
        if pointer not in self.live:
            raise RuntimeError(f"invalid or double guest free at {pointer:#x}")
        _, span = self.live.pop(pointer)
        # free_blocks stays sorted and coalesced, so only the two neighbours of
        # the released span can merge; this matches a full sort-and-rebuild.
        blocks = self.free_blocks
        index = bisect.bisect_left(blocks, (pointer, 0))
        if index and blocks[index - 1][0] + blocks[index - 1][1] == pointer:
            index -= 1
            pointer, span = blocks[index][0], blocks[index][1] + span
            del blocks[index]
        if index < len(blocks) and pointer + span == blocks[index][0]:
            span += blocks[index][1]
            del blocks[index]
        blocks.insert(index, (pointer, span))

    def realloc(self, pointer, count):
        if not pointer:
            return self.malloc(count)
        if pointer not in self.live:
            raise RuntimeError(f"invalid guest realloc at {pointer:#x}")
        old_count, span = self.live[pointer]
        if not count:
            self.free(pointer)
            return 0
        if count <= span:
            self.live[pointer] = (count, span)
            return pointer
        replacement = self.malloc(count)
        if replacement:
            self.machine.mem_write(replacement, bytes(self.machine.mem_read(pointer, min(old_count, count))))
            self.free(pointer)
        return replacement


def import_register_io(machine):
    """ArmRegisterIO with cheaper calls for the per-import register exchange.

    Same Unicorn entry points, registers and order; registers are read fresh on
    every call. Only the ctypes call form changes: separate function pointers
    without argtypes take ready ctypes objects instead of converting each call.
    """
    import ctypes as C
    from arm_register_io import ArmRegisterIO
    from unicorn.unicorn_py3.unicorn import uclib

    def native(name):
        function = uclib[name]  # a fresh pointer; uclib's own stays as declared
        function.restype = C.c_int
        return function

    class ImportRegisterIO(ArmRegisterIO):
        def __init__(self, machine):
            super().__init__(machine)
            self.read_batch, self.read, self.write = (
                native("uc_reg_read_batch"), native("uc_reg_read"), native("uc_reg_write"))
            self.handle = machine._uch
            self.counts = [C.c_int(n) for n in range(5)]
            self.lr, self.r0, self.pc = (C.c_int(r) for r in (arm.UC_ARM_REG_LR, arm.UC_ARM_REG_R0,
                                                               arm.UC_ARM_REG_PC))
            self.r0_value = C.c_uint32()
            self.r0_pointer = C.byref(self.r0_value)

        def args(self, count=4):
            if not 0 < count <= 4:
                return super().args(count)
            status = self.read_batch(self.handle, self.ids, self.pointers, self.counts[count])
            if status:
                raise uc.UcError(status)
            return self.values[:count]

        def return_value(self, value):
            # PC is written from the buffer LR was just read into. The scalar PC
            # write keeps Unicorn's leave-the-block behaviour after the hook.
            status = self.read(self.handle, self.lr, self.link_pointer)
            if status:
                raise uc.UcError(status)
            self.r0_value.value = value & 0xffffffff
            status = self.write(self.handle, self.r0, self.r0_pointer)
            if status:
                raise uc.UcError(status)
            status = self.write(self.handle, self.pc, self.link_pointer)
            if status:
                raise uc.UcError(status)

    return ImportRegisterIO(machine)


class NativePlatform(StartupProbe):
    def __init__(self, elf, files=None):
        super().__init__(elf)
        self.register_io = import_register_io(self.machine)
        from crash_diagnostics import install as install_crash_diagnostics
        install_crash_diagnostics(self)
        self.heap = GuestHeap(self.machine)
        from guest_ctype import bind_tables
        bind_tables(self)
        self.import_counts = collections.Counter()
        self.destructors = []
        self.mutexes = {}
        self.conditions = {}
        self.logs = []
        self.errno_address = TLS + 0x100
        self.once_done = set()
        self.once_frames = []
        self.thread_keys = {}
        self.next_thread_key = 0
        self.c_locale = self.allocate_bytes(b"C\0")
        self.files = files
        self.file_pointers = {}
        self.string_leases = {}
        self.analytics = LocalAnalytics()
        self.advertising = LocalAdvertising()
        self.math = None
        self.platform_logs = []
        host_locale = (locale.getlocale()[0] or "en").replace("-", "_").split("_")
        self.desktop_locale = {"language": host_locale[0].lower(),
                               "country": host_locale[1].upper() if len(host_locale) > 1 else ""}
        self.jump_threads = {}
        self.fields = {}
        self.random48 = 0x1234abcd330e
        self.pending_notifications = {}
        self.screen_locked = False  # Desktop probes have no Android keyguard.
        self.restricted_profile = False  # Explicit desktop mode; no Android UserManager.
        self.display_dpi = 160.0  # Logical framebuffer density; no Android scaling.
        self.network = None
        self.error_strings = {}
        self.guest_environment = {}
        self.text_input = None
        self.message_box = None
        self.message_box_delivered = None
        self.calendar_time_pointer = 0
        self.zone_strings = {}
        self.sort_frames = {}
        self.machine.mem_map(SORT_RETURN, 0x1000)
        self.machine.mem_write(SORT_RETURN, bytes.fromhex('feffffea'))
        self.machine.hook_add(uc.UC_HOOK_CODE, self.sort_return, begin=SORT_RETURN, end=SORT_RETURN)
        self.desktop_build = {"MODEL": f"{platform.system()} {platform.machine()}",
                              "MANUFACTURER": "Desktop", "BRAND": "Desktop",
                              "DEVICE": platform.machine(), "PRODUCT": "Nemo desktop",
                              "HARDWARE": platform.machine()}
        self.exception_events = []
        self.milestones = {}
        preinit = BASE + (elf.symbol("_Z18applicationPreInitv")["address"] & ~1)
        self.machine.hook_add(uc.UC_HOOK_CODE, self.observe_preinit, begin=preinit, end=preinit)
        throw = BASE + (elf.symbol("__cxa_throw")["address"] & ~1)
        self.machine.hook_add(uc.UC_HOOK_CODE, self.record_exception, begin=throw, end=throw)
        for name, register in (("_ZN3sdr5Debug3logERKNS_6StringE", arm.UC_ARM_REG_R0),
                               ("_ZN3sdr5Debug3logEiRKNS_6StringENS0_8SeverityE", arm.UC_ARM_REG_R1),
                               ("_ZN3sdr5Debug15exceptionThrownERKNS_6StringE", arm.UC_ARM_REG_R0)):
            location = BASE + (elf.symbol(name)["address"] & ~1)
            self.machine.hook_add(uc.UC_HOOK_CODE, self.record_debug, user_data=register, begin=location, end=location)
        self.machine.mem_map(ONCE_RETURN, 0x1000)
        self.machine.mem_write(ONCE_RETURN, bytes.fromhex('feffffea'))
        self.machine.hook_add(uc.UC_HOOK_CODE, self.once_return, begin=ONCE_RETURN, end=ONCE_RETURN)

    def record_exception(self, machine, address, size, context):
        exception, type_info, destructor, _ = self.imported_args()
        name = self.bytestring(self.word(type_info + 4)).decode("ascii", "replace")
        self.exception_events.append({"type": name, "object": hex(exception),
                                      "destructor": hex(destructor), "caller": hex(machine.reg_read(arm.UC_ARM_REG_LR))})

    def observe_preinit(self, machine, address, size, context):
        label = self.milestones["applicationPreInit"] = {"entered": True, "returned": False}
        caller = machine.reg_read(arm.UC_ARM_REG_LR) & ~1
        token = []
        def returned(machine, address, size, context):
            label["returned"] = True
            machine.hook_del(token[0])
        token.append(machine.hook_add(uc.UC_HOOK_CODE, returned, begin=caller, end=caller))

    def record_debug(self, machine, address, size, register):
        string = machine.reg_read(register)
        buffer, count = self.word(string + 4), self.word(string + 8)
        if count > HEAP_SIZE // 2:
            raise RuntimeError("unexpected engine debug-string size")
        captured = min(count, 65536)
        value = bytes(machine.mem_read(buffer, captured * 2)).decode("utf-16-le", "replace") if captured else ""
        if captured < count:
            value += f" [truncated original debug message: {count} UTF-16 units]"
        self.logs.append(value)

    def once_return(self, machine, address, size, context):
        pointer, caller = self.once_frames.pop()
        self.once_done.add(pointer)
        self.word(pointer, 2)
        machine.reg_write(arm.UC_ARM_REG_LR, caller)
        self.return_value(0)

    def java_string(self, value):
        return self.reference("java/lang/String", kind="string", scope="local", value=value)

    def sort_next(self, frame, result=None):
        try:
            left, right = next(frame["iterator"]) if result is None else frame["iterator"].send(result)
        except StopIteration as completed:
            self.machine.mem_write(frame["base"], completed.value)
            self.sort_frames[getattr(self, "current_thread", 0)].pop()
            self.machine.reg_write(arm.UC_ARM_REG_LR, frame["caller"])
            self.return_value(0)
            return
        self.machine.reg_write(arm.UC_ARM_REG_R0, left)
        self.machine.reg_write(arm.UC_ARM_REG_R1, right)
        self.machine.reg_write(arm.UC_ARM_REG_LR, SORT_RETURN)
        self.machine.reg_write(arm.UC_ARM_REG_PC, frame["comparator"])

    def sort_return(self, machine, address, size, context):
        result = struct.unpack("<i", struct.pack("<I", machine.reg_read(arm.UC_ARM_REG_R0)))[0]
        self.sort_next(self.sort_frames[getattr(self, "current_thread", 0)][-1], result)

    def jni_call(self, machine, address, size, context):
        index = (address - ENV_CALLS) // 4
        if index in (171, 172, 173, 174):
            env, receiver, third, fourth = self.imported_args()
            if env != self.env_pointer():
                raise RuntimeError('invalid JNI object-array environment')
            if index == 172:
                count, class_ref, initial = receiver, self.ref(third), fourth
                if count > 100_000 or class_ref['kind'] != 'class':
                    raise RuntimeError('invalid JNI object-array allocation')
                if initial and self.ref(initial)['class'] != class_ref['class']:
                    raise RuntimeError('incompatible JNI array initial value')
                value = dict(self.ref(initial)) if initial else None
                return self.return_value(self.reference('[L' + class_ref['class'] + ';',
                                         kind='object_array', scope='local',
                                         component=class_ref['class'], elements=[value] * count))
            array = self.ref(receiver)
            if array['kind'] != 'object_array':
                raise RuntimeError('unsupported JNI array kind')
            if index == 171:
                return self.return_value(len(array['elements']))
            if third >= len(array['elements']):
                raise RuntimeError('JNI object-array index out of bounds')
            if index == 173:
                value = array['elements'][third]
                if not value:
                    return self.return_value(0)
                reference = dict(value)
                reference.pop('scope', None)
                class_name = reference.pop('class')
                return self.return_value(self.reference(class_name, scope='local', **reference))
            if fourth and self.ref(fourth)['class'] != array['component']:
                raise RuntimeError('incompatible JNI array element')
            # Arrays retain values after deletion of the caller's local reference.
            value = dict(self.ref(fourth)) if fourth else None
            array['elements'][third] = value
            return self.return_value(0)
        if index == 29:  # NewObjectV; constructors are explicit contracts.
            env, receiver, method_id, arguments = self.imported_args()
            method = self.methods.get(method_id)
            from desktop_audio import CLASS, MediaPlayer
            if (env != self.env_pointer() or self.ref(receiver)['kind'] != 'class' or
                    self.ref(receiver)['class'] != CLASS or method !=
                    {'class': CLASS, 'method': '<init>', 'descriptor': '()V'}):
                raise RuntimeError(f'unimplemented Java constructor: {method}')
            self.events.append({'operation': 'NewObjectV', **method})
            return self.return_value(self.reference(CLASS, scope='local', media=MediaPlayer(self.files)))
        if index == 62:  # CallVoidMethodV
            env, receiver, method_id, arguments = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI void method invocation")
            method = self.methods[method_id]
            self.events.append({"operation": "CallVoidMethodV", **method})
            if (not method.get('static') and method['class'] == 'net/livingmobile/sdr/shop/GoogleIabV3'
                    and self.ref(receiver)['class'] == method['class']
                    and method['method'] == 'downloadAvailableInfo'
                    and method['descriptor'] == '([Ljava/lang/String;)V'):
                # Recovered Java returns immediately when billing is unsupported.
                array, = read_va_list(machine, arguments, method['descriptor'])
                if self.ref(array)['class'] != '[Ljava/lang/String;':
                    raise RuntimeError('invalid billing product array')
                self.platform_logs.append({'service': 'billing', 'available': False,
                                           'operation': 'downloadAvailableInfo'})
                return self.return_value(0)
            from desktop_audio import CLASS, invoke
            if method['class'] == CLASS:
                return self.return_value(invoke(self, receiver, method, read_va_list(machine, arguments, method['descriptor'])))
            if (not method.get('static') and method['class'] == 'net/livingmobile/sdr/appui/MessageBox'
                    and method['method'] == 'invalidateListenerPointer' and method['descriptor'] == '()V'):
                reference = self.ref(receiver)
                if reference['class'] != method['class'] or 'message_box' not in reference:
                    raise RuntimeError('invalid message-box receiver')
                reference['message_box'].invalidate_listener()
                return self.return_value(0)
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/appui/Root" and self.ref(receiver)["class"] == method["class"]:
                from message_box_contract import REMOVE_METHOD, REMOVE_SIGNATURE
                if method['method'] == REMOVE_METHOD and method['descriptor'] == REMOVE_SIGNATURE:
                    from desktop_message_boxes import remove_box
                    remove_box(self, read_va_list(machine, arguments, REMOVE_SIGNATURE)[0])
                    return self.return_value(0)
                from desktop_text import CREATE_DESCRIPTOR, create_entry
                if method["method"] == "createTextField" and method["descriptor"] == CREATE_DESCRIPTOR:
                    self.text_input = create_entry(self, read_va_list(machine, arguments, CREATE_DESCRIPTOR))
                    return self.return_value(0)
            raise RuntimeError(f"unimplemented Java void contract: {method}")
        if index == 56:  # CallFloatMethodV; ARM soft-float returns bits in r0.
            env, receiver, method_id, arguments = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI float method invocation")
            method = self.methods[method_id]
            self.events.append({"operation": "CallFloatMethodV", **method})
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["method"] == "getDPI" and method["descriptor"] == "()F":
                return self.return_value(struct.unpack("<I", struct.pack("<f", self.display_dpi))[0])
            raise RuntimeError(f"unimplemented Java float contract: {method}")
        if index == 38:  # CallBooleanMethodV
            env, receiver, method_id, arguments = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI boolean method invocation")
            method = self.methods[method_id]
            self.events.append({"operation": "CallBooleanMethodV", **method})
            from desktop_audio import CLASS, invoke
            if method['class'] == CLASS:
                return self.return_value(invoke(self, receiver, method, read_va_list(machine, arguments, method['descriptor'])))
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["method"] == "isConnectedToNetwork" and method["descriptor"] == "()Z":
                return self.return_value(int(self.network is not None and self.network.connected()))
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["method"] == "isRestrictedProfile" and method["descriptor"] == "()Z":
                return self.return_value(int(self.restricted_profile))
            if (not method.get('static') and method['class'] == 'net/livingmobile/sdr/app/SdrActivity'
                    and self.ref(receiver)['class'] == method['class']
                    and method['method'] == 'isMarketVersionSupported' and method['descriptor'] == '()Z'):
                # The recovered Java checks com.android.vending. Desktop has
                # no Google Play store, matching its package-not-found result.
                return self.return_value(0)
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/shop/GoogleIabV3" and self.ref(receiver)["class"] == method["class"] and method["method"] == "isBillingSupported" and method["descriptor"] == "()Z" and self.ref(receiver).get("backend") == "unavailable desktop store":
                return self.return_value(0)
            raise RuntimeError(f"unimplemented Java boolean contract: {method}")
        if index == 50:  # CallIntMethodV
            env, receiver, method_id, arguments = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI integer method invocation")
            method = self.methods[method_id]
            self.events.append({"operation": "CallIntMethodV", **method})
            if not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["method"] == "isLocked" and method["descriptor"] == "()I":
                return self.return_value(int(self.screen_locked))
            raise RuntimeError(f"unimplemented Java integer contract: {method}")
        if index == 144:  # GetStaticFieldID
            env, receiver, name, descriptor = self.imported_args()
            if env != self.env_pointer() or self.ref(receiver)["kind"] != "class":
                raise RuntimeError("invalid JNI static field lookup")
            field = {"class": self.ref(receiver)["class"], "field": self.string(name),
                     "descriptor": self.string(descriptor), "static": True}
            field_id = self.next_handle
            self.next_handle += 4
            self.fields[field_id] = field
            self.events.append({"operation": "GetStaticFieldID", **field})
            return self.return_value(field_id)
        if index in (145, 150):  # GetStaticObjectField / GetStaticIntField
            env, receiver, field_id, _ = self.imported_args()
            if env != self.env_pointer() or field_id not in self.fields:
                raise RuntimeError("invalid JNI static field access")
            field = self.fields[field_id]
            if self.ref(receiver)["class"] != field["class"]:
                raise RuntimeError("JNI static field/class mismatch")
            self.events.append({"operation": "GetStaticField", **field})
            if index == 145 and field["descriptor"] == "Ljava/lang/String;":
                if field["class"] == "android/os/Build" and field["field"] in self.desktop_build:
                    return self.return_value(self.java_string(self.desktop_build[field["field"]]))
                if field["class"] == "android/os/Build$VERSION" and field["field"] == "RELEASE":
                    return self.return_value(self.java_string(platform.release()))
            raise RuntimeError(f"unimplemented Java static field contract: {field}")
        if index in (115, 35):  # CallStaticObjectMethodV / CallObjectMethodV
            env, receiver, method_id, arguments = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI object method invocation")
            method = self.methods[method_id]
            self.events.append({"operation": "CallObjectMethodV", **method})
            from message_box_contract import CREATE_METHOD, CREATE_SIGNATURE
            if (index == 35 and not method.get('static')
                    and method['class'] == 'net/livingmobile/sdr/appui/Root'
                    and self.ref(receiver)['class'] == method['class']
                    and method['method'] == CREATE_METHOD and method['descriptor'] == CREATE_SIGNATURE):
                from desktop_message_boxes import create_box
                return self.return_value(create_box(self, read_va_list(machine, arguments, CREATE_SIGNATURE)))
            if (index == 35 and not method.get('static')
                    and method['class'] == 'net/livingmobile/sdr/app/SdrActivity'
                    and self.ref(receiver)['class'] == method['class']
                    and (method['method'], method['descriptor']) in
                    (('getAppVersion', '(Z)Ljava/lang/String;'), ('getAppVersion', '()Ljava/lang/String;'),
                     ('getAppBuildNumber', '()Ljava/lang/String;'))):
                from apk_metadata import version
                name, build = version(self.files.apk)
                include_build = True
                if method['descriptor'].startswith('(Z)'):
                    include_build = bool(read_va_list(machine, arguments, method['descriptor'])[0])
                value = build if method['method'] == 'getAppBuildNumber' else f'{name} ({build})' if include_build else name
                return self.return_value(self.java_string(value))
            if index == 35 and not method.get("static") and method["class"] == "net/livingmobile/sdr/appui/Root" and self.ref(receiver)["class"] == method["class"] and method["descriptor"] == "()Ljava/lang/String;" and method["method"] in ("getTextFieldValue", "removeTextField"):
                text = self.text_input.text if self.text_input is not None else ""
                if method["method"] == "removeTextField" and self.text_input is not None:
                    self.text_input.active = False
                return self.return_value(self.java_string(text))
            if index == 35 and not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["method"] == "getGoogleIab" and method["descriptor"] == "()Lnet/livingmobile/sdr/shop/GoogleIabV3;":
                return self.return_value(self.reference("net/livingmobile/sdr/shop/GoogleIabV3", scope="local", backend="unavailable desktop store"))
            if index == 35 and not method.get("static") and method["class"] == "net/livingmobile/sdr/app/SdrActivity" and self.ref(receiver)["class"] == method["class"] and method["descriptor"] == "()Ljava/lang/String;":
                if method['method'] == 'getDeviceIdentifier':
                    return self.return_value(self.java_string(self.desktop_build['MANUFACTURER'] + ' ' + self.desktop_build['MODEL']))
                if method["method"] == "getInstallationId":
                    from desktop_identity import installation_id
                    return self.return_value(self.java_string(installation_id(self.files)))
                if method["method"] == "getRandomUUID":
                    import uuid
                    return self.return_value(self.java_string(str(uuid.uuid4())))
            if method["class"] == "java/util/Locale":
                if index == 115 and method.get("static") and self.ref(receiver)["kind"] == "class" and method["method"] == "getDefault" and method["descriptor"] == "()Ljava/util/Locale;":
                    return self.return_value(self.reference("java/util/Locale", scope="local", **self.desktop_locale))
                if index == 35 and not method.get("static") and self.ref(receiver)["class"] == "java/util/Locale" and method["descriptor"] == "()Ljava/lang/String;" and method["method"] in ("getLanguage", "getCountry"):
                    field = "language" if method["method"] == "getLanguage" else "country"
                    return self.return_value(self.java_string(self.ref(receiver)[field]))
            raise RuntimeError(f"unimplemented Java object contract: {method}")
        if index == 163:  # NewString: native SDR strings already contain UTF-16.
            env, pointer, count, _ = self.imported_args()
            if env != self.env_pointer() or count > 0x100000:
                raise RuntimeError("invalid JNI UTF-16 string arguments")
            value = bytes(machine.mem_read(pointer, count * 2)).decode("utf-16-le", "surrogatepass") if count else ""
            self.events.append({"operation": "NewString"})
            return self.return_value(self.java_string(value))
        if index in (164, 165, 166):
            env, handle, pointer, _ = self.imported_args()
            if env != self.env_pointer() or self.ref(handle)["kind"] != "string":
                raise RuntimeError("invalid JNI UTF-16 string receiver")
            if index == 164:
                return self.return_value(len(self.ref(handle)["value"].encode("utf-16-le", "surrogatepass")) // 2)
            if index == 165:
                value = self.ref(handle)["value"].encode("utf-16-le", "surrogatepass") + b"\0\0"
                result = self.allocate_bytes(value)
                self.string_leases[result] = {"handle": handle, "encoding": "utf16"}
                if pointer:
                    machine.mem_write(pointer, b"\x01")
                self.events.append({"operation": "GetStringChars"})
                return self.return_value(result)
            if self.string_leases.get(pointer) != {"handle": handle, "encoding": "utf16"}:
                raise RuntimeError("mismatched JNI UTF-16 string release")
            del self.string_leases[pointer]
            self.heap.free(pointer)
            self.events.append({"operation": "ReleaseStringChars"})
            return self.return_value(0)
        if index == 167:  # NewStringUTF
            env, pointer, _, _ = self.imported_args()
            if env != self.env_pointer():
                raise RuntimeError("invalid JNI environment")
            encoded = self.bytestring(pointer).replace(b"\xc0\x80", b"\0")
            value = encoded.decode("utf-8", "surrogatepass")
            value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
            self.events.append({"operation": "NewStringUTF"})
            return self.return_value(self.java_string(value))
        if index in (118, 130, 142):  # Static boolean/int/void varargs calls.
            env, class_handle, method_id, va_list = self.imported_args()
            if env != self.env_pointer() or method_id not in self.methods:
                raise RuntimeError("invalid JNI static method invocation")
            method = self.methods[method_id]
            if not method.get("static") or self.ref(class_handle)["class"] != method["class"]:
                raise RuntimeError("JNI static method/class mismatch")
            descriptor = method["descriptor"]
            if not descriptor.endswith({118: ')Z', 130: ')I', 142: ')V'}[index]):
                raise RuntimeError("JNI static method return type mismatch")
            arguments = read_va_list(machine, va_list, descriptor)
            self.events.append({"operation": "CallStaticMethodV", **method})
            if method["class"] == LocalAdvertising.CLASS:
                return self.return_value(self.advertising.invoke(method["method"], descriptor, arguments))
            if method["class"] == LocalAdvertising.TAPJOY_CLASS:
                return self.return_value(self.advertising.invoke_offer(method["method"], descriptor, arguments))
            if method['class'] == LocalAdvertising.ADCOLONY_CLASS:
                return self.return_value(self.advertising.invoke_video(method['method'], descriptor, arguments))
            if method["class"] == "com/disney/nemo/NemoLocalNotification" and index == 142 and method["method"] == "cancelAll" and descriptor == "(Landroid/app/Activity;)V":
                if len(arguments) != 1 or self.ref(arguments[0])["class"] != "net/livingmobile/sdr/app/SdrActivity":
                    raise RuntimeError("invalid local-notification activity")
                self.pending_notifications.clear()
                self.events.append({"operation": "CancelAllLocalNotifications", "backend": "desktop local queue"})
                return self.return_value(0)
            if method["class"] == LocalAnalytics.COMSCORE_CLASS:
                return self.return_value(self.analytics.invoke_comscore(method["method"], descriptor, arguments))
            if method["class"] == LocalAnalytics.KOCHAVA_CLASS:
                return self.return_value(self.analytics.invoke_kochava(method["method"], descriptor, arguments))
            if method["class"] == LocalAnalytics.GOOGLE_CLASS:
                return self.return_value(self.analytics.invoke_google(method["method"], descriptor, arguments))
            if method["class"] != LocalAnalytics.CLASS:
                raise RuntimeError(f"unimplemented static Java contract: {method['class']}.{method['method']}{descriptor}")
            return self.return_value(int(self.analytics.invoke(method["method"], descriptor, arguments)))
        if index not in (169, 170):
            return super().jni_call(machine, address, size, context)
        env, handle, pointer, _ = self.imported_args()
        if env != self.env_pointer() or self.ref(handle)["kind"] != "string":
            raise RuntimeError("invalid JNI string receiver")
        if index == 169:  # GetStringUTFChars, JNI modified UTF-8 (UTF-16 code units)
            units = self.ref(handle)["value"].encode("utf-16-le", "surrogatepass")
            encoded = bytearray()
            for at in range(0, len(units), 2):
                unit = units[at] | (units[at + 1] << 8)
                if unit == 0:
                    encoded.extend(b"\xc0\x80")
                elif unit < 0x80:
                    encoded.append(unit)
                elif unit < 0x800:
                    encoded.extend((0xc0 | (unit >> 6), 0x80 | (unit & 63)))
                else:
                    encoded.extend((0xe0 | (unit >> 12), 0x80 | ((unit >> 6) & 63), 0x80 | (unit & 63)))
            encoded.append(0)
            result = self.allocate_bytes(bytes(encoded))
            self.string_leases[result] = {"handle": handle, "encoding": "modifiedutf8"}
            if pointer:
                machine.mem_write(pointer, b"\x01")
            self.events.append({"operation": "GetStringUTFChars", "value": self.ref(handle)["value"]})
            self.return_value(result)
        else:
            if self.string_leases.get(pointer) != {"handle": handle, "encoding": "modifiedutf8"}:
                raise RuntimeError("mismatched JNI string release")
            del self.string_leases[pointer]
            self.heap.free(pointer)
            self.events.append({"operation": "ReleaseStringUTFChars"})
            self.return_value(0)

    def bytestring(self, address, limit=None, terminated=True):
        if limit is None:
            allocation = self.heap.live.get(address)
            limit = allocation[0] if allocation else 0x100000
        if not limit:
            return b""
        if not address:
            raise RuntimeError("unexpected null C string")
        data = bytearray()
        while len(data) < limit:
            count = min(256, 4096 - ((address + len(data)) & 4095), limit - len(data))
            chunk = bytes(self.machine.mem_read(address + len(data), count))
            end = chunk.find(b"\0")
            if end >= 0:
                data.extend(chunk[:end])
                return bytes(data)
            data.extend(chunk)
        if not terminated:
            return bytes(data)
        raise RuntimeError("unterminated C string in guest memory")

    def allocate_bytes(self, value):
        pointer = self.heap.malloc(len(value))
        if not pointer:
            raise MemoryError("guest heap exhausted")
        self.machine.mem_write(pointer, value)
        return pointer

    def imported_args(self, count=4):
        if self.register_io is not None:
            return self.register_io.args(count)
        args = [self.machine.reg_read(r) for r in (arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
                                                  arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3)]
        stack = self.machine.reg_read(arm.UC_ARM_REG_SP)
        return args[:count] + [self.word(stack + i * 4) for i in range(max(0, count - 4))]

    def return_value(self, value=0):
        if self.register_io is not None:
            return self.register_io.return_value(value)
        return super().return_value(value)

    def get_mutex(self, pointer):
        if pointer not in self.mutexes:
            value = self.word(pointer)
            if value not in (0, 0x4000, 0x8000):
                raise RuntimeError(f"unknown static Bionic mutex value {value:#x}")
            self.mutexes[pointer] = {"kind": value >> 14, "depth": 0,
                                    "lock": threading.RLock() if value == 0x4000 else threading.Lock()}
        return self.mutexes[pointer]

    def bind_import(self, name, args):
        # Frequent memory/allocation imports skip the long name chain. No
        # earlier chain entry claims these names; both paths share helpers.
        common = COMMON_IMPORTS.get(name)
        if common is not None:
            return common(self, args)
        return self.bind_import_chain(name, args)

    def bind_import_chain(self, name, args):
        """Original ordered dispatch, kept callable for differential tests."""
        if name == "sscanf":
            from guest_scanf import sscanf
            return sscanf(self, args)
        if name == "getpid":
            return os.getpid()
        if name == "time":
            seconds = int(time.time())
            if not -0x80000000 <= seconds <= 0x7fffffff:
                self.word(self.errno_address, errno.EOVERFLOW)
                return -1
            if args[0]:
                self.word(args[0], seconds)
            return seconds
        if name == "getenv":
            # The desktop guest has an explicit environment. Host proxy settings
            # and credentials are not inherited into the original client.
            value = self.guest_environment.get(self.string(args[0]))
            return self.allocate_bytes(value.encode() + b"\0") if value is not None else 0
        if name == "strerror":
            code = args[0]
            if code not in self.error_strings:
                error_name = next((key for key, value in vars(errno).items() if key.startswith("E") and value == code), None)
                host_code = getattr(host_errno, error_name, None) if error_name else None
                message = os.strerror(host_code) if host_code is not None else "Success" if code == 0 else f"Unknown error {code}"
                self.error_strings[code] = self.allocate_bytes(message.encode("utf-8") + b"\0")
            return self.error_strings[code]
        if self.network is not None:
            from desktop_network import IMPORTS as SOCKET_IMPORTS
            if name in SOCKET_IMPORTS:
                return self.network.call(self, name, args)
        if name in ("opendir", "readdir", "closedir"):
            from guest_directories import call
            return call(self, name, args)
        if name == "strtod":
            from guest_floating import strtod
            return strtod(self, args)
        if name in ("strtol", "strtoul", "strtoll", "atoi"):
            from guest_numbers import convert
            return convert(self, name, args)
        if name == "srand48":
            self.random48 = ((args[0] & 0xffffffff) << 16) | 0x330e
            return 0
        if name == "lrand48":
            self.random48 = (0x5deece66d * self.random48 + 0xb) & ((1 << 48) - 1)
            return self.random48 >> 17
        if name in ("setjmp", "longjmp"):
            # Historical Bionic ARM jmp_buf: 64 words, D8-D15 at word 2,
            # FPSCR at 18, R4-R14 at 19. No guest signals are delivered.
            pointer = args[0]
            registers = [getattr(arm, f"UC_ARM_REG_R{i}") for i in range(4, 13)] + [arm.UC_ARM_REG_SP, arm.UC_ARM_REG_LR]
            floating = [getattr(arm, f"UC_ARM_REG_D{i}") for i in range(8, 16)]
            thread = getattr(self, "current_thread", 0)
            if name == "setjmp":
                data = bytearray(256)
                struct.pack_into("<II", data, 0, 0x4278f501, 0)
                struct.pack_into("<8Q", data, 8, *(self.machine.reg_read(r) for r in floating))
                struct.pack_into("<I", data, 72, self.machine.reg_read(arm.UC_ARM_REG_FPSCR))
                struct.pack_into("<11I", data, 76, *(self.machine.reg_read(r) for r in registers))
                self.machine.mem_write(pointer, bytes(data))
                self.jump_threads[pointer] = thread
                return 0
            data = bytes(self.machine.mem_read(pointer, 256))
            if struct.unpack_from("<II", data) != (0x4278f501, 0) or self.jump_threads.get(pointer) != thread:
                raise RuntimeError("invalid or cross-thread ARM longjmp target")
            core = struct.unpack_from("<11I", data, 76)
            if not core[-1] or not core[-2]:
                raise RuntimeError("invalid ARM longjmp stack/return address")
            for register, value in zip(floating, struct.unpack_from("<8Q", data, 8)):
                self.machine.reg_write(register, value)
            self.machine.reg_write(arm.UC_ARM_REG_FPSCR, struct.unpack_from("<I", data, 72)[0])
            for register, value in zip(registers, core):
                self.machine.reg_write(register, value)
            return args[1] or 1
        if name in ("__android_log_print", "__android_log_write"):
            from guest_printf import format_c
            cursor = 3
            def next_word():
                nonlocal cursor
                value = self.imported_args(cursor + 1)[cursor]
                cursor += 1
                return value
            def next_integer64():
                nonlocal cursor
                cursor = (cursor + 1) & ~1
                low, high = next_word(), next_word()
                return low | (high << 32)
            def next_double():
                return struct.unpack("<d", struct.pack("<Q", next_integer64()))[0]
            value = (format_c(self, self.bytestring(args[2]), next_word, next_double, next_integer64) if name.endswith("print")
                     else self.bytestring(args[2]))
            self.platform_logs.append({"priority": args[0], "tag": self.string(args[1]),
                                       "message": value.decode("utf-8", "replace")})
            return len(value)
        if name in MATH_IMPORTS:
            if self.math is None:
                self.math = MathBindings()
            return self.math.call(self, name, args)
        if name in ("snprintf", "vsnprintf", "sprintf", "vsprintf", "vasprintf", "asprintf"):
            from guest_printf import snprintf
            return snprintf(self, args, va_list=name.startswith("v"), bounded=name in ("snprintf", "vsnprintf"),
                            allocated=name in ('asprintf', 'vasprintf'))
        a, b, c, d = args[:4]
        if name in ("memcpy", "memmove", "memset", "memcmp", "memchr", "strncpy", "strncmp") and c > HEAP_SIZE:
            raise RuntimeError(f"{name} exceeds the guest memory budget")
        if name in ("stat", "mkdir", "remove", "unlink", "rename"):
            if self.files is None:
                raise UnsupportedImport(name + " (no filesystem mount)", args, self.machine.reg_read(arm.UC_ARM_REG_LR))
            try:
                path = self.string(a)
                if name == "stat":
                    self.machine.mem_write(b, self.files.stat_arm32(path))
                elif name == "mkdir":
                    self.files.mkdir(path, b & 0o777)
                elif name == "rename":
                    self.files.resolve(path, writable=True).replace(self.files.resolve(self.string(b), writable=True))
                else:
                    target = self.files.resolve(path, writable=True)
                    if name == "remove" and target.is_dir():
                        target.rmdir()
                    else:
                        target.unlink()
                return 0
            except OSError as error:
                self.word(self.errno_address, errno.from_host(error))
                return -1
        if name in {"fopen", "fclose", "fread", "fwrite", "fseek", "ftell", "ftello", "fseeko",
                    "feof", "ferror", "clearerr", "fflush", "fgetc", "getc", "ungetc"}:
            if self.files is None:
                raise UnsupportedImport(name + " (no filesystem mount)", args, self.machine.reg_read(arm.UC_ARM_REG_LR))
            return self.stdio(name, args)
        if name in COMMON_IMPORTS:
            return COMMON_IMPORTS[name](self, args)
        if name == "calloc":
            count = a * b
            result = self.heap.malloc(count)
            if result:
                self.machine.mem_write(result, b"\0" * count)
            else:
                self.word(self.errno_address, errno.ENOMEM)
            return result
        if name == "realloc":
            result = self.heap.realloc(a, b)
            if b and not result:
                self.word(self.errno_address, errno.ENOMEM)
            return result
        if name == "strstr":
            position = self.bytestring(a).find(self.bytestring(b))
            return a + position if position >= 0 else 0
        if name in ("wctob", "btowc"):
            return a if a <= 127 else -1
        if name == "setlocale":
            # The guest starts in the C locale. No host process locale changes.
            if not b or self.bytestring(b) in (b"C", b"POSIX"):
                return self.c_locale
            return 0
        if name == "wctype":
            value = self.bytestring(a).decode("ascii")
            return CHARACTER_CLASSES.index(value) + 1 if value in CHARACTER_CLASSES else 0
        if name == "iswctype":
            return int(c_character_class(a, CHARACTER_CLASSES[b - 1])) if 1 <= b <= len(CHARACTER_CLASSES) else 0
        if name == "towupper":
            return a - 32 if 97 <= a <= 122 else a
        if name == "towlower":
            return a + 32 if 65 <= a <= 90 else a
        if name == "strdup":
            return self.allocate_bytes(self.bytestring(a) + b"\0")
        if name == "strndup":
            if b > HEAP_SIZE:
                raise RuntimeError("strndup exceeds the guest memory budget")
            return self.allocate_bytes(self.bytestring(a, limit=b, terminated=False) + b"\0")
        if name in ("strcpy", "strncpy"):
            if name == "strncpy":
                value = self.bytestring(b, c, terminated=False).ljust(c, b"\0")
            else:
                value = self.bytestring(b) + b"\0"
            if value:
                self.machine.mem_write(a, value)
            return a
        if name in ("strcmp", "strncmp", "memcmp", "strcasecmp", "strncasecmp"):
            if name == "memcmp":
                left, right = bytes(self.machine.mem_read(a, c)), bytes(self.machine.mem_read(b, c))
            else:
                if name in ("strncmp", "strncasecmp"):
                    left, right = self.bytestring(a, c, terminated=False), self.bytestring(b, c, terminated=False)
                else:
                    left, right = self.bytestring(a), self.bytestring(b)
            if name in ("strcasecmp", "strncasecmp"):
                left, right = left.lower(), right.lower()
            return (left > right) - (left < right)
        if name in ("strchr", "strrchr", "memchr"):
            value = bytes(self.machine.mem_read(a, c)) if name == "memchr" else self.bytestring(a) + b"\0"
            position = value.rfind(bytes([b & 255])) if name == "strrchr" else value.find(bytes([b & 255]))
            return 0 if position < 0 else a + position
        if name == "__cxa_atexit":
            self.destructors.append({"function": a, "argument": b, "dso": c})
            return 0
        if name == "__gnu_Unwind_Find_exidx":
            for section in self.elf.sections:
                if section["name"] == ".ARM.exidx" and any(
                    segment["type"] == 1 and BASE + segment["address"] <= a < BASE + segment["address"] + segment["memory_size"]
                    for segment in self.elf.segments
                ):
                    self.word(b, section["size"] // 8)
                    return BASE + section["address"]
            self.word(b, 0)
            return 0
        if name == "pthread_key_create":
            key = self.next_thread_key
            self.next_thread_key += 1
            self.thread_keys[key] = {"destructor": b, "value": 0}
            self.word(a, key)
            return 0
        if name == "pthread_getspecific":
            return self.thread_keys.get(a, {}).get("value", 0)
        if name == "pthread_setspecific":
            if a not in self.thread_keys:
                return errno.EINVAL
            self.thread_keys[a]["value"] = b
            return 0
        if name == "pthread_key_delete":
            if a not in self.thread_keys:
                return errno.EINVAL
            del self.thread_keys[a]
            return 0
        if name == "__errno":
            return self.errno_address
        if name == "gettimeofday":
            now = time.time_ns()
            self.machine.mem_write(a, struct.pack("<ii", now // 1_000_000_000, (now // 1000) % 1_000_000))
            if b:
                self.machine.mem_write(b, b"\0" * 8)
            return 0
        if name == "clock_gettime":
            if a not in (0, 1):
                raise RuntimeError(f"unsupported guest clock id {a}")
            now = time.time_ns() if a == 0 else time.monotonic_ns()
            self.machine.mem_write(b, struct.pack("<ii", now // 1_000_000_000, now % 1_000_000_000))
            return 0
        if name == "mktime":
            values = struct.unpack("<9i", self.machine.mem_read(a, 36))
            second, minute, hour, day, month, year, _, _, dst = values
            try:
                value = int(time.mktime((year + 1900, month + 1, day, hour, minute, second, 0, 0, dst)))
                if not -0x80000000 <= value <= 0x7fffffff:
                    raise OverflowError("legacy time_t overflow")
                normalized = time.localtime(value)
                self.machine.mem_write(a, struct.pack("<9i", normalized.tm_sec, normalized.tm_min, normalized.tm_hour,
                                                     normalized.tm_mday, normalized.tm_mon - 1, normalized.tm_year - 1900,
                                                     (normalized.tm_wday + 1) % 7, normalized.tm_yday - 1, normalized.tm_isdst))
                return value
            except (OverflowError, ValueError):
                self.word(self.errno_address, errno.EOVERFLOW)
                return -1
        if name == "localtime":
            seconds = struct.unpack("<i", self.machine.mem_read(a, 4))[0]
            try:
                value = time.localtime(seconds)
            except (OverflowError, ValueError, OSError):
                self.word(self.errno_address, errno.EOVERFLOW)
                return 0
            zone = getattr(value, "tm_zone", time.tzname[int(value.tm_isdst > 0)])
            offset = getattr(value, "tm_gmtoff", -(time.altzone if value.tm_isdst > 0 else time.timezone))
            if zone not in self.zone_strings:
                self.zone_strings[zone] = self.allocate_bytes(zone.encode("utf-8") + b"\0")
            if not self.calendar_time_pointer:
                self.calendar_time_pointer = self.allocate_bytes(bytes(44))
            fields = (value.tm_sec, value.tm_min, value.tm_hour, value.tm_mday,
                      value.tm_mon - 1, value.tm_year - 1900, (value.tm_wday + 1) % 7,
                      value.tm_yday - 1, value.tm_isdst, offset)
            self.machine.mem_write(self.calendar_time_pointer, struct.pack("<10iI", *fields, self.zone_strings[zone]))
            return self.calendar_time_pointer
        if name == "pthread_mutex_init":
            kind = self.word(b) & 15 if b else 0
            if kind not in (0, 1, 2):
                raise RuntimeError(f"unsupported Bionic mutex kind {kind}")
            self.word(a, kind << 14)
            self.mutexes[a] = {"kind": kind, "depth": 0, "lock": threading.RLock() if kind == 1 else threading.Lock()}
            return 0
        if name == "pthread_mutex_lock":
            mutex = self.get_mutex(a)
            if not mutex["lock"].acquire(blocking=False):
                raise UnsupportedImport("pthread_mutex_lock (would block)", args, self.machine.reg_read(arm.UC_ARM_REG_LR))
            mutex["depth"] += 1
            return 0
        if name == "pthread_mutex_unlock":
            mutex = self.get_mutex(a)
            if not mutex["depth"]:
                return errno.EPERM
            mutex["lock"].release()
            mutex["depth"] -= 1
            return 0
        if name == "pthread_mutex_destroy":
            mutex = self.get_mutex(a)
            if mutex["depth"]:
                return errno.EBUSY
            del self.mutexes[a]
            self.word(a, 0xdead)
            return 0
        if name == "pthread_cond_init":
            if b and self.word(b):
                raise RuntimeError("non-default condition attributes need implementation")
            self.conditions[a] = threading.Condition()
            self.word(a, 0)
            return 0
        if name in ("pthread_cond_signal", "pthread_cond_destroy"):
            condition = self.conditions.get(a)
            if condition is None:
                if self.word(a) != 0:
                    raise RuntimeError("unknown static Bionic condition value")
                condition = self.conditions[a] = threading.Condition()
            if name == "pthread_cond_destroy":
                del self.conditions[a]
            else:
                with condition:
                    condition.notify(1)
            return 0
        raise UnsupportedImport(name, args, self.machine.reg_read(arm.UC_ARM_REG_LR))

    def stdio(self, name, args):
        a, b, c, d = args[:4]
        stream = None
        try:
            if name == "fopen":
                handle = self.files.open(self.string(a), self.string(b))
                # Legacy Bionic FILE is 84 bytes. libzip directly reads its
                # 16-bit flags at +12, so an opaque, uninitialized token is
                # insufficient even when fread/fseek are host-bound.
                pointer = self.heap.malloc(84)
                if not pointer:
                    self.files.close(handle)
                    raise OSError(host_errno.ENOMEM, "guest FILE allocation failed")
                self.file_pointers[pointer] = handle
                self.machine.mem_write(pointer, b"\0" * 84)
                self.machine.mem_write(pointer + 14, struct.pack("<H", handle))
                self.sync_file_flags(pointer, self.files.stream(handle))
                return pointer
            if name == "fflush" and not a:
                for item in self.files.streams.values():
                    item.file.flush()
                return 0
            file_pointer = d if name in ("fread", "fwrite") else b if name == "ungetc" else a
            if file_pointer not in self.file_pointers:
                raise OSError(host_errno.EBADF, "invalid guest FILE pointer")
            handle = self.file_pointers[file_pointer]
            stream = self.files.stream(handle)
            if not hasattr(stream, "pushback"):
                stream.pushback = bytearray()
            if name == "fclose":
                self.files.close(handle)
                del self.file_pointers[file_pointer]
                self.heap.free(file_pointer)
                return 0
            if name in ("fread", "fwrite"):
                count = b * c
                if count > HEAP_SIZE:
                    raise OSError(host_errno.EINVAL, "stdio request exceeds guest memory budget")
                if not count:
                    return 0
                if name == "fread":
                    prefix = bytes(reversed(stream.pushback[-count:]))
                    if prefix:
                        del stream.pushback[-len(prefix):]
                    value = prefix + stream.file.read(count - len(prefix))
                    if len(self.files.events) < 1000:
                        self.files.events.append({"operation": "read", "path": stream.path, "requested": count, "returned": len(value), "position_after": stream.file.tell()})
                    if value:
                        self.machine.mem_write(a, value)
                    stream.eof = len(value) < count
                    self.sync_file_flags(file_pointer, stream)
                    return len(value) // b
                if stream.pushback:
                    stream.file.seek(-len(stream.pushback), 1)
                    stream.pushback.clear()
                return stream.file.write(bytes(self.machine.mem_read(a, count))) // b
            if name in ("fseek", "fseeko"):
                # Legacy Bionic off_t is 32-bit. The *64 symbols are distinct
                # APIs and are not imported by this client.
                offset = struct.unpack("<i", struct.pack("<I", b))[0]
                whence = c
                if whence not in (0, 1, 2):
                    raise OSError(host_errno.EINVAL, "invalid stdio seek origin")
                stream.file.seek(offset - (len(stream.pushback) if whence == 1 else 0), whence)
                stream.pushback.clear()
                if len(self.files.events) < 1000:
                    self.files.events.append({"operation": name, "path": stream.path, "offset": offset, "whence": whence,
                                              "position_after": stream.file.tell(), "raw_args": [hex(v) for v in args]})
                stream.eof = False
                self.sync_file_flags(file_pointer, stream)
                return 0
            if name in ("ftell", "ftello"):
                offset = stream.file.tell() - len(stream.pushback)
                if len(self.files.events) < 1000:
                    self.files.events.append({"operation": name, "path": stream.path, "position": offset})
                if offset < 0 or offset > 0x7fffffff:
                    raise OSError(host_errno.EOVERFLOW, "file offset exceeds legacy ARM32 off_t")
                return offset & 0xffffffff
            if name == "feof":
                return int(stream.eof)
            if name == "ferror":
                return int(bool(stream.error))
            if name == "clearerr":
                stream.eof, stream.error = False, 0
                self.sync_file_flags(file_pointer, stream)
                return 0
            if name == "fflush":
                stream.file.flush()
                return 0
            if name in ("fgetc", "getc"):
                value = bytes([stream.pushback.pop()]) if stream.pushback else stream.file.read(1)
                if not value:
                    stream.eof = True
                    self.sync_file_flags(file_pointer, stream)
                    return -1
                return value[0]
            if name == "ungetc":
                if a == 0xffffffff:
                    return -1
                if len(stream.pushback) >= 4096:
                    return -1
                stream.pushback.append(a & 255)
                stream.eof = False
                self.sync_file_flags(file_pointer, stream)
                return a & 255
        except (OSError, ValueError) as error:
            code = errno.from_host(error)
            self.word(self.errno_address, code)
            if stream is not None:
                stream.error = code
                self.sync_file_flags(file_pointer, stream)
            return 0 if name in ("fopen", "fread", "fwrite") else -1
        raise UnsupportedImport(name, args, self.machine.reg_read(arm.UC_ARM_REG_LR))

    def sync_file_flags(self, pointer, stream):
        readable, writable = stream.file.readable(), stream.file.writable()
        flags = 0x10 if readable and writable else 0x08 if writable else 0x04
        if stream.eof:
            flags |= 0x20
        if stream.error:
            flags |= 0x40
        self.machine.mem_write(pointer + 12, struct.pack("<H", flags))

    def import_call(self, machine, address, size, context):
        name = self.import_names.get(address)
        if name is None:
            name = f"import-address-{address:#x}"
        self.import_counts[name] += 1
        args = self.imported_args()
        if name == "qsort":
            from guest_sort import comparisons
            base, count, item_size, comparator = args
            if count < 2 or item_size == 0:
                return self.return_value(0)
            if count > 1_000_000 or count * item_size > HEAP_SIZE or not comparator:
                raise RuntimeError("invalid or oversized guest qsort")
            frame = {"base": base, "caller": machine.reg_read(arm.UC_ARM_REG_LR), "comparator": comparator,
                     "iterator": comparisons(base, count, item_size, bytes(machine.mem_read(base, count * item_size)))}
            self.sort_frames.setdefault(getattr(self, "current_thread", 0), []).append(frame)
            self.bindings_called.add(name)
            self.sort_next(frame)
            return
        if name == "pthread_once":
            pointer, callback = args[:2]
            if pointer in self.once_done:
                self.return_value(0)
            else:
                if any(p == pointer for p, _ in self.once_frames):
                    raise RuntimeError("recursive pthread_once initialization would deadlock")
                self.once_frames.append((pointer, machine.reg_read(arm.UC_ARM_REG_LR)))
                machine.reg_write(arm.UC_ARM_REG_LR, ONCE_RETURN)
                machine.reg_write(arm.UC_ARM_REG_PC, callback)
            self.bindings_called.add(name)
            return
        result = self.bind_import(name, args)
        self.bindings_called.add(name)
        self.return_value(result & 0xffffffff)

    def initialize_globals(self, progress=None):
        self.initializers_completed = []
        for section in self.elf.sections:
            if section["name"] != ".init_array":
                continue
            for index in range(section["size"] // 4):
                pointer = self.word(BASE + section["address"] + index * 4)
                if pointer in (0, 0xffffffff):
                    continue
                self.current_initializer = {"index": index, "elf_pointer": hex(pointer - BASE)}
                self.call_address(pointer, instruction_limit=2_000_000, timeout_us=5_000_000)
                self.initializers_completed.append(self.current_initializer)
                if progress is not None and len(self.initializers_completed) % 100 == 0:
                    progress(len(self.initializers_completed))
        self.current_initializer = None


def copy_import(runtime, args, name="memcpy"):
    """memcpy/memmove: a snapshot read makes overlapping copies memmove-safe."""
    destination, source, count = args[:3]
    if count > HEAP_SIZE:
        raise RuntimeError(f"{name} exceeds the guest memory budget")
    if count:
        runtime.machine.mem_write(destination, bytes(runtime.machine.mem_read(source, count)))
    return destination


def set_import(runtime, args):
    destination, value, count = args[:3]
    if count > HEAP_SIZE:
        raise RuntimeError("memset exceeds the guest memory budget")
    runtime.machine.mem_write(destination, bytes([value & 255]) * count)
    return destination


def malloc_import(runtime, args):
    result = runtime.heap.malloc(args[0])
    if not result:
        runtime.word(runtime.errno_address, errno.ENOMEM)
    return result


def free_import(runtime, args):
    runtime.heap.free(args[0])
    return 0


def strlen_import(runtime, args):
    return len(runtime.bytestring(args[0]))


def move_import(runtime, args):
    return copy_import(runtime, args, "memmove")


COMMON_IMPORTS = {"memcpy": copy_import, "memmove": move_import, "memset": set_import,
                  "malloc": malloc_import, "free": free_import, "strlen": strlen_import}
