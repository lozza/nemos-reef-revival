"""Native exchange patch regression tests using schematic bytes only."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'patcher'))
import patch_apk as patcher


def branch_destination(data, source):
    first, second = struct.unpack('<HH', data)
    sign = (first >> 10) & 1
    i1 = 1 ^ ((second >> 13) & 1) ^ sign
    i2 = 1 ^ ((second >> 11) & 1) ^ sign
    offset = ((sign << 24) | (i1 << 23) | (i2 << 22)
              | ((first & 1023) << 12) | ((second & 2047) << 1))
    if sign:
        offset -= 1 << 25
    return source + 4 + offset


class ExchangePatcherTest(unittest.TestCase):
    def fixture(self):
        data = bytearray(0x540000)
        data[0x100:0x100 + len(patcher.ACCOUNT_URL) + 1] = patcher.ACCOUNT_URL + b'\0'
        data[0x200:0x200 + len(patcher.GAME_URL) + 1] = patcher.GAME_URL + b'\0'
        for at, old, _ in (patcher.SETTINGS_PATCH, patcher.AMBIENT_FADE_PATCH,
                           *patcher.EXCHANGE_DIALOG_PATCHES):
            data[at:at + len(old)] = old
        data[0x4697bc:0x4697c0] = bytes.fromhex('caf5bcff')
        return bytes(data)

    def test_exchange_uses_sand_dollar_getter_and_only_changes_expected_bytes(self):
        original = self.fixture()
        account = 'http://127.0.0.1:18080/nemo/api/'
        game = 'http://127.0.0.1:18080/nemo/e1/'
        result = patcher.patch_lib(original, account, game, False, False)
        self.assertEqual(branch_destination(result[0x4697bc:0x4697c0], 0x4697bc), 0x23473c)
        self.assertEqual(struct.unpack_from('<I', result, 0x469864)[0], 0x7c59b6)
        self.assertEqual(struct.unpack_from('<I', result, 0x46979c)[0], 0x7c5a68)
        # Restore the deliberately patched ranges; everything else must match.
        restored = bytearray(result)
        ranges = [(0x100, len(patcher.ACCOUNT_URL)), (0x200, len(patcher.GAME_URL)),
                  (0x4697bc, 4), (0x469864, 4), (0x46979c, 4),
                  (patcher.SETTINGS_PATCH[0], 2), (patcher.AMBIENT_FADE_PATCH[0], 2)]
        for at, length in ranges:
            restored[at:at + length] = original[at:at + length]
        self.assertEqual(restored, original)

    def test_capability_route_still_fits_maximum_supported_server_address(self):
        base = 'http://' + 'a' * 22
        self.assertEqual(len(base), 29)
        result = patcher.patch_lib(self.fixture(), base + '/nemo/api/', base + '/nemo/e1/', False, False)
        self.assertIn((base + '/nemo/e1/').encode() + b'\0', result)

    def test_unrecognised_exchange_code_is_rejected(self):
        data = bytearray(self.fixture())
        data[0x4697bc:0x4697c0] = b'\0' * 4
        with self.assertRaises(SystemExit):
            patcher.patch_lib(bytes(data), 'http://test/nemo/api/', 'http://test/nemo/e1/', False, False)


if __name__ == '__main__':
    unittest.main()
