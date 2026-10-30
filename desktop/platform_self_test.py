"""Asset-free diagnostics for a packaged desktop runtime."""
import ctypes as C
import io
import json
from pathlib import Path
import platform
import struct
import wave


def main():
    from compiled_bridge import load_library
    from desktop_gl import DesktopGL, U, I, P
    from desktop_paths import desktop_directory
    from jit_api import load, library_path, Stop, STOP_TRAP
    from smoke_original import uc
    import miniaudio
    import tkinter as tk

    result = {'platform':platform.platform(), 'machine':platform.machine(),
              'scope':'Synthetic native CPU, library ABI, OpenGL, Tk and audio decoding; no original game assets'}
    from gameplay_contract import verify
    result['gameplay_contract'] = verify()
    load_library()
    result['native_bridge_abi'] = 'passed'
    lib = load()
    code = C.create_string_buffer(4096)
    # ARM: mov r0,#7; add r0,r0,#5; branch to sentinel at100c.
    C.memmove(code,bytes.fromhex('0700a0e3050080e2ffffffea'),12)
    cpu = lib.dp_create(1)
    if not cpu:
        raise RuntimeError('JIT creation failed')
    try:
        assert lib.dp_map(cpu,0x1000,4096,C.addressof(code)) == 0
        lib.dp_add_trap(cpu,0x100c,1)
        lib.dp_set_cpsr(cpu,0x10)
        stop = Stop()
        reason = lib.dp_run(cpu,0x1000,100,C.byref(stop))
        assert reason & STOP_TRAP and lib.dp_get_reg(cpu,0) == 12, (reason,stop.pc)
        result['synthetic_arm_jit'] = 'passed'
    finally:
        lib.dp_destroy(cpu)
    C.CDLL(str(library_path('jit_dispatch_v7.dll'))).jd_run
    result['dispatcher_load'] = 'passed'
    machine = uc.Uc(uc.UC_ARCH_ARM,uc.UC_MODE_ARM)
    machine.mem_map(0x1000,4096)
    machine.mem_write(0x1000,bytes.fromhex('0700a0e3050080e2'))
    machine.emu_start(0x1000,0x1008,count=2)
    from unicorn import arm_const
    assert machine.reg_read(arm_const.UC_ARM_REG_R0) == 12
    result['unicorn'] = 'passed'
    graphics = DesktopGL(width=64,height=64,visible=False)
    try:
        graphics.checked('glClearColor',None,(C.c_float,)*4,(1,0,0,1))
        graphics.checked('glClear',None,(U,),(0x4000,))
        pixel = (C.c_ubyte*4)()
        graphics.checked('glReadPixels',None,(I,I,I,I,U,U,P),(32,32,1,1,0x1908,0x1401,pixel))
        assert list(pixel) == [255,0,0,255],list(pixel)
        result['opengl'] = graphics.info
    finally:
        graphics.close()
    window = tk.Tk()
    window.withdraw()
    window.update()
    window.destroy()
    result['tk_dialogs'] = 'passed'
    audio = io.BytesIO()
    with wave.open(audio,'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(struct.pack('<8h',0,100,-100,200,-200,50,-50,0))
    decoded = miniaudio.decode(audio.getvalue())
    assert len(decoded.samples) > 0
    result['audio_decoding'] = 'passed'
    result['status'] = 'passed'
    output = desktop_directory()/'work/platform-self-test.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
