"""Check the generated UI keeps atlas-specific sprites and original file ownership."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import economy_ui as ui


class FixtureAPK:
    def __init__(self, files):
        self.files = files

    def namelist(self):
        return list(self.files)

    def read(self, name):
        return self.files[name]


class EconomyUITest(unittest.TestCase):
    def test_classic_profile_serves_original_bytes_to_restore_cached_ui(self):
        files = {'assets/currencyscreen_content_exchange.bin': b'original-layout',
                 'assets/dialog_question_exchange.bin': b'original-confirmation'}
        self.assertEqual(ui.build(FixtureAPK(files), revival=False),
                         {name.removeprefix('assets/'): value for name, value in files.items()})

    def test_correct_sprite_atlas_for_big_art_and_small_currency_icons(self):
        # Schematic length-prefixed strings, never original game asset bytes.
        original = (ui.field('shop_pearl_2') + ui.field('urchin_spine_extra_icon') * 2
                    + ui.field('UI_currency_pearl'))
        confirmation = ui.field('UI_currency_pearl') + ui.field('urchin_spine_extra_icon')
        files = {'assets/currencyscreen_content_exchange.bin': original,
                 'assets/dialog_question_exchange.bin': confirmation}
        result = ui.build(FixtureAPK(files))
        exchange = result['currencyscreen_content_exchange.bin']
        self.assertEqual(exchange, ui.field('shop_coin_3') + ui.field('shop_algae_3')
                         + ui.field('UI_currency_blob') + ui.field('UI_currency_coin'))
        self.assertEqual(result['dialog_question_exchange.bin'],
                         ui.field('UI_currency_coin') + ui.field('UI_currency_blob'))
        self.assertEqual(files['assets/currencyscreen_content_exchange.bin'], original)

    def test_download_metadata_is_derived_from_exact_runtime_payload(self):
        name = 'currencyscreen_content_exchange.bin'
        bundles = {'bundles': [{'resources': [{'name': name, 'path': name,
                   'remoteVersion': 'old', 'size': 3, 'url': 'old'},
                  {'name': 'other.bin', 'url': 'unchanged'}]}]}
        before = copy.deepcopy(bundles)
        payload = b'test-generated-UI'
        result = ui.bundle(bundles, {name: payload}, 'http://127.0.0.1:18080')
        resource = result['bundles'][0]['resources'][0]
        digest = hashlib.sha1(payload).hexdigest()
        self.assertEqual(resource['remoteVersion'], digest)
        self.assertEqual(resource['size'], len(payload))
        self.assertEqual(resource['url'], f'http://127.0.0.1:18080/nemo/revival-ui/{digest}/{name}')
        self.assertEqual(bundles, before)
        self.assertEqual(result['bundles'][0]['resources'][1], before['bundles'][0]['resources'][1])


if __name__ == '__main__':
    unittest.main()
