"""Development desktop window for the original client; gameplay is still incomplete."""
import argparse
from collections import deque
from pathlib import Path
import time
import sys
import os
import math

from audit_apk import ROOT, write_json
from desktop_gl import DesktopGL, glfw
from desktop_input import DesktopInput
from initialize_client import run
from smoke_original import BASE, HERE, arm
from trace_startup import ENV


def launch(args):
    launch_started = time.perf_counter()
    graphics = DesktopGL(visible=not args.hidden)
    # Keep full validation in graphics fixtures/debug contexts. During play,
    # validate the frame rather than querying the driver after every GL call.
    graphics.check_each_call = False
    glfw.set_window_attrib(graphics.window, glfw.RESIZABLE, glfw.FALSE)
    if getattr(args, 'fullscreen', False):
        graphics.toggle_fullscreen()
    glfw.swap_interval(1)
    pending = deque()
    frame_times = deque(maxlen=240)
    frame_details = deque(maxlen=240)
    pressed = False
    wheel_steps = 0.0
    last_poll = 0
    runtime = None
    overlay = None
    from message_box_overlay import MessageBoxOverlay
    dialog = MessageBoxOverlay(visible=not args.hidden)
    if not args.hidden:
        from text_overlay import TextOverlay
        overlay = TextOverlay(graphics)
    report = {"status": "starting", "frames_completed": 0,
              "scope": "Desktop prototype with original reef, pointer/text input and audio; extended gameplay remains unverified."}

    def position():
        x, y = glfw.get_cursor_pos(graphics.window)
        return graphics.pointer(x, y, clamp=pressed)

    def button(window, button, action, modifiers):
        nonlocal pressed
        if button != glfw.MOUSE_BUTTON_LEFT or action not in (glfw.PRESS, glfw.RELEASE):
            return
        point = position()
        if point is None or (action == glfw.RELEASE and not pressed):
            return
        pressed = action == glfw.PRESS
        pending.append(("down" if pressed else "up", *point))

    def motion(window, x, y):
        if pressed:
            if position() is None:
                return
            if pending and pending[-1][0] == "move":
                pending.pop()
            pending.append(("move", *position()))

    def scroll(window, xoffset, yoffset):
        nonlocal wheel_steps
        if not pressed and position() is not None and math.isfinite(yoffset):
            wheel_steps = max(-8, min(8, wheel_steps + yoffset))

    def poll():
        nonlocal last_poll
        now = time.monotonic()
        if now - last_poll >= 0.01:
            glfw.poll_events()
            graphics.refresh_display()
            if overlay is not None:
                overlay.pump()
            dialog.pump()
            last_poll = now
        if glfw.window_should_close(graphics.window):
            raise KeyboardInterrupt

    glfw.set_mouse_button_callback(graphics.window, button)
    glfw.set_cursor_pos_callback(graphics.window, motion)
    glfw.set_scroll_callback(graphics.window, scroll)
    report['mouse_wheel_zoom_registered'] = True
    glfw.set_window_title(graphics.window, "Nemo's Reef - Loading")
    try:
        requested_backend = os.environ.get('NEMO_CPU_BACKEND','unicorn')
        if requested_backend not in ('unicorn','dynarmic'):
            raise ValueError('Unknown desktop CPU backend')
        if requested_backend == 'dynarmic':
            from experimental_jit import record_hooks
            record_hooks()
        setup, runtime = run(ROOT / "nemo.apk", "renderer", data=args.data,
                             progress=(lambda count: print(f'Loading game: {count} initializers', flush=True))
                                      if args.hidden or getattr(sys, 'frozen', False) else None,
                             graphics=graphics, retain_runtime=True, server=args.server, host_poll=poll,
                             time_slice_us=10000)
        if setup["status"] != "passed":
            raise RuntimeError(setup["error"])
        report['initialization_phases'] = setup.get('phase_timings',[])
        graphics.check_error('renderer initialization')
        pending.clear()
        inputs = DesktopInput(runtime)
        target = BASE + runtime.elf.symbol("Java_net_livingmobile_sdr_app_SdrRenderer_drawFrame")["address"]
        report["status"] = "running"
        while args.frames is None or report["frames_completed"] < args.frames:
            if (requested_backend == 'dynarmic' and report['frames_completed'] >= 60
                    and getattr(runtime,'_experimental_jit',None) is None):
                # Keep the original startup/JNI path for the first loading
                # frames, then move gameplay to the verified faster backend.
                from experimental_jit import WarmJit
                backend = WarmJit(runtime)
                runtime._experimental_jit = backend
                backend.enable_dispatch()
                backend.enable_native_proxy()
                report['jit_activation_seconds'] = time.perf_counter()-launch_started
            if report['frames_completed'] == 60 and os.environ.get('NEMO_NATIVE_DRAW') == 'deferred':
                bridge = getattr(runtime,'_native_bridge',None)
                if (bridge is not None and not bridge.draw_enabled
                        and getattr(runtime,'_experimental_jit',None) is not None):
                    bridge.set_native_draw_enabled(True)
            frame_started = time.perf_counter()
            cpu_started = time.process_time()
            draws_before = runtime.draws
            calls_before = runtime.gl_events_total
            poll()
            from desktop_message_boxes import deliver_reply
            deliver_reply(inputs)
            box = runtime.message_box
            if box is not None and not box.resolved:
                dialog.sync(box, box.title, box.text,
                            tuple(zip(box.allowed_buttons, box.labels)), box.press)
                pending.clear()
                wheel_steps = 0.0
                pressed = False
                time.sleep(0.01)
                continue
            dialog.sync(None)
            if overlay is not None:
                overlay.sync(runtime.text_input)
                if overlay.finish_requested:
                    inputs.finish_text()
                    overlay.dismiss()
            inputs.flush_text()
            if wheel_steps:
                inputs.zoom(wheel_steps)
                wheel_steps = 0.0
            while pending:
                inputs.touch(*pending.popleft())
            runtime.call_address(target, ENV, 0, instruction_limit=2_000_000_000, timeout_us=600_000_000)
            report["frames_completed"] += 1
            runtime.pump_workers(32)
            if args.hidden and args.frames and report['frames_completed'] == args.frames:
                report['capture'] = graphics.capture(args.output.with_suffix('.png'))
            glfw.swap_buffers(graphics.window)
            graphics.check_error('game frame')
            if report['frames_completed'] == 1:
                glfw.set_window_title(graphics.window, "Nemo's Reef")
            render_seconds = time.perf_counter()-frame_started
            from frame_pacing import wait_for_frame
            wait_for_frame(frame_started)
            elapsed = time.perf_counter() - frame_started
            frame_times.append(elapsed)
            frame_details.append({'frame': report['frames_completed'], 'seconds': elapsed,
                                  'render_seconds': render_seconds,
                                  'cpu_seconds': time.process_time() - cpu_started,
                                  'draws': runtime.draws - draws_before,
                                  'graphics_calls': runtime.gl_events_total - calls_before,
                                  'free_blocks': len(runtime.heap.free_blocks),
                                  'textures_loaded': len(runtime.pvrtc_transfers)})
        report["status"] = "passed"
    except KeyboardInterrupt:
        report["status"] = "closed"
    except Exception as error:
        report.update(status="stopped", error=f"{type(error).__name__}: {error}")
    finally:
        dialog.close()
        if overlay is not None:
            overlay.close()
        if runtime is not None:
            backend = getattr(runtime,'_experimental_jit',None)
            report['cpu_backend'] = {'name':'dynarmic' if backend is not None else 'unicorn',
                                     'interpreter_fallbacks':backend.fallbacks if backend else 0}
            report['memory_fault'] = runtime.last_memory_fault
            report['revival_exchange'] = getattr(runtime, 'revival_exchange', {'active': False})
            report['gameplay_compatibility'] = getattr(runtime, 'gameplay_compatibility', {})
            report['engine_acceleration'] = {
                'integer_flags': runtime.integer_flag_compare['enabled'],
                'native_copies': runtime.copy_acceleration.get('native', False),
                'shared_heap': runtime.heap.backing is not None,
                'direct_vertices': runtime.direct_vertices,
                'direct_vertex_draws': getattr(runtime, 'direct_vertex_draws', 0),
                'direct_client_attributes': getattr(runtime, 'direct_client_attributes', 0)}
            report['timer'] = {'reusable': runtime.reusable_timer,
                               'stops': runtime.slice_timer.stops if runtime.slice_timer else 0}
            if getattr(runtime,'_native_bridge',None) is not None:
                report['compiled_bridge'] = runtime._native_bridge.summary()
            runtime.close_timing()
            from desktop_audio import close_all
            close_all(runtime)
            report['audio'] = [event for event in runtime.platform_logs if event.get('service') == 'audio']
            report['message_boxes'] = [event for event in runtime.platform_logs if event.get('service') == 'message_box']
            report.update(milestones=runtime.milestones, engine_logs=runtime.logs[-200:],
                          png_interlace=runtime.png_interlace_stats,
                          png_combine=runtime.png_combine_stats,
                          boundary_preemptions=runtime.boundary_preemptions,
                          copy_small_limit=runtime.copy_acceleration.get('small_limit', 0),
                          direct_vbos=runtime.direct_vbos,
                          pvrtc_transfers=runtime.pvrtc_transfers,
                          workers=runtime.worker_summary(), imports_called=dict(runtime.import_counts))
            if runtime.network is not None:
                report["network"] = {"server": runtime.network.origin, "events": runtime.network.events}
                runtime.network.close_all()
            runtime.files.close_all()
            report["pc"] = hex(runtime.machine.reg_read(arm.UC_ARM_REG_PC))
        report['display'] = {'fullscreen': graphics.fullscreen, 'logical_size': [graphics.width, graphics.height],
                             'framebuffer_size': list(glfw.get_framebuffer_size(graphics.window))}
        graphics.close()
        report['elapsed_seconds'] = time.perf_counter() - launch_started
        report['recent_frame_seconds'] = list(frame_times)
        report['recent_frame_details'] = list(frame_details)
        write_json(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:18081")
    parser.add_argument("--data", type=Path, default=HERE / "work" / "runtime-data")
    parser.add_argument("--output", type=Path, default=HERE / "work" / "interactive-session.json")
    parser.add_argument("--fullscreen", action="store_true", help="Start fullscreen; F11 or Alt+Enter toggles")
    parser.add_argument("--hidden", action="store_true", help="Hide the native window for startup checks")
    parser.add_argument("--frames", type=int, help="Stop after a bounded number of callbacks; 0 checks renderer setup")
    args = parser.parse_args()
    if args.frames is not None and args.frames < 0:
        parser.error("--frames cannot be negative")
    report = launch(args)
    print(f"{report['status'].upper()}: {report['frames_completed']} original frames; report: {args.output}")
    if report["status"] == "stopped":
        print(report["error"])
        raise SystemExit(2)


if __name__ == "__main__":
    main()
