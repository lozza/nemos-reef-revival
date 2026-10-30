"""Execute original ARM workers on one CPU with explicit sleep/lock scheduling.

Guest contexts share memory and have separate stacks, TLS and JNI attachment.
This is cooperative execution, not a complete pthread implementation. Worker
condition waits release/reacquire guest mutexes. Main-thread blocking schedules
ready workers. Non-default thread attributes and TLS destructor teardown remain
explicit unsupported operations.
"""
import guest_errno as errno
import time

from native_platform import NativePlatform, UnsupportedImport
from smoke_original import arm, RETURN
from trace_startup import ENV, ENV_TABLE, VM, VM_CALLS, TLS, JNI_VERSION_1_6

THREAD_BASE = 0x20000000
THREAD_SLOT = 0x100000
THREAD_RETURN = 0x0a000000


class CooperativePlatform(NativePlatform):
    def __init__(self, elf, files=None):
        super().__init__(elf, files)
        self.current_thread = 0
        self._time_slice_us = 0
        self._slice_deadline = 0.0
        self.boundary_preemptions = 0
        self.slice_timer = None
        self.reusable_timer = True
        self.threads = {0: {"id": 0, "state": "main", "tls": TLS, "env": ENV,
                            "attached": True, "values": {}}}
        self.next_thread = 1
        self.thread_events = []
        self.last_scheduled = 0
        self.yielded = False
        self.host_poll = None
        self.machine.mem_map(THREAD_RETURN, 0x1000)

    @property
    def time_slice_us(self):
        return self._time_slice_us

    @time_slice_us.setter
    def time_slice_us(self, value):
        if value != 0 and not 1000 <= value <= 100_000:
            raise ValueError('time slices must be zero or 1–100 milliseconds')
        if self.current_thread != 0:
            raise RuntimeError('scheduling mode must change outside worker execution')
        if value != self._time_slice_us:
            # Counted and uncounted blocks must never share translated code.
            self.machine.ctl_flush_tb()
            self._time_slice_us = value

    def env_pointer(self):
        return self.threads[self.current_thread]["env"]

    def emulate_slice(self, begin, until, timeout, count):
        bridge = getattr(self,'_native_bridge',None)
        if bridge is not None:
            bridge.prepare_slice(self._slice_deadline)
            try:
                return self._emulate_slice(begin,until,timeout,count)
            finally:
                bridge.sync_counts()
        return self._emulate_slice(begin,until,timeout,count)

    def _emulate_slice(self, begin, until, timeout, count):
        backend = getattr(self,'_experimental_jit',None)
        if backend is not None and backend.handle is not None:
            # The JIT checks a native wall deadline between bounded instruction
            # batches and import calls; it does not need a Python timer thread.
            return self.machine.emu_start(begin,until,timeout=timeout,count=count)
        if not self.time_slice_us or not self.reusable_timer:
            return self.machine.emu_start(begin, until, timeout=timeout, count=count)
        if self.slice_timer is None:
            from slice_timer import SliceTimer
            self.slice_timer = SliceTimer(self.machine)
        with self.slice_timer.limit(self._slice_deadline):
            return self.machine.emu_start(begin, until, timeout=0, count=count)

    def close_timing(self):
        backend = getattr(self,'_experimental_jit',None)
        if backend is not None:
            backend.close()
            self._experimental_jit = None
        bridge = getattr(self,'_native_bridge',None)
        if bridge is not None:
            bridge.close()
        if self.slice_timer is not None:
            self.slice_timer.close()
            self.slice_timer = None

    def return_value(self, value=0):
        result = super().return_value(value)
        if self._slice_deadline and time.monotonic() >= self._slice_deadline:
            # Check after updating PC, so a long host callback cannot resume at
            # its entry and repeat side effects when the native timer expires.
            self.boundary_preemptions += 1
            self.machine.emu_stop()
        return result

    def kernel_helper(self, machine, address, size, context):
        if address == 0xffff0fe0:
            self.kernel_events.append({"helper": "get_tls", "thread": self.current_thread})
            return self.return_value(self.threads[self.current_thread]["tls"])
        return super().kernel_helper(machine, address, size, context)

    def jni_call(self, machine, address, size, context):
        if VM_CALLS <= address < VM_CALLS + 32:
            vm, destination, version, _ = self.imported_args()
            if vm != VM:
                raise RuntimeError("invalid JavaVM receiver")
            index = (address - VM_CALLS) // 4
            thread = self.threads[self.current_thread]
            if index == 4:  # AttachCurrentThread
                if version:
                    raise UnsupportedImport("AttachCurrentThread with arguments", self.imported_args(), machine.reg_read(arm.UC_ARM_REG_LR))
                thread["attached"] = True
                self.word(destination, thread["env"])
                self.events.append({"operation": "AttachCurrentThread", "thread": self.current_thread})
                return self.return_value(0)
            if index == 5:  # DetachCurrentThread
                thread["attached"] = False
                self.events.append({"operation": "DetachCurrentThread", "thread": self.current_thread})
                return self.return_value(0)
            if index == 6:  # GetEnv
                if version != JNI_VERSION_1_6:
                    self.word(destination, 0)
                    return self.return_value(-3 & 0xffffffff)
                self.word(destination, thread["env"] if thread["attached"] else 0)
                self.events.append({"operation": "GetEnv", "version": hex(version), "thread": self.current_thread})
                return self.return_value(0 if thread["attached"] else 0xfffffffe)
        if not self.threads[self.current_thread]["attached"]:
            raise RuntimeError("JNI operation from a detached guest thread")
        return super().jni_call(machine, address, size, context)

    def create_thread(self, destination, attributes, routine, argument):
        if attributes:
            raise UnsupportedImport("pthread_create with attributes", [destination, attributes, routine, argument], self.machine.reg_read(arm.UC_ARM_REG_LR))
        if self.next_thread > 32:
            return errno.EAGAIN
        identifier = self.next_thread
        self.next_thread += 1
        start = THREAD_BASE + identifier * THREAD_SLOT
        self.machine.mem_map(start, THREAD_SLOT)
        env = start + 0x200
        self.word(env, ENV_TABLE)
        parent = self.machine.context_save()
        for register in (arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1, arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3):
            self.machine.reg_write(register, 0)
        self.machine.reg_write(arm.UC_ARM_REG_R0, argument)
        self.machine.reg_write(arm.UC_ARM_REG_SP, start + THREAD_SLOT - 16)
        self.machine.reg_write(arm.UC_ARM_REG_LR, THREAD_RETURN)
        self.machine.reg_write(arm.UC_ARM_REG_PC, routine)
        context = self.machine.context_save()
        self.machine.context_restore(parent)
        self.threads[identifier] = {"id": identifier, "state": "ready", "context": context,
                                    "tls": start, "env": env, "attached": False, "values": {},
                                    "routine": routine, "argument": argument}
        self.word(destination, identifier)
        self.thread_events.append({"operation": "create", "thread": identifier, "routine": hex(routine), "argument": hex(argument)})
        return 0

    def get_mutex(self, pointer):
        if pointer not in self.mutexes:
            value = self.word(pointer)
            if value not in (0, 0x4000, 0x8000):
                raise RuntimeError(f"unsupported Bionic mutex value {value:#x}")
            self.mutexes[pointer] = {"kind": value >> 14, "depth": 0, "owner": None, "waiters": []}
        return self.mutexes[pointer]

    def suspend(self, state, **fields):
        if self.once_frames:
            raise RuntimeError("suspension inside pthread_once requires per-thread callback frames")
        self.threads[self.current_thread].update(state=state, **fields)
        self.yielded = True
        self.return_value(0)
        self.machine.emu_stop()

    def release_mutex(self, pointer):
        mutex = self.get_mutex(pointer)
        if mutex["owner"] != self.current_thread or not mutex["depth"]:
            return errno.EPERM
        mutex["depth"] -= 1
        if mutex["depth"] == 0:
            mutex["owner"] = None
            if mutex["waiters"]:
                successor = mutex["waiters"].pop(0)
                mutex.update(owner=successor, depth=1)
                self.threads[successor]["state"] = "ready"
        return 0

    def get_condition(self, pointer):
        if pointer not in self.conditions:
            if self.word(pointer) != 0:
                raise RuntimeError("unknown static Bionic condition value")
            self.conditions[pointer] = {"waiters": []}
        return self.conditions[pointer]

    def wake_condition(self, condition, count):
        for _ in range(min(count, len(condition["waiters"]))):
            identifier = condition["waiters"].pop(0)
            thread = self.threads[identifier]
            mutex = self.get_mutex(thread["wait_mutex"])
            if mutex["owner"] is None:
                mutex.update(owner=identifier, depth=1)
                thread["state"] = "ready"
            else:
                mutex["waiters"].append(identifier)
                thread["state"] = "mutex"
            self.thread_events.append({"operation": "condition_wake", "thread": identifier})

    def bind_import(self, name, args):
        a, b, c, d = args[:4]
        if name == "pthread_create":
            return self.create_thread(a, b, c, d)
        if name in ("usleep", "sleep"):
            duration = a / 1_000_000 if name == "usleep" else a
            self.thread_events.append({"operation": "sleep", "thread": self.current_thread, "seconds": duration})
            self.suspend("sleeping", wake=time.monotonic() + duration)
            return 0
        if name == "pthread_mutex_init":
            kind = self.word(b) & 15 if b else 0
            if kind not in (0, 1, 2):
                return errno.EINVAL
            self.word(a, kind << 14)
            self.mutexes[a] = {"kind": kind, "depth": 0, "owner": None, "waiters": []}
            return 0
        if name == "pthread_mutex_lock":
            mutex = self.get_mutex(a)
            if mutex["owner"] is None:
                mutex.update(owner=self.current_thread, depth=1)
                return 0
            if mutex["owner"] == self.current_thread:
                if mutex["kind"] == 1:
                    mutex["depth"] += 1
                    return 0
                if mutex["kind"] == 2:
                    return errno.EDEADLK
            mutex["waiters"].append(self.current_thread)
            self.suspend("mutex", mutex=a)
            return 0
        if name == "pthread_mutex_unlock":
            return self.release_mutex(a)
        if name == "pthread_mutex_destroy":
            mutex = self.get_mutex(a)
            if mutex["owner"] is not None or mutex["waiters"]:
                return errno.EBUSY
            del self.mutexes[a]
            self.word(a, 0xdead)
            return 0
        if name == "pthread_cond_init":
            if b and self.word(b):
                raise UnsupportedImport("non-default condition attributes", args, self.machine.reg_read(arm.UC_ARM_REG_LR))
            self.word(a, 0)
            self.conditions[a] = {"waiters": []}
            return 0
        if name == "pthread_cond_wait":
            mutex = self.get_mutex(b)
            if mutex["owner"] != self.current_thread or mutex["depth"] != 1:
                raise RuntimeError("condition wait requires a mutex owned once by the current guest thread")
            condition = self.get_condition(a)
            self.release_mutex(b)
            condition["waiters"].append(self.current_thread)
            self.thread_events.append({"operation": "condition_wait", "thread": self.current_thread})
            self.suspend("condition", wait_mutex=b, condition=a)
            return 0
        if name in ("pthread_cond_signal", "pthread_cond_broadcast"):
            condition = self.get_condition(a)
            self.wake_condition(condition, 1 if name == "pthread_cond_signal" else len(condition["waiters"]))
            return 0
        if name == "pthread_cond_destroy":
            condition = self.get_condition(a)
            if condition["waiters"]:
                return errno.EBUSY
            del self.conditions[a]
            self.word(a, 0xdead)
            return 0
        if name == "pthread_getspecific":
            return self.threads[self.current_thread]["values"].get(a, 0)
        if name == "pthread_setspecific":
            if a not in self.thread_keys:
                return errno.EINVAL
            self.threads[self.current_thread]["values"][a] = b
            return 0
        if name == "pthread_key_delete":
            if a not in self.thread_keys:
                return errno.EINVAL
            del self.thread_keys[a]
            for thread in self.threads.values():
                thread["values"].pop(a, None)
            return 0
        return super().bind_import(name, args)

    def call_address(self, address, *args, instruction_limit=100_000, timeout_us=2_000_000):
        if self.current_thread != 0:
            raise RuntimeError("host calls must start outside worker execution")
        self.prepare_call(*args)
        self.machine.reg_write(arm.UC_ARM_REG_PC, address)
        main = self.threads[0]
        main["state"] = "running"
        return self.resume_call(instruction_limit=instruction_limit, timeout_us=timeout_us)

    def resume_call(self, instruction_limit=100_000, timeout_us=2_000_000):
        """Resume guest state; time deadlines always apply, instruction limits in counted mode."""
        if self.current_thread != 0:
            raise RuntimeError("resume must start outside worker execution")
        main = self.threads[0]
        if main["state"] not in ("running", "ready"):
            raise RuntimeError(f"cannot resume main thread in state {main['state']}")
        main["state"] = "running"
        address = self.machine.reg_read(arm.UC_ARM_REG_PC)
        deadline = time.monotonic() + timeout_us / 1_000_000
        remaining = instruction_limit
        while (remaining > 0 or self.time_slice_us) and time.monotonic() < deadline:
            if self.host_poll is not None:
                self.host_poll()
            quantum = min(100_000, remaining)
            self.yielded = False
            pc = self.machine.reg_read(arm.UC_ARM_REG_PC)
            thumb = bool(self.machine.reg_read(arm.UC_ARM_REG_CPSR) & 32)
            time_left = max(1, int((deadline - time.monotonic()) * 1_000_000))
            self._slice_deadline = time.monotonic() + min(time_left, self.time_slice_us) / 1_000_000 if self.time_slice_us else 0.0
            try:
                self.emulate_slice(pc | int(thumb), RETURN,
                                       count=0 if self.time_slice_us else quantum,
                                       timeout=min(time_left, self.time_slice_us) if self.time_slice_us else time_left)
            finally:
                self._slice_deadline = 0.0
            if self.machine.reg_read(arm.UC_ARM_REG_PC) == RETURN:
                main["state"] = "main"
                return self.machine.reg_read(arm.UC_ARM_REG_R0)
            if not self.yielded:
                if not self.time_slice_us:
                    remaining -= quantum
                main["state"] = "ready"
            # A quantum may end in the middle of a nonblocking once initializer.
            # Its callback stack belongs to the main CPU context; finish it
            # before switching threads. Explicit blocking still fails in suspend.
            if self.once_frames and not self.yielded:
                main["state"] = "running"
                continue
            # pump_workers preserves this suspended main CPU context. Mutex
            # ownership is transferred before a waiter resumes after its import.
            while time.monotonic() < deadline:
                self.pump_workers()
                if main["state"] == "sleeping" and main["wake"] <= time.monotonic():
                    main["state"] = "ready"
                if main["state"] == "ready":
                    main["state"] = "running"
                    break
                runnable = any(t["state"] in ("ready", "sleeping") for i, t in self.threads.items() if i)
                if not runnable and main["state"] != "sleeping":
                    raise RuntimeError(f"guest deadlock: main is waiting in {main['state']} with no runnable worker")
                time.sleep(0.001)
        raise RuntimeError(f"function at {address:#x} exceeded its cooperative execution budget")

    def pump_workers(self, max_quanta=32):
        """Run ready workers; sleeping ones retain original contexts until due."""
        if self.current_thread != 0:
            raise RuntimeError("worker pumping must start from the host/main thread")
        if self.once_frames:
            raise RuntimeError("worker scheduling inside an unfinished pthread_once callback is not implemented")
        parent = self.machine.context_save()
        original_errno = self.errno_address
        count = 0
        try:
            for _ in range(max_quanta):
                if self.host_poll is not None:
                    self.host_poll()
                now = time.monotonic()
                ready = []
                for identifier, thread in self.threads.items():
                    if identifier == 0:
                        continue
                    if thread["state"] == "sleeping" and thread["wake"] <= now:
                        thread["state"] = "ready"
                    if thread["state"] == "ready":
                        ready.append(identifier)
                if not ready:
                    break
                identifier = next((i for i in sorted(ready) if i > self.last_scheduled), min(ready))
                thread = self.threads[identifier]
                self.last_scheduled = self.current_thread = identifier
                self.errno_address = thread["tls"] + 0x100
                self.machine.context_restore(thread["context"])
                self.yielded = False
                pc = self.machine.reg_read(arm.UC_ARM_REG_PC)
                thumb = bool(self.machine.reg_read(arm.UC_ARM_REG_CPSR) & 32)
                try:
                    self._slice_deadline = time.monotonic() + self.time_slice_us / 1_000_000 if self.time_slice_us else 0.0
                    self.emulate_slice(pc | int(thumb), THREAD_RETURN,
                                           count=0 if self.time_slice_us else 100_000,
                                           timeout=self.time_slice_us or 1_000_000)
                except Exception as error:
                    thread["context"] = self.machine.context_save()
                    thread["state"] = "stopped"
                    self.thread_events.append({"operation": "platform_stop", "thread": identifier,
                                               "pc": hex(self.machine.reg_read(arm.UC_ARM_REG_PC)), "error": str(error)})
                    raise
                finally:
                    self._slice_deadline = 0.0
                thread["context"] = self.machine.context_save()
                if self.machine.reg_read(arm.UC_ARM_REG_PC) == THREAD_RETURN:
                    if any(self.thread_keys.get(key, {}).get("destructor") and value for key, value in thread["values"].items()):
                        raise RuntimeError("guest thread exit with TLS destructors requires teardown support")
                    thread.update(state="finished", result=self.machine.reg_read(arm.UC_ARM_REG_R0))
                    self.thread_events.append({"operation": "exit", "thread": identifier, "result": thread["result"]})
                elif not self.yielded:
                    thread["state"] = "ready"  # instruction/time quantum expired
                count += 1
                if self.threads[0]["state"] == "ready":
                    break
        finally:
            self.current_thread = 0
            self.errno_address = original_errno
            self.machine.context_restore(parent)
        return count

    def worker_summary(self):
        return [{"id": identifier, "state": thread["state"], "attached": thread["attached"],
                 "routine": hex(thread["routine"]), "stack_and_tls": hex(thread["tls"])}
                for identifier, thread in self.threads.items() if identifier]
