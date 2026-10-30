#!/usr/bin/env python3
"""Trace original initialization and optional desktop rendering; stop at missing interfaces."""
import argparse
import hashlib
from pathlib import Path
import sys
import time
import zipfile

from audit_apk import CLIENT, ROOT, Elf32, write_json
from smoke_original import HERE, arm
from native_platform import NativePlatform, UnsupportedImport
from guest_files import APK_PATH, DATA_PATH, CACHE_PATH, GuestFiles
from trace_startup import ENV, VM
from cooperative_platform import CooperativePlatform
from smoke_original import BASE


def run(apk, stage="globals", data=None, progress=None, graphics=None, frames=1, retain_runtime=False,
        frame_timeout=120, frame_instructions=200_000_000, accelerate_bitmaps=True, server=None, host_poll=None,
        time_slice_us=0):
    if frames < 1:
        raise ValueError("frame count must be positive")
    if frame_timeout <= 0 or frame_instructions < 1:
        raise ValueError("frame execution limits must be positive")
    started = time.perf_counter()
    with zipfile.ZipFile(apk) as z:
        elf = Elf32(z.read(CLIENT))
    files = GuestFiles(apk, data or HERE / "work" / "runtime-data")
    if stage in ("renderer", "frame"):
        from graphics_platform import GraphicsPlatform
        if graphics is None:
            raise ValueError("renderer stage requires a desktop OpenGL context")
        runtime = GraphicsPlatform(elf, files, graphics)
        runtime.bitmap_acceleration = accelerate_bitmaps
    else:
        runtime = CooperativePlatform(elf, files) if stage == "paths" else NativePlatform(elf, files)
    if server:
        from desktop_network import DesktopNetwork
        runtime.network = DesktopNetwork(server)
    if isinstance(runtime, CooperativePlatform):
        runtime.host_poll = host_poll
        # Use the selected scheduler from the first constructor onward. Switching
        # from counted execution after renderer setup also discarded all startup
        # translations just before the first game frame.
        runtime.time_slice_us = time_slice_us
    result = {"status": "passed", "host": sys.platform,
              "client_sha256": hashlib.sha256(elf.data).hexdigest(),
              "stage": stage, "phases_completed": [],
              "frames_requested": frames if stage == "frame" else 0, "frames_completed": 0,
              "frame_timings": [],
              "scope": "Original global, JNI/path setup and optional renderer initialization. Gameplay remains unverified."}
    phase = "global initializers"
    phase_started = time.perf_counter()
    phase_timings = []
    def completed():
        nonlocal phase_started
        now = time.perf_counter()
        phase_timings.append({'phase':phase,'seconds':now-phase_started})
        phase_started = now
    try:
        runtime.initialize_globals(progress)
        result["phases_completed"].append(phase)
        completed()
        if stage in ("paths", "renderer", "frame"):
            phase = "JNI_OnLoad"
            runtime.call("JNI_OnLoad", VM, 0)
            result["phases_completed"].append(phase)
            completed()
            phase = "SdrRoot.onCreate"
            activity = runtime.reference("net/livingmobile/sdr/app/SdrActivity", scope="borrowed")
            ui = runtime.reference("net/livingmobile/sdr/appui/Root", scope="borrowed")
            runtime.call("Java_net_livingmobile_sdr_app_SdrRoot_onCreate", ENV, 0, activity, ui)
            result["phases_completed"].append(phase)
            completed()
            phase = "SdrRoot.initSoundSystem"
            runtime.call("Java_net_livingmobile_sdr_app_SdrRoot_initSoundSystem", ENV, 0)
            result["phases_completed"].append(phase)
            completed()
            phase = "SdrRoot.onGameResume"
            runtime.call("Java_net_livingmobile_sdr_app_SdrRoot_onGameResume", ENV, 0)
            result["phases_completed"].append(phase)
            completed()
            phase = "SdrRoot.initPaths"
            path_handles = [runtime.java_string(path) for path in (APK_PATH, DATA_PATH, CACHE_PATH)]
            runtime.call_address(BASE + elf.symbol("Java_net_livingmobile_sdr_app_SdrRoot_initPaths")["address"],
                                 ENV, 0, *path_handles, instruction_limit=10_000_000, timeout_us=10_000_000)
            result["phases_completed"].append(phase)
            result["paths_initialized"] = True
            completed()
            result["resolver_paths"] = {}
            for symbol, expected in (("_ZN3sdr2io8Resolver12_packagePathE", APK_PATH),
                                     ("_ZN3sdr2io8Resolver9_dataPathE", DATA_PATH),
                                     ("_ZN3sdr2io8Resolver10_cachePathE", CACHE_PATH)):
                pointer = runtime.call("_ZNK3sdr6String5asciiEv", BASE + elf.symbol(symbol)["address"])
                value = runtime.bytestring(pointer).decode("utf-8")
                runtime.heap.free(pointer)
                if value != expected:
                    raise RuntimeError(f"resolver path mismatch for {symbol}: {value!r}")
                result["resolver_paths"][symbol] = value
            phase = "worker startup"
            runtime.pump_workers(max_quanta=4)
            result["phases_completed"].append(phase)
            completed()
            if not runtime.milestones.get("applicationPreInit", {}).get("returned"):
                phase = "applicationPreInit"
                raise RuntimeError("native path setup returned after catching an exception; applicationPreInit did not finish")
            if stage in ("renderer", "frame"):
                phase = "SdrRoot.init renderer"
                runtime.call_address(BASE + elf.symbol("Java_net_livingmobile_sdr_app_SdrRoot_init")["address"],
                                     ENV, 0, graphics.width, graphics.height,
                                     instruction_limit=10_000_000, timeout_us=10_000_000)
                result["phases_completed"].append(phase)
                result["renderer_initialized"] = True
                completed()
                if stage == "frame":
                    for index in range(frames):
                        phase = f"SdrRenderer.drawFrame {index + 1}"
                        frame_started = time.perf_counter()
                        runtime.call_address(BASE + elf.symbol("Java_net_livingmobile_sdr_app_SdrRenderer_drawFrame")["address"],
                                             ENV, 0, instruction_limit=frame_instructions, timeout_us=int(frame_timeout * 1_000_000))
                        result["frames_completed"] += 1
                        result["frame_timings"].append({"frame": index + 1, "seconds": time.perf_counter() - frame_started})
                        if progress is not None:
                            print(f"Original frames completed: {index + 1}", flush=True)
                        runtime.pump_workers(max_quanta=4)
                    result["phases_completed"].append("SdrRenderer.drawFrame")
    except Exception as error:
        result.update({"status": "stopped", "error": str(error), "error_type": type(error).__name__,
                       "phase": phase, "initializer": runtime.current_initializer,
                       "guest_pc": hex(runtime.machine.reg_read(arm.UC_ARM_REG_PC))})
        if isinstance(error, UnsupportedImport):
            result["unsupported_import"] = {"name": error.name, "arguments": [hex(a) for a in error.arguments], "caller": hex(error.caller)}
    result['phase_timings'] = phase_timings
    result.update({"initializers_completed": len(runtime.initializers_completed),
                   "revival_exchange": getattr(runtime, 'revival_exchange', {'active': False}),
                   "elapsed_seconds": time.perf_counter() - started,
                   "imports_called": dict(runtime.import_counts), "destructors_registered": len(runtime.destructors),
                   "live_allocations": len(runtime.heap.live), "guest_heap_bytes_in_use": sum(s for _, s in runtime.heap.live.values()),
                   "file_events": files.events, "jni_events": runtime.events,
                   "exception_events": runtime.exception_events, "engine_logs": runtime.logs,
                   "platform_logs": runtime.platform_logs,
                   "milestones": runtime.milestones})
    result["analytics"] = {"backend": "local; no uploads", "initialized": runtime.analytics.initialized,
                           "comscore_initialized": runtime.analytics.comscore_initialized,
                           "google_initialized": runtime.analytics.google_initialized}
    result["analytics"]["kochava_initialized"] = runtime.analytics.kochava_initialized
    if isinstance(runtime, CooperativePlatform):
        result["workers"] = runtime.worker_summary()
        result["thread_events"] = runtime.thread_events
    if graphics is not None:
        result["graphics"] = graphics.info
        result["gl_events"] = list(runtime.gl_events)
        if getattr(runtime,'_native_bridge',None) is not None:
            result['compiled_bridge'] = runtime._native_bridge.summary()
        result["gl_events_total"] = runtime.gl_events_total
        result["draw_calls"] = runtime.draws
        result["bitmap_accelerations"] = runtime.bitmap_accelerations
        result["checksum_accelerations"] = runtime.checksum_stats
        result["json_lookup_accelerations"] = runtime.json_lookup_stats
        result["zip_lookup_accelerations"] = runtime.zip_lookup_stats
        result["png_filter_accelerations"] = runtime.png_filter_stats
        result["png_interlace_accelerations"] = runtime.png_interlace_stats
        result["png_combine_accelerations"] = runtime.png_combine_stats
        result["pvrtc_transfers"] = runtime.pvrtc_transfers
        result["graphics_bridge"] = {"matrix_palette": "CPU OES vertex skinning",
                                     "vertex_reads": {"enabled": runtime.vertex_reader.enabled,
                                                      "reads": runtime.vertex_reader.reads,
                                                      "batches": runtime.vertex_reader.batches,
                                                      "fallbacks": runtime.vertex_reader.fallbacks},
                                     "time_slice_us": runtime.time_slice_us,
                                     "float_arithmetic": "guest VFP with original legacy fallbacks",
                                     "matrix_copy": runtime.copy_acceleration,
                                     "pvrtc": "CPU PVRTC1 2/4bpp decoding",
                                     "bitmap_conversion": "exact host RGBA4444" if runtime.bitmap_acceleration else "original ARM",
                                     "palette_matrices": runtime.palette.capacity,
                                     "vertex_units": runtime.palette.vertex_units}
    if runtime.network is not None:
        result["network"] = {"server": runtime.network.origin, "events": runtime.network.events}
    if retain_runtime:
        return result, runtime
    from desktop_audio import close_all
    close_all(runtime)
    files.close_all()
    if runtime.network is not None:
        runtime.network.close_all()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, default=ROOT / "nemo.apk")
    parser.add_argument("--stage", choices=("globals", "paths", "renderer", "frame"), default="globals")
    parser.add_argument("--data", type=Path, default=HERE / "work" / "runtime-data")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frames", type=int, default=1, help="Original drawFrame callbacks for --stage frame")
    parser.add_argument("--frame-timeout", type=float, default=120, help="Maximum seconds per original callback")
    parser.add_argument("--frame-instructions", type=int, default=200_000_000, help="Maximum ARM instructions per callback")
    parser.add_argument("--original-bitmaps", action="store_true", help="Keep the original slow ARM RGBA4444 conversion")
    parser.add_argument("--server", help="Loopback HTTP server origin for desktop networking")
    parser.add_argument('--time-slice-us', type=int, default=0,
                        help='Use wall-time scheduling; zero retains instruction-counted diagnostics')
    args = parser.parse_args()
    if args.output is None:
        args.output = HERE / "work" / ("initialization-result.json" if args.stage == "globals" else f"{args.stage}-result.json")
    graphics = None
    try:
        if args.stage in ("renderer", "frame"):
            from desktop_gl import DesktopGL
            graphics = DesktopGL()
            print(f"Desktop OpenGL: {graphics.info['renderer']}", flush=True)
        result = run(args.apk, args.stage, args.data,
                     lambda count: print(f"Original global initializers completed: {count}", flush=True), graphics, args.frames,
                     frame_timeout=args.frame_timeout, frame_instructions=args.frame_instructions,
                     accelerate_bitmaps=not args.original_bitmaps, server=args.server, time_slice_us=args.time_slice_us)
        if args.stage == "frame" and result["status"] == "passed":
            result["capture"] = graphics.capture(args.output.with_suffix(".png"))
            result["capture"]["scope"] = f"After {args.frames} original drawFrame callbacks; may be splash only."
    finally:
        if graphics is not None:
            graphics.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print(f"{result['status'].upper()}: {result['initializers_completed']} original global initializers completed")
    print(f"{result['destructors_registered']} destructors registered; {result['live_allocations']} live allocations")
    if result["status"] != "passed":
        print(f"Stopped in {result['phase']} ({result['initializer']}): {result['error']}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
