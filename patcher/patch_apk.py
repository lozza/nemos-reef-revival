#!/usr/bin/env python3
"""Points the Nemo's Reef 1.8.1 APK at a private server.

    python patch_apk.py nemos-reef-1-8-1-en-android.apk http://192.168.1.20:8080 [--debug-log]

Rewrites the two server URLs inside lib/armeabi/libsdr.so (the server URL can be at most 29
characters, since the account API URL <server>/nemo/api/ must fit the original's 39), optionally re-enables the engine's debug log (visible with `adb logcat`), then
re-signs the APK with uber-apk-signer (https://github.com/patrickfav/uber-apk-signer) if its jar is
next to this script or given with --signer, or with `apksigner` from the Android SDK.
"""
import argparse
import glob
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib

import pvrtc

LIB = 'lib/armeabi/libsdr.so'
DEX = 'classes.dex'
ACCOUNT_URL = b'http://nemo.apps.tapulous.com/nemo/api/'
GAME_URL = b'http://nemo.pragueweb.tapulous.com/nemo/'

# sdr::Debug::log overloads are compiled to `bx lr` in the release build; branch them to the
# still-working sdr::Debug::print overloads with the same leading arguments (file offset == vaddr).
DEBUG_PATCHES = [
    (0xa4fe3c, 0xa500fc),  # log(String const&)                  -> print(String const&)
    (0xa4fe40, 0xa50020),  # log(int, String const&, Severity)    -> print(int, String const&)
    (0xa4fe8c, 0xa500fc),  # exceptionThrown(String const&)       -> print(String const&): every
                           # sdr::BaseException message (e.g. "Key 'x' not found") reaches logcat
]

# With the debug log on, also log the name of every sprite and texture the engine loads (to find
# graphics that fail to load). Each hooked function starts with `push {r4-r7, lr}; mov r7, fp`; that is
# replaced by a b.w into a stub in the zero padding after .rodata, which calls Debug::print(name) and
# then runs the two displaced instructions and branches back.
TRACE_HOOKS = [0x9c1bfc,   # sdr::Sprite::load(String const&)
               0x9c3960]   # sdr::Texture::load(String const&)
TRACE_PROLOGUE = bytes.fromhex('f0b55f46')
TRACE_PRINT = 0xa500fc     # sdr::Debug::print(String const&)
TRACE_CAVE = 0xc0e940      # zero padding past the end of the first LOAD segment (0xc0e934)


# sdr::RendererES1::RendererES1 asks glGetIntegerv(GL_MAX_PALETTE_MATRICES_OES) and sizes a vector with
# the answer; the Android emulator's GLES 1 layer returns garbage there and the game aborts with
# "vector::reserve". Replace `blx glGetIntegerv` (r1 = &result) with `movs r0, #32; str r0, [r1]`.
EMULATOR_PATCH = (0xa036ee, bytes.fromhex('44f164ec'), bytes.fromhex('20200860'))


# DialogSettings::DialogSettings shows part of the game server's host name: substring(11, indexOf('.') - 11),
# which assumes the host starts with "nemo". With an IP address the first '.' comes earlier, the length
# is negative and opening Menu > Settings crashes. Replace `subs r3, #0xb` with `movs r3, #0` (empty text,
# as the original "http://nemo.pragueweb..." gave).
SETTINGS_PATCH = (0x5308b8, bytes.fromhex('0b3b'), bytes.fromhex('0023'))
# EntityFish::EntityFish sets the LOD-fade flag (Entity+0xf0) on the small ambient fish (Beaked, Firefish,
# Ordinary, Ambient), so Entity::updateLodAlpha fades them out unless the camera is zoomed right in. Replace
# `strb r3, [r4, r2]` with a nop: they stay visible at every zoom.
AMBIENT_FADE_PATCH = (0x2a5384, bytes.fromhex('a354'), bytes.fromhex('c046'))

# DialogFlowExchange checks pearls regardless of the catalogue's currency.
# Revival exchanges spend sand dollars. Change only this flow's balance getter
# and its two insufficient-funds dialog references, leaving pearl shops intact.
EXCHANGE_BALANCE_AT = 0x4697bc
EXCHANGE_DIALOG_PATCHES = (
    (0x469864, bytes.fromhex('be597c00'), bytes.fromhex('b6597c00')),
    (0x46979c, bytes.fromhex('705a7c00'), bytes.fromhex('685a7c00')),
)


# SdrRoot.init(SdrActivity, int, int) stores data under /sdcard/Android/data/<pkg>/files, which apps this
# old may no longer create on Android 11+ (logins and caches are then never saved). Turning the
# `move-result-object v3` after ExternalStorage.getAppDataPath() into a nop keeps v3 = the app's
# internal dataDir while still running the mkdirs for .cache/.cache/install.
DEX_INTERNAL_STORAGE_PATCH = (0x33a9c8, bytes.fromhex('0c03'), bytes.fromhex('0000'))


def patch_dex(dex):
    dex = bytearray(dex)
    at, old, new = DEX_INTERNAL_STORAGE_PATCH
    if dex[at:at + len(old)] != old:
        sys.exit('unexpected bytes in %s at %#x; is this the 1.8.1 APK?' % (DEX, at))
    dex[at:at + len(new)] = new
    dex[12:32] = hashlib.sha1(dex[32:]).digest()             # header signature
    dex[8:12] = struct.pack('<I', zlib.adler32(dex[12:]))    # header checksum
    return bytes(dex)


def thumb2_b_w(src, dst):
    """Encodes a Thumb-2 `b.w dst` placed at src (4 bytes, little-endian halfwords)."""
    off = dst - (src + 4)
    if not -(1 << 24) <= off < (1 << 24) or off & 1:
        raise ValueError('branch out of range')
    s = (off >> 24) & 1
    i1, i2 = (off >> 23) & 1, (off >> 22) & 1
    imm10, imm11 = (off >> 12) & 0x3ff, (off >> 1) & 0x7ff
    j1, j2 = (1 - (i1 ^ s)), (1 - (i2 ^ s))
    hw1 = 0xf000 | (s << 10) | imm10
    hw2 = 0x9000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack('<HH', hw1, hw2)


def thumb2_bl(src, dst):
    """Encodes a Thumb-2 `bl dst` placed at src."""
    hw1, hw2 = struct.unpack('<HH', thumb2_b_w(src, dst))
    return struct.pack('<HH', hw1, hw2 | 0x4000)


def trace_hooks(lib):
    at = TRACE_CAVE
    if any(lib[at:at + 32 * len(TRACE_HOOKS)]):
        sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % at)
    for fn in TRACE_HOOKS:
        if lib[fn:fn + 4] != TRACE_PROLOGUE:
            sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % fn)
        stub = (bytes.fromhex('1fb5'          # push {r0-r4, lr}
                              '0846')         # mov r0, r1 (the name)
                + thumb2_bl(at + 4, TRACE_PRINT)
                + bytes.fromhex('bde81f40')   # pop.w {r0-r4, lr}
                + TRACE_PROLOGUE
                + thumb2_b_w(at + 16, fn + 4))
        lib[at:at + len(stub)] = stub
        lib[fn:fn + 4] = thumb2_b_w(fn, at)
        at += 32
    # grow the first LOAD segment (program header 1: p_filesz, p_memsz) over the stubs
    ph = struct.unpack_from('<I', lib, 28)[0] + 32
    filesz, memsz = struct.unpack_from('<II', lib, ph + 16)
    if filesz != memsz or filesz > TRACE_CAVE:
        sys.exit('unexpected program headers in %s' % LIB)
    struct.pack_into('<II', lib, ph + 16, at, at)


def replace_cstring(lib, old, new):
    if len(new) > len(old):
        sys.exit('URL %r is longer than the original %r (%d > %d characters)'
                 % (new.decode(), old.decode(), len(new), len(old)))
    i = lib.find(old + b'\0')
    if i < 0:
        sys.exit('could not find %r in %s (already patched?)' % (old.decode(), LIB))
    lib[i:i + len(old)] = new.ljust(len(old), b'\0')


def patch_lib(lib, account_url, game_url, debug_log, emulator):
    lib = bytearray(lib)
    replace_cstring(lib, ACCOUNT_URL, account_url.encode())
    replace_cstring(lib, GAME_URL, game_url.encode())
    at = EXCHANGE_BALANCE_AT
    if lib[at:at + 4] != thumb2_bl(at, 0x234738):
        sys.exit('unexpected exchange balance check; is this the 1.8.1 APK?')
    lib[at:at + 4] = thumb2_bl(at, 0x23473c)
    for at, old, new in EXCHANGE_DIALOG_PATCHES:
        if lib[at:at + 4] != old:
            sys.exit('unexpected exchange dialog at %#x' % at)
        lib[at:at + 4] = new
    if debug_log:
        for src, dst in DEBUG_PATCHES:
            if lib[src:src + 2] != b'\x70\x47':  # bx lr
                sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % src)
            lib[src:src + 4] = thumb2_b_w(src, dst)
        trace_hooks(lib)
    for at, old, new in (SETTINGS_PATCH, AMBIENT_FADE_PATCH):
        if lib[at:at + 2] != old:
            sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % at)
        lib[at:at + 2] = new
    if emulator:
        at, old, new = EMULATOR_PATCH
        if lib[at:at + 4] != old:
            sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % at)
        lib[at:at + 4] = new
    return bytes(lib)


def png_textures(zin):
    """Decodes every PVRTC-only .ddspvr texture to a .png next to it (see pvrtc.py)."""
    names = set(zin.namelist())
    todo = [n for n in sorted(names) if n.endswith('.ddspvr') and n[:-len('.ddspvr')] + '.png' not in names]
    out = {}
    for i, name in enumerate(todo, 1):
        w, h, rgba, alpha = pvrtc.ddspvr_to_rgba(zin.read(name))
        out[name[:-len('.ddspvr')] + '.png'] = pvrtc.png(w, h, rgba, alpha)
        print('\rconverting textures for non-PowerVR GPUs: %d/%d' % (i, len(todo)), end='', flush=True)
    if todo:
        print()
    return out


def music_files(music_dir):
    """music0..music3 (.ogg, or .mp3 such as the iOS version's tracks) from music_dir. The APK this was built
    for ships no music, and SdrMediaPlayer opens assets/music/musicN.ogg with openFd, which needs them stored
    uncompressed. Android's MediaPlayer goes by the file's contents, not its name, so MP3s are stored under
    the .ogg names unchanged."""
    out = {}
    if music_dir:
        for i in range(4):
            for ext in ('ogg', 'mp3'):
                path = os.path.join(music_dir, 'music%d.%s' % (i, ext))
                if os.path.exists(path):
                    with open(path, 'rb') as f:
                        out['assets/music/music%d.ogg' % i] = f.read()
                    break
        print('adding %d music track(s) from %s' % (len(out), music_dir))
    return out


def rebuild(apk_in, apk_out, lib, dex, music=None):
    with zipfile.ZipFile(apk_in) as zin, zipfile.ZipFile(apk_out, 'w') as zout:
        names = set(zin.namelist())
        for name, data in (music or {}).items():
            if name not in names:
                zout.writestr(zipfile.ZipInfo(name, date_time=(2015, 8, 28, 20, 56, 0)), data, compress_type=zipfile.ZIP_STORED)
        for name, data in png_textures(zin).items():
            zout.writestr(zipfile.ZipInfo(name, date_time=(2015, 8, 28, 20, 56, 0)), data, compress_type=zipfile.ZIP_STORED)
        for info in zin.infolist():
            if info.filename.startswith('META-INF/') and info.filename.rsplit('.', 1)[-1] in ('SF', 'RSA', 'DSA', 'MF', 'EC'):
                continue  # old signature
            data = {LIB: lib, DEX: dex}.get(info.filename) or zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)


def sign(apk, signer_jar):
    if signer_jar:
        out = tempfile.mkdtemp()
        subprocess.check_call(['java', '-jar', signer_jar, '-a', apk, '-o', out, '--allowResign'])
        signed = glob.glob(os.path.join(out, '*.apk'))[0]
        shutil.move(signed, apk)
        shutil.rmtree(out, ignore_errors=True)
        return True
    if shutil.which('apksigner'):
        ks = os.path.join(os.path.dirname(os.path.abspath(apk)), 'nemo-debug.keystore')
        if not os.path.exists(ks):
            subprocess.check_call(['keytool', '-genkeypair', '-keystore', ks, '-storepass', 'android',
                                   '-alias', 'nemo', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000',
                                   '-dname', 'CN=Nemo Private Server'])
        subprocess.check_call(['apksigner', 'sign', '--ks', ks, '--ks-pass', 'pass:android', apk])
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apk', help='original nemos-reef 1.8.1 APK')
    ap.add_argument('server', help='server base URL, e.g. http://192.168.1.20:8080')
    ap.add_argument('-o', '--output', help='output APK (default: <apk>-private.apk)')
    ap.add_argument('--debug-log', action='store_true', help="send the game's internal log to logcat")
    ap.add_argument('--emulator', action='store_true',
                    help='work around the Android emulator GL driver (only needed when testing in an emulator)')
    ap.add_argument('--music', metavar='DIR',
                    help='folder with music0..music3 (.ogg or .mp3; the game ships without music)')
    ap.add_argument('--signer', help='path to uber-apk-signer.jar')
    args = ap.parse_args()

    base = args.server.rstrip('/')
    # The route advertises the native sand-dollar exchange fix to the server.
    account_url, game_url = base + '/nemo/api/', base + '/nemo/e1/'
    out = args.output or os.path.splitext(args.apk)[0] + '-private.apk'
    signer = args.signer or next(iter(sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uber-apk-signer*.jar')))), None)

    with zipfile.ZipFile(args.apk) as z:
        lib = patch_lib(z.read(LIB), account_url, game_url, args.debug_log, args.emulator)
        dex = patch_dex(z.read(DEX))
    rebuild(args.apk, out, lib, dex, music_files(args.music))
    print('account API -> %s\ngame API    -> %s' % (account_url, game_url))
    if sign(out, signer):
        print('wrote signed %s' % out)
    else:
        print('wrote UNSIGNED %s: put uber-apk-signer.jar next to this script (or pass --signer), '
              'or install the Android SDK build-tools (apksigner), then run again' % out)


if __name__ == '__main__':
    main()
