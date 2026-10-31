"""Empty Patch shop rows must not interfere with filled Patch production."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions
import quests
import worlddef
from test_server_runtime import make_apk


class PatchShopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        apk = Path(cls.folder.name) / 'fixture.apk'
        make_apk(apk)
        # The general server fixture groups spec_house_* as houses. The real
        # APK explicitly marks this particular prototype ET_PATCH.
        prototypes = [(pid, dict(attrs, type='ET_PATCH') if pid == 'spec_house_patch' else attrs)
                      for pid, attrs in worlddef.read_prototypes(str(apk))]
        with patch('worlddef.read_prototypes', return_value=prototypes):
            cls.definitions = {mode: worlddef.build(str(apk), quests.Story(economy_mode=mode))
                               for mode in ('classic', 'revival')}

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_empty_patch_has_no_generic_production_shop_rows(self):
        for mode, wd in self.definitions.items():
            self.assertEqual(len(wd['patchList']), 3)
            for p in wd['patchList']:
                with self.subTest(mode=mode, prototype=p['id']):
                    self.assertEqual(p['producingTime'], {'duration': 0, 'timeWarp': 0})
                    self.assertFalse(any(p['producedReward'].values()))
                    self.assertEqual(p['maxPlants'], 4)
            self.assertTrue(any(s['prototypeId'] == 'spec_house_patch' for s in wd['ingameShopItems']))

    def test_filling_patch_retains_upgraded_plant_timer_and_reward(self):
        for mode, wd in self.definitions.items():
            with self.subTest(mode=mode):
                protos = actions.Prototypes(wd)
                reef = actions.Reef(protos)
                base = protos.get('coral_fan')[1]
                upgraded = protos.get(base['upgradePrototypeId'])[1]
                patch = {'id': 1, 'prototypeId': 'spec_house_patch', 'state': actions.BUILT,
                         'plantCount': 0}
                world = {'patches': [patch], 'corals': [], 'status': {'level': 13}}
                for ident in range(2, 6):
                    world['corals'].append({'id': ident, 'prototypeId': 'coral_fan',
                                            'state': actions.READY, 'growingCycle': 3})
                    reaction = reef.apply(world, {'action': 'placeEntityInPatch',
                                          'data': {'id': ident, 'patchId': 1}}, 1000)
                    self.assertEqual(reaction['resultCode'], 200)
                    self.assertEqual(reaction['data']['full'], ident == 5)
                self.assertEqual(world['corals'], [])
                duration = upgraded['producingTime']['duration']
                self.assertGreater(duration, 0)
                self.assertEqual(patch['producingInterval'], {'start': 1000, 'finish': 1000 + duration})
                patch['state'] = actions.READY
                reaction = reef.apply(world, {'action': 'entityCollectReward',
                                      'data': {'id': 1}}, 1000 + duration)
                self.assertEqual(reaction['resultCode'], 200)
                for currency in ('energy', 'virtualMoney', 'xp'):
                    self.assertEqual(world['status'].get(currency, 0), upgraded['producedReward'][currency])
                self.assertEqual(upgraded['producedReward']['energy'], base['producedReward']['energy'] * 4)
                self.assertEqual(patch['producingInterval']['finish'], 1000 + 2 * duration)


if __name__ == '__main__':
    unittest.main()
