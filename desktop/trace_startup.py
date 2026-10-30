#!/usr/bin/env python3
"""Trace original JNI startup against a small host-side interface model.

No Android or Java VM is started. Supported JNI operations manage opaque object
references and record method lookups. Unknown operations fail explicitly. This
is a contract-discovery probe, not a complete desktop platform implementation.
"""
import argparse
import hashlib
from pathlib import Path
import struct
import sys
import zipfile

from smoke_original import OriginalCodeProbe, BASE, HERE, arm, uc, require_equal
from audit_apk import CLIENT, ROOT, Elf32, write_json

JNI = 0x08000000
ENV = JNI
ENV_TABLE = JNI + 0x1000
VM = JNI + 0x2000
VM_TABLE = JNI + 0x3000
ENV_CALLS = JNI + 0x4000
VM_CALLS = JNI + 0x5000
OBJECT = JNI + 0x8000
TLS = JNI + 0x9000
JNI_VERSION_1_6 = 0x10006

# JNI table slots are from the JNI header, including its four reserved entries.
ENV_NAMES = {6: "FindClass", 21: "NewGlobalRef", 22: "DeleteGlobalRef",
             23: "DeleteLocalRef", 31: "GetObjectClass", 33: "GetMethodID", 113: "GetStaticMethodID"}


class StartupProbe(OriginalCodeProbe):
    def env_pointer(self):
        return ENV

    def __init__(self, elf):
        super().__init__(elf)
        self.string_page_size = self.machine.ctl_get_page_size()
        self.events = []
        self.references = {}
        self.methods = {}
        self.next_handle = JNI + 0x10000
        self.machine.mem_map(JNI, 0x100000)
        self.word(ENV, ENV_TABLE)
        self.word(VM, VM_TABLE)
        for index in range(234):
            self.word(ENV_TABLE + index * 4, ENV_CALLS + index * 4)
        for index in range(8):
            self.word(VM_TABLE + index * 4, VM_CALLS + index * 4)
        self.machine.mem_write(ENV_CALLS, bytes.fromhex('feffffea') * 234)
        self.machine.mem_write(VM_CALLS, bytes.fromhex('feffffea') * 8)
        self.machine.hook_add(uc.UC_HOOK_CODE, self.jni_call, begin=ENV_CALLS, end=VM_CALLS + 31)
        self.machine.mem_map(0xffff0000, 0x1000)
        self.word(0xffff0ffc, 3)
        for helper in (0xffff0fa0, 0xffff0fc0, 0xffff0fe0):
            self.machine.mem_write(helper, bytes.fromhex('feffffea'))
        self.machine.hook_add(uc.UC_HOOK_CODE, self.kernel_helper, begin=0xffff0000, end=0xffff0fff)
        self.kernel_events = []

    def kernel_helper(self, machine, address, size, context):
        # This probe has one guest execution thread. Ordered host operations
        # suffice for its barrier/CAS tests; a concurrent runtime needs a shared
        # memory model and per-thread TLS. ABI: docs.kernel.org/arch/arm/kernel_user_helpers.html
        if address == 0xffff0fc0:
            old = machine.reg_read(arm.UC_ARM_REG_R0)
            new = machine.reg_read(arm.UC_ARM_REG_R1)
            pointer = machine.reg_read(arm.UC_ARM_REG_R2)
            if pointer & 3:
                raise RuntimeError("unaligned kuser_cmpxchg operand")
            success = self.word(pointer) == old
            if success:
                self.word(pointer, new)
            flags = machine.reg_read(arm.UC_ARM_REG_CPSR)
            flags = (flags | (1 << 29)) if success else (flags & ~(1 << 29))
            machine.reg_write(arm.UC_ARM_REG_CPSR, flags)
            self.kernel_events.append({"helper": "cmpxchg", "success": success})
            self.return_value(0 if success else 1)
        elif address == 0xffff0fa0:
            self.kernel_events.append({"helper": "memory_barrier"})
            machine.reg_write(arm.UC_ARM_REG_PC, machine.reg_read(arm.UC_ARM_REG_LR))
        elif address == 0xffff0fe0:
            self.kernel_events.append({"helper": "get_tls"})
            self.return_value(TLS)
        else:
            raise RuntimeError(f"unsupported ARM kernel helper at {address:#x}")

    def word(self, address, value=None):
        if value is None:
            return struct.unpack("<I", self.machine.mem_read(address, 4))[0]
        self.machine.mem_write(address, struct.pack("<I", value))

    def string(self, address):
        if not address:
            raise RuntimeError("unexpected null JNI string")
        result = bytearray()
        while len(result) < 4096:
            pointer = address+len(result)
            count = min(256,self.string_page_size-pointer%self.string_page_size,4096-len(result))
            chunk = bytes(self.machine.mem_read(pointer,count))
            end = chunk.find(b'\0')
            if end >= 0:
                result.extend(chunk[:end])
                return result.decode('utf-8')
            result.extend(chunk)
        raise RuntimeError("JNI string exceeds probe limit")

    def reference(self, class_name, kind="object", **fields):
        handle = self.next_handle
        self.next_handle += 4
        self.references[handle] = {"class": class_name, "kind": kind, **fields}
        return handle

    def ref(self, handle):
        if handle not in self.references:
            raise RuntimeError(f"unknown or expired JNI reference {handle:#x}")
        return self.references[handle]

    def return_value(self, value=0):
        self.machine.reg_write(arm.UC_ARM_REG_R0, value)
        self.machine.reg_write(arm.UC_ARM_REG_PC, self.machine.reg_read(arm.UC_ARM_REG_LR))

    def jni_call(self, machine, address, size, context):
        args = [machine.reg_read(r) for r in (arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
                                             arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3)]
        if VM_CALLS <= address < VM_CALLS + 32:
            index = (address - VM_CALLS) // 4
            if index != 6:
                raise RuntimeError(f"unsupported JavaVM operation at slot {index}")
            require_equal(args[0], VM, "JavaVM receiver")
            require_equal(args[2], JNI_VERSION_1_6, "requested JNI version")
            self.word(args[1], self.env_pointer())
            self.events.append({"operation": "GetEnv", "version": hex(args[2])})
            self.return_value(0)
            return
        index = (address - ENV_CALLS) // 4
        operation = ENV_NAMES.get(index)
        if operation is None:
            raise RuntimeError(f"unsupported JNIEnv operation at slot {index}")
        require_equal(args[0], self.env_pointer(), "JNIEnv receiver")
        event = {"operation": operation}
        result = 0
        if operation == "FindClass":
            name = self.string(args[1])
            event["class"] = name
            result = self.reference(name, kind="class", scope="local")
        elif operation == "NewGlobalRef":
            if args[1]:
                ref = self.ref(args[1])
                event["class"] = ref["class"]
                fields = {key: value for key, value in ref.items() if key not in ("class", "kind", "scope")}
                result = self.reference(ref["class"], kind=ref["kind"], scope="global", **fields)
        elif operation in ("DeleteGlobalRef", "DeleteLocalRef"):
            if args[1]:
                event["class"] = self.ref(args[1])["class"]
                del self.references[args[1]]
        elif operation == "GetObjectClass":
            ref = self.ref(args[1])
            event["class"] = ref["class"]
            result = self.reference(ref["class"], kind="class", scope="local")
        elif operation in ("GetMethodID", "GetStaticMethodID"):
            ref = self.ref(args[1])
            require_equal(ref["kind"], "class", "method lookup receiver")
            event.update({"class": ref["class"], "method": self.string(args[2]),
                          "descriptor": self.string(args[3])})
            if operation == "GetStaticMethodID":
                event["static"] = True
            result = self.next_handle
            self.next_handle += 4
            self.methods[result] = {k: v for k, v in event.items() if k != "operation"}
        self.events.append(event)
        self.return_value(result)


def run(apk):
    with zipfile.ZipFile(apk) as z:
        elf = Elf32(z.read(CLIENT))
    probe = StartupProbe(elf)
    stages = []
    version = probe.call("JNI_OnLoad", VM, 0)
    require_equal(version, JNI_VERSION_1_6, "JNI_OnLoad result")
    stages.append("JNI_OnLoad")
    activity = probe.reference("net/livingmobile/sdr/app/SdrActivity", scope="borrowed")
    ui = probe.reference("net/livingmobile/sdr/appui/Root", scope="borrowed")
    probe.call("Java_net_livingmobile_sdr_app_SdrRoot_onCreate", ENV, 0, activity, ui)
    for symbol, expected_class in (
        ("_ZN4sdrx8internal4Root12_ndkActivityE", "net/livingmobile/sdr/app/SdrActivity"),
        ("_ZN4sdrx8internal4Root10_ndkUiRootE", "net/livingmobile/sdr/appui/Root"),
    ):
        ref = probe.ref(probe.word(BASE + elf.symbol(symbol)["address"]))
        require_equal(ref["class"], expected_class, "stored platform object")
        require_equal(ref["scope"], "global", "stored reference lifetime")
    stages.append("SdrRoot.onCreate: activity and UI global references verified")
    # Zeroed storage permits the cache routine to release its initially empty
    # reference field. This is not construction or initialization of audio.
    probe.call("_ZN3sdr3sfx19AndroidSoundManager8cacheJNIEv", OBJECT)
    stages.append("AndroidSoundManager.cacheJNI: method contracts captured")
    presets = probe.call("_Z26applicationRendererPresetsv")
    config = {}
    for key, getter in (
        ("renderer_type", "_ZNK4sdrx15RendererPresets15getRendererTypeEv"),
        ("depth_buffer", "_ZNK4sdrx15RendererPresets20isDepthBufferEnabledEv"),
        ("stencil_buffer", "_ZNK4sdrx15RendererPresets22isStencilBufferEnabledEv"),
        ("msaa", "_ZN4sdrx15RendererPresets13isMsaaEnabledEv"),
        ("msaa_samples", "_ZN4sdrx15RendererPresets17getNumMsaaSamplesEv"),
    ):
        config[key] = probe.call(getter, presets)
    require_equal(config, {"renderer_type": 0, "depth_buffer": 1, "stencil_buffer": 0,
                           "msaa": 0, "msaa_samples": 4}, "original renderer presets")
    kernel_calls = len(probe.kernel_events)
    require_equal(probe.call("_Z26applicationRendererPresetsv"), presets, "renderer singleton identity")
    require_equal(len(probe.kernel_events), kernel_calls, "initialized singleton avoids repeated guard acquisition")
    stages.append("applicationRendererPresets: original guarded singleton and graphics settings verified")
    return {"status": "passed", "host": sys.platform,
            "client_sha256": hashlib.sha256(elf.data).hexdigest(), "android_runtime": False,
            "jni_version": hex(version), "stages": stages,
            "method_lookups": list(probe.methods.values()), "events": probe.events,
            "renderer_presets": config, "kernel_helper_events": probe.kernel_events,
            "scope": "JNI contract discovery only. No Java methods were invoked; audio and full startup are not implemented."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, default=ROOT / "nemo.apk")
    parser.add_argument("--output", type=Path, default=HERE / "work" / "startup-trace.json")
    args = parser.parse_args()
    result = run(args.apk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print(f"PASS: original JNI_OnLoad, activity/UI reference setup and audio JNI cache routine")
    print(f"Recovered {len(result['method_lookups'])} Java method contracts; no Android runtime")
    print(f"Original renderer settings: {result['renderer_presets']}")
    for method in result["method_lookups"]:
        print(f"  {method['class']}.{method['method']}{method['descriptor']}")
    print("Contract-discovery probe only. No audio or full client startup.")


if __name__ == "__main__":
    main()
