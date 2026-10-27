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
# The game ships its ARM code as "armeabi" (ARMv5), which newer devices and emulators such as BlueStacks no longer
# list among their ABIs, so they refuse to install it ("no matching ABIs"). ARMv7 runs the same code, and every
# 32-bit ARM device or translator accepts armeabi-v7a, so the libraries move there.
OLD_ABI, NEW_ABI = 'lib/armeabi/', 'lib/armeabi-v7a/'
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
# RendererES1::setTextureMinFilter maps the engine's filter modes through this GLenum table (NEAREST, LINEAR,
# then the four *_MIPMAP_* modes). Emulators' GLES 1 layers (LDPlayer) do not build the mipmaps
# (GL_GENERATE_MIPMAP), so those textures are incomplete and draw black; use NEAREST/LINEAR instead.
EMULATOR_MIN_FILTERS = (0xc30118, bytes.fromhex('00270000012700000227000003270000'),
                        bytes.fromhex('00260000012600000026000001260000'))
# RendererGLTextureFixed::setSamplerStates: presets 2-5 blend a second texture in on unit 1 (the reef floor
# uses preset 5), which the emulator samples as black. Set unit 1 to mode 6 (pass the colour through) instead:
# `movs r2, #N` -> `movs r2, #6` before each setTextureEnvMode(1, N) call.
EMULATOR_SAMPLER_PRESETS = [(at, bytes([n, 0x22]), b'\x06\x22')
                            for at, n in ((0xa027aa, 5), (0xa027d8, 2), (0xa027ee, 3), (0xa02804, 4))]
# sdr::Geometry (only the reef floor's tile map uses it) binds its vertex buffer through a recorded renderer
# state object when it has one; on the emulator the floor then draws untextured (white). Always take the
# plain VertexBuffer/IndexBuffer path instead: the branches in bind, unbind and draw.
EMULATOR_GEOMETRY = [(0x9ab8e0, b'\x0b\xd1', b'\xc0\x46'),   # bind: bne state-object path -> nop
                     (0x9ab91c, b'\x0b\xd1', b'\xc0\x46'),   # unbind: same
                     (0x9ab954, b'\x0b\xd0', b'\x0b\xe0')]   # draw: beq plain path -> b


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


MANIFEST = 'AndroidManifest.xml'
# Android 14+ refuses to install apps whose targetSdkVersion is below 23 ("App not installed"); the game targets
# 22. Raising it means the runtime-permission model: WRITE_EXTERNAL_STORAGE and GET_ACCOUNTS are no longer granted
# at install. The patched game keeps its data in internal storage (DEX_INTERNAL_STORAGE_PATCH) and the Apache
# HTTP library the bundled SDKs use is still provided to apps targeting below 28.
MODERN_TARGET_SDK = 23


def patch_target_sdk(manifest, target=MODERN_TARGET_SDK):
    """Sets <uses-sdk android:targetSdkVersion> in the binary manifest (AXML) to at least `target`."""
    m = bytearray(manifest)
    u16 = lambda o: struct.unpack_from('<H', m, o)[0]
    u32 = lambda o: struct.unpack_from('<I', m, o)[0]
    count, flags, start = u32(16), u32(24), u32(28)
    def string(i):
        off = 8 + start + u32(36 + 4 * i)
        if flags & 0x100:  # UTF-8 pool: char count, byte count, bytes
            off += 1 if m[off] < 0x80 else 2
            n = m[off]
            off += 1 if n < 0x80 else 2
            return bytes(m[off:off + n]).decode('utf-8', 'replace')
        return bytes(m[off + 2:off + 2 + 2 * u16(off)]).decode('utf-16le')
    o = 8 + u32(12)
    while o < len(m):
        kind, size = u16(o), u32(o + 4)
        if kind == 0x0102 and string(u32(o + 20)) == 'uses-sdk':  # start of an element
            first = o + 16 + u16(o + 24)
            for a in range(u16(o + 28)):
                at = first + 20 * a
                if string(u32(at + 4)) == 'targetSdkVersion' and m[at + 15] == 0x10 and u32(at + 16) < target:
                    struct.pack_into('<I', m, at + 16, target)
                    return bytes(m)
            return manifest
        o += size
    return manifest


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
        for at, old, new in [EMULATOR_PATCH, EMULATOR_MIN_FILTERS] + EMULATOR_SAMPLER_PRESETS + EMULATOR_GEOMETRY:
            if lib[at:at + len(old)] != old:
                sys.exit('unexpected bytes at %#x; is this the 1.8.1 APK?' % at)
            lib[at:at + len(old)] = new
    return bytes(lib)


def png_textures(zin, emulator=False):
    """Decodes every PVRTC-only .ddspvr texture to a .png next to it (see pvrtc.py). With emulator, also
    returns rewritten two-texture materials (see bake_two_texture_materials)."""
    names = set(zin.namelist())
    todo = [n for n in sorted(names) if n.endswith('.ddspvr') and n[:-len('.ddspvr')] + '.png' not in names]
    decoded = {}
    for i, name in enumerate(todo, 1):
        decoded[name[:-len('.ddspvr')] + '.png'] = pvrtc.ddspvr_to_rgba(zin.read(name))
        print('\rconverting textures for non-PowerVR GPUs: %d/%d' % (i, len(todo)), end='', flush=True)
    if todo:
        print()
    mats = bake_two_texture_materials(zin, decoded) if emulator else {}
    return {name: pvrtc.png(*tex) for name, tex in decoded.items()}, mats


def read_mat(blob):
    """A .mat file: u16 property count, that many (id, value) byte pairs, u16 texture count, then per texture
    a u16 length, the file name and a u16 (always 0). Big-endian. Returns (header bytes, texture names)."""
    at = 2 + 2 * struct.unpack_from('>H', blob)[0]
    head, count = blob[:at], struct.unpack_from('>H', blob, at)[0]
    at += 2
    textures = []
    for _ in range(count):
        n = struct.unpack_from('>H', blob, at)[0]
        textures.append(blob[at + 2:at + 2 + n].decode())
        at += 4 + n
    return head, textures


def bake_two_texture_materials(zin, decoded):
    """Fish such as the bicolor or emperor angelfish are drawn with two textures: X_d.png (a white cut-out
    mask) on texture unit 0 and X_lm.png (the colours) multiplied in on unit 1. Emulators' GLES 1 layers
    (LDPlayer) sample unit 1 as black, so those fish come out as black silhouettes. Bake the mask into the
    _lm texture (colour = lm * d, alpha = d) and make the material use just that one texture."""
    mats, baked = {}, set()
    for name in sorted(n for n in zin.namelist() if n.startswith('assets/') and n.endswith('.mat')):
        head, textures = read_mat(zin.read(name))
        if len(textures) != 2:
            continue
        for folder in sorted({n.rsplit('/', 1)[0] for n in decoded}):
            d, lm = decoded.get(folder + '/' + textures[0]), decoded.get(folder + '/' + textures[1])
            if not d or not lm or folder + '/' + textures[1] in baked:
                continue
            baked.add(folder + '/' + textures[1])
            (dw, dh, drgba, _), (w, h, lrgba, _) = d, lm
            out = bytearray(lrgba)
            for y in range(h):
                for x in range(w):
                    i, j = 4 * (y * w + x), 4 * ((y * dh // h) * dw + x * dw // w)
                    for c in range(3):
                        out[i + c] = lrgba[i + c] * drgba[j + c] // 255
                    out[i + 3] = drgba[j + 3]
            decoded[folder + '/' + textures[1]] = (w, h, bytes(out), True)
        lm = textures[1].encode()
        mats[name] = head + struct.pack('>HH', 1, len(lm)) + lm + b'\0\0'
    return mats


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


def rebuild(apk_in, apk_out, lib, dex, music=None, manifest=None, emulator=False):
    with zipfile.ZipFile(apk_in) as zin, zipfile.ZipFile(apk_out, 'w') as zout:
        names = set(zin.namelist())
        for name, data in (music or {}).items():
            if name not in names:
                zout.writestr(zipfile.ZipInfo(name, date_time=(2015, 8, 28, 20, 56, 0)), data, compress_type=zipfile.ZIP_STORED)
        pngs, mats = png_textures(zin, emulator)
        for name, data in pngs.items():
            zout.writestr(zipfile.ZipInfo(name, date_time=(2015, 8, 28, 20, 56, 0)), data, compress_type=zipfile.ZIP_STORED)
        for info in zin.infolist():
            if info.filename.startswith('META-INF/') and info.filename.rsplit('.', 1)[-1] in ('SF', 'RSA', 'DSA', 'MF', 'EC'):
                continue  # old signature
            data = {LIB: lib, DEX: dex, MANIFEST: manifest, **mats}.get(info.filename) or zin.read(info.filename)
            if info.filename.startswith(OLD_ABI):
                info = copy_info(info, NEW_ABI + info.filename[len(OLD_ABI):])
            zout.writestr(info, data, compress_type=info.compress_type)


def copy_info(info, name):
    out = zipfile.ZipInfo(name, date_time=info.date_time)
    out.compress_type, out.external_attr = info.compress_type, info.external_attr
    return out


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
    ap.add_argument('--modern-android', action='store_true',
                    help='target Android 6.0 so Android 14 and newer will install it (see MODERN_TARGET_SDK)')
    args = ap.parse_args()

    base = args.server.rstrip('/')
    # The route advertises the native sand-dollar exchange fix to the server.
    account_url, game_url = base + '/nemo/api/', base + '/nemo/e1/'
    out = args.output or os.path.splitext(args.apk)[0] + '-private.apk'
    signer = args.signer or next(iter(sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uber-apk-signer*.jar')))), None)

    with zipfile.ZipFile(args.apk) as z:
        lib = patch_lib(z.read(LIB), account_url, game_url, args.debug_log, args.emulator)
        dex = patch_dex(z.read(DEX))
    with zipfile.ZipFile(args.apk) as z:
        manifest = patch_target_sdk(z.read(MANIFEST)) if args.modern_android else None
    rebuild(args.apk, out, lib, dex, music_files(args.music), manifest, args.emulator)
    print('account API -> %s\ngame API    -> %s' % (account_url, game_url))
    if sign(out, signer):
        print('wrote signed %s' % out)
    else:  # Android refuses to install an unsigned APK ("package appears to be invalid")
        os.remove(out)
        sys.exit('could not sign the APK, so nothing was written: put uber-apk-signer.jar in the patcher folder '
                 '(or pass --signer) and make sure Java is installed, or install the Android SDK build-tools '
                 '(apksigner), then run again')


if __name__ == '__main__':
    main()
