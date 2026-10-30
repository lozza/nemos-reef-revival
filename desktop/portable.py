"""Entry point for the self-contained desktop executable and its private server."""
from pathlib import Path
import os
import sys
import traceback

from desktop_paths import desktop_directory, project_root


def main():
    child = '--server-child' in sys.argv
    log_directory = desktop_directory() / 'work'
    log_directory.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        stream = (log_directory / ('server-launch.log' if child else 'desktop-launch.log')).open(
            'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = stream
    if child:
        sys.argv.remove('--server-child')
        sys.path.insert(0, str(project_root() / 'server'))
        try:
            import nemo_server
            nemo_server.main()
        except BaseException:
            traceback.print_exc()
            raise SystemExit(1)
        return
    try:
        if sys.platform == 'win32' or sys.platform.startswith('linux'):
            os.environ.setdefault('NEMO_CPU_BACKEND','dynarmic')
            os.environ.setdefault('NEMO_NATIVE_DRAW','deferred')
        if sys.platform.startswith('linux'):
            os.environ.setdefault('NEMO_COMPILED_BRIDGE','1')
            os.environ.setdefault('NEMO_SHARED_HEAP','1')
        if '--self-test' in sys.argv:
            from platform_self_test import main as self_test
            self_test()
            return
        from play import main as play
        play()
    except KeyboardInterrupt:
        return
    except BaseException as error:
        if isinstance(error, SystemExit) and not error.code:
            return
        message = str(error)
        report = log_directory / 'launch-error.txt'
        report.write_text(traceback.format_exc(), encoding='utf-8')
        if getattr(sys, 'frozen', False) and '--hidden' in sys.argv:
            raise SystemExit(1)
        if getattr(sys, 'frozen', False) and sys.platform == 'win32':
            import ctypes
            heading = ("Nemo's Reef stopped unexpectedly." if getattr(error, 'game_started', False)
                       else "Nemo's Reef could not start.")
            ctypes.windll.user32.MessageBoxW(None,
                f"{heading}\n\n{message}\n\nDetails: {report}", "Nemo's Reef", 0x10)
            raise SystemExit(1)
        else:
            raise


if __name__ == '__main__':
    main()
