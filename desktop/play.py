"""Launch the original client and its private local server together."""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time

from audit_apk import ROOT
from desktop_paths import desktop_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--hidden', action='store_true')
    parser.add_argument('--frames', type=int)
    args = parser.parse_args()
    if not (ROOT / 'nemo.apk').is_file():
        raise SystemExit('Place your original game APK in the project folder as nemo.apk.')
    from run_client import launch
    storage = desktop_directory() / 'work' / 'play'
    storage.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    with (storage / 'server.log').open('a', encoding='utf-8') as log:
        command = ([sys.executable, '--server-child'] if getattr(sys, 'frozen', False) else
                   [sys.executable, '-u', str(ROOT / 'server' / 'nemo_server.py')])
        server = subprocess.Popen([*command, '--apk', str(ROOT / 'nemo.apk'), '--host', '127.0.0.1',
                                   '--port', str(port), '--data', str(storage / 'server-data')],
                                  cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f'The local server could not start; see {storage / "server.log"}.')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('The local server did not become ready.')
                    time.sleep(0.1)
            args.server = f'http://127.0.0.1:{port}'
            args.data = storage / 'client-data'
            args.output = storage / 'client-session.json'
            print('Starting Nemo\'s Reef. Initial loading can take several minutes.', flush=True)
            result = launch(args)
            if result['status'] == 'stopped':
                error = RuntimeError(f'{result["error"]}\nDetails: {args.output}')
                error.game_started = result['frames_completed'] > 0
                raise error
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
