"""Read version attributes from the APK's compiled Android manifest."""
import struct
import zipfile


def version(apk):
    with zipfile.ZipFile(apk) as archive:
        data = archive.read('AndroidManifest.xml')
    if struct.unpack_from('<H', data)[0] != 3:
        raise ValueError('expected compiled Android manifest')
    strings = []
    at = 8
    while at < len(data):
        kind, header, size = struct.unpack_from('<HHI', data, at)
        if size < header or at + size > len(data):
            raise ValueError('invalid manifest chunk')
        if kind == 1:
            count, _, flags, start = struct.unpack_from('<IIII', data, at + 8)
            for n in range(count):
                offset = at + start + struct.unpack_from('<I', data, at + header + n*4)[0]
                if flags & 256:
                    def length(p):
                        value = data[p]
                        return (((value & 127) << 8) | data[p+1], p+2) if value & 128 else (value, p+1)
                    _, offset = length(offset)
                    length_bytes, offset = length(offset)
                    strings.append(data[offset:offset+length_bytes].decode('utf-8'))
                else:
                    length_units, = struct.unpack_from('<H', data, offset)
                    offset += 2
                    if length_units & 32768:
                        length_units = ((length_units & 32767) << 16) | struct.unpack_from('<H', data, offset)[0]
                        offset += 2
                    strings.append(data[offset:offset+length_units*2].decode('utf-16-le'))
        elif kind == 0x102:
            _, name, start, stride, count = struct.unpack_from('<IIHHH', data, at + 16)
            if strings[name] == 'manifest':
                values = {}
                for n in range(count):
                    _, name, raw, _, _, value_type, value = struct.unpack_from('<IIIHBBI', data, at+16+start+n*stride)
                    if strings[name] in ('versionName', 'versionCode'):
                        values[strings[name]] = strings[value] if value_type == 3 else str(value)
                return values['versionName'], values['versionCode']
        at += size
    raise ValueError('manifest version attributes not found')
