"""Economy invariants: currency, retries, daily limits and saved seed progress."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import economy
import actions
import quests
import nemo_server
from test_server_runtime import make_apk


class ExchangeTest(unittest.TestCase):
    def setUp(self):
        self.world = {'status': {'level': 10, 'virtualMoney': 10000,
                                 'energy': 187, 'hardMoney': 5, 'xp': 123}}
        economy.start_session(self.world)
        self.now = 1792886400000

    def buy(self, n=1, stamp=1, level=10, now=None, profile='revival'):
        return economy.exchange(self.world,
            {'exchangePackageId': f'revival-algae-v1-L{level}-{n}'},
            stamp, 0, self.now if now is None else now, profile)

    def test_purchase_changes_only_coins_algae_and_persistent_quota(self):
        result = self.buy(2)
        self.assertEqual(result['resultCode'], 200)
        self.assertEqual(self.world['status'], {'level': 10, 'virtualMoney': 9200,
                                               'energy': 587, 'hardMoney': 5, 'xp': 123})
        self.world = json.loads(json.dumps(self.world))
        before = copy.deepcopy(self.world)
        self.assertEqual(self.buy(2), result)
        self.assertEqual(self.world, before)
        self.assertEqual(self.buy(2, stamp=2)['resultCode'], 403)
        self.assertEqual(self.world, before)
        self.assertEqual(self.buy(1, stamp=3)['resultCode'], 200)
        self.assertEqual(self.world['algaeExchange']['packs'], 3)

    def test_retry_across_midnight_is_not_a_second_purchase(self):
        result = self.buy(3)
        before = copy.deepcopy(self.world)
        tomorrow = self.now + 86400000
        self.assertEqual(self.buy(3, now=tomorrow), result)
        self.assertEqual(self.world, before)
        self.assertEqual(self.buy(3, stamp=2, now=tomorrow)['resultCode'], 200)
        self.assertEqual(self.world['status']['energy'], 1387)

    def test_session_restart_does_not_reset_daily_limit(self):
        self.buy(3)
        economy.start_session(self.world)
        self.assertEqual(self.buy(1)['resultCode'], 403)

    def test_rejections_never_mutate_the_reef(self):
        for n, level, stamp, profile, code in (
            (1, 11, 1, 'revival', 403),
            (1, 5, 1, 'revival', 403),
            (1, 10, None, 'revival', 400),
            (1, 10, True, 'revival', 400),
            (1, 10, 1, 'classic', 403),
            (4, 10, 1, 'revival', 400),
        ):
            with self.subTest(n=n, level=level, stamp=stamp, profile=profile):
                before = copy.deepcopy(self.world)
                self.assertEqual(self.buy(n, stamp, level, profile=profile)['resultCode'], code)
                self.assertEqual(self.world, before)
        self.world['status']['virtualMoney'] = 399
        before = copy.deepcopy(self.world)
        self.assertEqual(self.buy()['resultCode'], 402)
        self.assertEqual(self.world, before)

    def test_client_cannot_supply_rewards_or_reuse_receipt_for_larger_pack(self):
        self.assertEqual(economy.exchange(self.world,
            {'exchangePackageId': 'revival-algae-v1-L10-1', 'reward': {'energy': 999999}},
            1, 0, self.now, 'revival')['resultCode'], 200)
        self.assertEqual(self.world['status']['energy'], 387)
        before = copy.deepcopy(self.world)
        self.assertEqual(self.buy(3)['resultCode'], 409)
        self.assertEqual(self.world, before)

    def test_catalogue_rates_and_native_currency_fields(self):
        self.assertEqual(economy.packages(5), [])
        for level in (6, 10, 20, 38, 60):
            for n, p in enumerate(economy.packages(level), 1):
                self.assertEqual(p['cost']['virtualMoney'], 40 * level * n)
                self.assertEqual(p['reward']['energy'], 20 * level * n)
                self.assertEqual(p['reward']['xp'], 0)
                self.assertEqual(p['reward']['hardMoney'], 0)
                self.assertEqual(p['guiOrder'], n)


class SeedEconomyTest(unittest.TestCase):
    def test_insufficient_seed_feed_does_not_grant_an_unpaid_growth_stage(self):
        reef = actions.Reef(actions.Prototypes({'revivalEconomy': 'revival',
            'seedPrototypeList': [{'id': 'seed_coin', 'growingCycleCost': {'energy': 60},
                                   'growingCycles': 3, 'growingTime': {'duration': 900000}}],
            'ornamentalPlantPrototypeList': [{'id': 'orn_plant_lilac'}]}))
        entity = {'id': 1, 'prototypeId': 'orn_plant_lilac', 'seedPrototypeId': 'seed_coin',
                  'state': actions.WAITING_FEED, 'growingCycle': 1}
        world = {'status': {'energy': 59}, 'ornamentalPlants': [entity]}
        before = copy.deepcopy(world)
        result = reef.apply(world, {'action': 'entityFeed', 'aid': 0, 'data': {'id': 1}}, 1000)
        self.assertEqual(result['resultCode'], 402)
        self.assertEqual(world, before)

    def test_classic_catalogue_is_unchanged_and_revival_does_not_edit_source(self):
        path = Path(quests.STORY)
        original = path.read_bytes()
        classic = quests.Story(economy_mode='classic')
        revival = quests.Story(economy_mode='revival')
        self.assertEqual(classic.data['items']['seed_coin']['feed'], 200)
        self.assertEqual(revival.data['items']['seed_coin']['feed'], 60)
        self.assertEqual(revival.data['items']['seed_coin']['step'], 900)
        self.assertEqual(path.read_bytes(), original)

    def test_rarity_roll_is_independent_of_number_of_species(self):
        for rare_count in (1, 105):
            choices = ['orn_plant_lilac'] + [f'rare_plant_{i}' for i in range(rare_count)]
            for sid, chance in (('seed_coin', .25), ('seed_coin_expensive', .75)):
                for roll, rare in ((chance - .001, True), (chance, False)):
                    with patch('economy.random.random', return_value=roll):
                        result = economy.choose_seed({}, sid, choices)
                    self.assertEqual(result.startswith('rare_plant_'), rare)

    def test_fifth_mystery_seed_guarantees_rare_and_resets_pity(self):
        world = {}
        with patch('economy.random.random', return_value=.99):
            results = [economy.choose_seed(world, 'seed_coin',
                       ['rare_plant_a', 'orn_plant_lilac']) for _ in range(5)]
        self.assertEqual(results, ['orn_plant_lilac'] * 4 + ['rare_plant_a'])
        self.assertEqual(world['mysterySeedMisses'], 0)

    def test_timer_migration_preserves_paid_feeds_identity_and_elapsed_fraction(self):
        now = 10000000
        entity = {'id': 89, 'prototypeId': 'orn_plant_lilac',
                  'seedPrototypeId': 'seed_coin', 'growingCycle': 2, 'state': 3,
                  'growingInterval': {'start': now - 6300000, 'finish': now + 6300000}}
        world = {'ornamentalPlants': [entity], 'status': {'energy': 187}}
        expected = copy.deepcopy(entity)
        expected.update(seedEconomyVersion=economy.VERSION,
                        growingInterval={'start': now - 450000, 'finish': now + 450000})
        economy.migrate_seed_timers(world, now)
        self.assertEqual(entity, expected)
        self.assertEqual(world['status']['energy'], 187)
        before = copy.deepcopy(world)
        economy.migrate_seed_timers(world, now + 1000)
        self.assertEqual(world, before)


class EconomyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        folder = Path(self.directory.name)
        apk = folder / 'fixture.apk'
        make_apk(apk)
        self.game = nemo_server.Game(SimpleNamespace(apk=str(apk), data=str(folder / 'data'),
            no_story=False, worlddef=None, economy='revival', reset_all=False, reset_worlds=False))
        self.addCleanup(self.game.store.db.close)
        user = self.game.store.create_user('isolated-test', 'Trial')
        self.uid = user['uid']
        self.world = self.game.user_world(self.uid)
        self.world['status'].update(level=10, virtualMoney=10000, energy=187)
        economy.start_session(self.world)
        self.game.store.save_world(self.uid, self.world)

    def request(self, endpoint, **data):
        body, _ = self.game.game(endpoint, {'uid': self.uid, 'json': json.dumps(data),
                                           '_exchange_client': True})
        return json.loads(body)

    def test_level_catalogue_versions_are_stable_and_contain_correct_offers(self):
        version = self.request('getcurrentworldversion')['worldVersion']
        self.assertEqual(self.request('getcurrentworldversion')['worldVersion'], version)
        wd = json.loads(nemo_server.xor(self.game.game('getdaworld', {'worldVersion': version})[0]))
        self.assertEqual(wd['exchangePackagePrototypes'], economy.packages(10))
        self.world['status']['level'] = 20
        self.game.store.save_world(self.uid, self.world)
        higher = self.request('getcurrentworldversion')['worldVersion']
        self.assertNotEqual(higher, version)
        wd = json.loads(nemo_server.xor(self.game.game('getdaworld', {'worldVersion': higher})[0]))
        self.assertEqual(wd['exchangePackagePrototypes'], economy.packages(20))
        self.world['status']['level'] = 5
        self.game.store.save_world(self.uid, self.world)
        low = self.request('getcurrentworldversion')['worldVersion']
        wd = json.loads(nemo_server.xor(self.game.game('getdaworld', {'worldVersion': low})[0]))
        self.assertEqual(wd['exchangePackagePrototypes'], [])

    def test_older_apks_do_not_receive_unusable_offers_or_make_exchanges(self):
        raw, _ = self.game.game('getcurrentworldversion', {'uid': self.uid})
        version = json.loads(raw)['worldVersion']
        wd = json.loads(nemo_server.xor(self.game.game('getdaworld', {'worldVersion': version})[0]))
        self.assertEqual(wd['exchangePackagePrototypes'], [])
        before = copy.deepcopy(self.world)
        result = self.game.reaction(self.world, {'action': 'exchangeMoney', 'aid': 0,
                    'data': {'exchangePackageId': 'revival-algae-v1-L10-1'}}, stamp=1)
        self.assertEqual(result['resultCode'], 403)
        self.assertEqual(self.world, before)

    def test_real_action_route_persists_exchange_and_deduplicates_after_reload(self):
        action = {'action': 'exchangeMoney', 'aid': 0,
                  'data': {'exchangePackageId': 'revival-algae-v1-L10-1'}}
        first = self.request('api', stamp=7, actions=[action])
        self.assertEqual(first['reactions'][0]['resultCode'], 200)
        self.assertEqual(first['status']['virtualMoney'], 9600)
        self.assertEqual(first['status']['energy'], 387)
        second = self.request('api', stamp=7, actions=[action])
        self.assertEqual(second['status'], first['status'])
        saved = self.game.store.world(self.uid)
        self.assertEqual(saved['algaeExchange']['packs'], 1)
        self.assertEqual(len(saved['exchangeReceipts']), 1)

    def test_ui_download_uses_only_the_generated_resource_and_its_content_hash(self):
        import hashlib
        name = 'currencyscreen_content_exchange.bin'
        payload = b'schematic generated screen; no original asset bytes'
        self.game.economy_assets = {name: payload}
        self.game.bundles = {'bundles': [{'resources': [{'name': name}]}]}
        old_game = nemo_server.Handler.game
        nemo_server.Handler.game = self.game
        self.addCleanup(setattr, nemo_server.Handler, 'game', old_game)
        server = nemo_server.ThreadingHTTPServer(('127.0.0.1', 0), nemo_server.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        base = f'http://127.0.0.1:{server.server_port}'
        with urllib.request.urlopen(base + '/nemo/api/resources/1') as response:
            resource = json.load(response)['bundles'][0]['resources'][0]
        self.assertEqual(resource['url'], base + '/nemo/revival-ui/'
                         + hashlib.sha1(payload).hexdigest() + '/' + name)
        with urllib.request.urlopen(resource['url']) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(response.read(), payload)
        for path in ('/nemo/revival-ui/incorrect/' + name,
                     '/nemo/revival-ui/' + hashlib.sha1(payload).hexdigest() + '/nemo.db'):
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(base + path)
            self.assertEqual(failure.exception.code, 404)
            failure.exception.close()


if __name__ == '__main__':
    unittest.main()
