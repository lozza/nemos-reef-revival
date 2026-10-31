"""Vampire Crab feeding must match its pearl-only native reward panel."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions
import economy
import quests
import worlddef
from test_server_runtime import make_apk

CRABS = ('fish_rare_vampire', 'fish_rare_vampire_green',
         'fish_rare_vampire_red', 'fish_rare_vampire_orange')


class CrabRewardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        apk = Path(cls.folder.name) / 'fixture.apk'
        make_apk(apk)
        cls.story = quests.Story(economy_mode='revival')
        cls.protos = actions.Prototypes(worlddef.build(str(apk), cls.story))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_all_variants_display_one_pearl_and_have_vitality(self):
        for fid in CRABS:
            with self.subTest(fid=fid):
                p = self.protos.get(fid)[1]
                self.assertEqual(p['producingCost'], worlddef.cost(algae=100))
                self.assertEqual(p['producedReward'], worlddef.reward(pearls=1))
                self.assertEqual(p['producingTime'], {'duration': 86400000, 'timeWarp': 0})
                self.assertEqual(p['vitality'], 500)
                self.assertGreater(p['producingSpeedupCost']['hardMoney'], 1)

    def test_feed_and_harvest_are_affordable_timed_and_not_replayable(self):
        reef = actions.Reef(self.protos)
        fish = {'id': 99, 'prototypeId': 'fish_rare_vampire_orange', 'state': actions.BUILT}
        world = {'fishes': [fish], 'status': {'level': 8, 'energy': 99, 'hardMoney': 5}}
        feed = {'action': 'fishFeed', 'data': {'id': 99}}
        collect = {'action': 'entityCollectReward', 'data': {'id': 99}}
        self.assertEqual(reef.apply(world, feed, 1000)['resultCode'], 402)
        world['status']['energy'] = 100
        self.assertEqual(reef.apply(world, feed, 1000)['resultCode'], 200)
        self.assertEqual(world['status']['energy'], 0)
        self.assertEqual(reef.apply(world, feed, 1001)['resultCode'], 409)
        self.assertEqual(reef.apply(world, collect, 1001)['resultCode'], 409)
        reef.advance(fish, 86401000)
        self.assertEqual(reef.apply(world, collect, 86401000)['resultCode'], 200)
        self.assertEqual(world['status']['hardMoney'], 6)
        self.assertEqual(world['status'].get('virtualMoney', 0), 0)
        self.assertEqual(world['status'].get('xp', 0), 0)
        self.assertEqual(reef.apply(world, collect, 86401001)['resultCode'], 409)

    def test_existing_paid_short_cycle_and_balances_are_preserved(self):
        world = {'progressionEconomyVersion': economy.PROGRESSION_VERSION,
                 'status': {'hardMoney': 5, 'energy': 100},
                 'fishes': [{'id': 99, 'prototypeId': 'fish_rare_vampire_orange',
                             'state': actions.PRODUCING,
                             'producingInterval': {'start': 1000, 'finish': 301000}}]}
        old = copy.deepcopy(world)
        economy.migrate_progression(world, self.protos, self.story.quests, 2000)
        self.assertEqual(world['status'], old['status'])
        self.assertEqual(world['fishes'][0]['producingInterval'], old['fishes'][0]['producingInterval'])
        self.assertEqual(world['fishes'][0]['id'], 99)

    def test_classic_catalogue_and_explicit_future_values_are_not_overwritten(self):
        self.assertNotIn('fishFeed', quests.Story(economy_mode='classic').data['items']['fish_rare_vampire_orange'])
        data = {'items': {'fish_rare_vampire_orange': {'fishFeed': 75, 'every': 43200,
                                                    'produce': {'hardMoney': 1}, 'hearts': 800}}}
        economy.configure_story(data, 'revival')
        self.assertEqual(data['items']['fish_rare_vampire_orange']['fishFeed'], 75)
        self.assertEqual(data['items']['fish_rare_vampire_orange']['hearts'], 800)


if __name__ == '__main__':
    unittest.main()
