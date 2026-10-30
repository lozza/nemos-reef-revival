"""Build a Linux x86_64 portable folder on a Linux host; no game assets included."""
import base64
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    if sys.platform != 'linux' or platform.machine() not in ('x86_64','AMD64'):
        raise SystemExit('Build on Linux x86_64.')
    from PyInstaller.__main__ import run
    matrix = HERE/'matrix_shim.bin'
    if not matrix.exists():
        matrix.write_bytes(base64.b64decode((HERE/'linux-matrix-shim.b64').read_text()))
    options = ['--onedir','--name','NemosReef','--noconfirm','--distpath',str(HERE/'linux-dist'),
        '--workpath',str(HERE/'linux-build'), '--specpath',str(HERE/'linux-build'),
        '--paths',str(HERE),'--paths',str(ROOT/'server'),
        '--add-data',f'{matrix}:.','--log-level','WARN','--hidden-import','tkinter',
        '--hidden-import','tkinter.ttk']
    for library in ('libnemo_bridge.so','libjit_core.so','libjit_dispatch_v7.so'):
        path = HERE/'linux/lib'/library
        if not path.is_file():
            raise RuntimeError(f'Missing native library: {path}')
        options += ['--add-binary',f'{path}:native']
    for package in ('unicorn','glfw','texture2ddecoder','miniaudio'):
        options += ['--collect-all',package]
    for resource in (ROOT/'server').glob('*.json'):
        options += ['--add-data',f'{resource}:.']
    options += [str(HERE/'portable.py')]
    run(options)
    bundle = HERE/'linux-dist/NemosReef'
    # Use the desktop's C++ runtime alongside its own graphics driver. An
    # older bundled libstdc++ can prevent newer system DRI drivers loading.
    for name in ('libstdc++.so.6','libgcc_s.so.1'):
        (bundle/'_internal'/name).unlink(missing_ok=True)
    shutil.copy2(HERE/'LINUX-TEST.md',bundle/'START HERE.txt')
    shutil.copy2(HERE/'linux-launch.sh',bundle/'Play Nemos Reef.sh')
    (bundle/'Play Nemos Reef.sh').chmod(0o755)
    (bundle/'music').mkdir(exist_ok=True)
    notices = bundle/'licenses'
    notices.mkdir(exist_ok=True)
    source = ROOT/'build/dynarmic'
    for label,path in {'Dynarmic':'LICENSE.txt','mcl':'externals/mcl/LICENSE',
        'fmt':'externals/fmt/LICENSE.rst','robin-map':'externals/robin-map/LICENSE',
        'Zydis':'externals/zydis/LICENSE','Zycore':'externals/zycore/LICENSE',
        'Xbyak':'externals/xbyak/COPYRIGHT'}.items():
        shutil.copy2(source/path,notices/(label+'.txt'))
    boost_license = Path('/usr/share/doc/libboost1.74-dev/copyright')
    if boost_license.exists():
        shutil.copy2(boost_license,notices/'Boost.txt')
    libraries = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (bundle/'_internal/native').glob('*.so')}
    (bundle/'linux-build-info.json').write_text(json.dumps({'platform':platform.platform(),
        'python':sys.version,'native_libraries':libraries,'game_assets_included':False,
        'uses_system_cpp_runtime':True,
        'scope':'Linux x86_64 experimental test; original-game gameplay not verified in CI'},indent=2))
    subprocess.run(['tar','-C',str(bundle.parent),'-czf',str(HERE/'NemosReef-Linux-x86_64-runtime.tar.gz'),bundle.name],check=True)


if __name__ == '__main__':
    main()

