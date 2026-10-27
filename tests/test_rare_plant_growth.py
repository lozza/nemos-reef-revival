"""Rare plants must never receive the native Harvestable timer protocol."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions


class RarePlantGrowthTest(unittest.TestCase):
    def setUp(self):
        def plant(pid, kind):
            return {'id': pid, 'type': kind, 'growingCycles': 3,
                    'growingCycleCost': {'energy': 10},
                    'growingTime': {'duration': 100, 'timeWarp': 0},
                    'producingTime': {'duration': 1000, 'timeWarp': 0}}
        self.reef = actions.Reef(actions.Prototypes({
            'revivalEconomy': 'revival',
            'rarePlantPrototypeList': [plant('rare_plant_sea_grapes_green', 'ET_RARE_PLANT')],
            'ornamentalPlantPrototypeList': [plant('orn_plant_trial', 'ET_ORN_PLANT')],
            'coralPrototypeList': [plant('coral_trial', 'ET_CORAL')],
            'seedPrototypeList': [plant('seed_pearl', 'ET_SEED')]}))
        self.world = {'status': {'energy': 100}, 'rarePlants': [], 'ornamentalPlants': [], 'corals': []}

    def test_seed_rare_final_feed_has_no_production_timer_and_completes_once(self):
        e = {'id': 118, 'prototypeId': 'rare_plant_sea_grapes_green',
             'seedPrototypeId': 'seed_pearl', 'state': actions.WAITING_FEED,
             'growingCycle': 2, 'seeded': 50, 'position': {'x': 82, 'z': 61}}
        self.world['rarePlants'].append(e)
        before = copy.deepcopy(e)
        response = self.reef.a_entityFeed(self.world, {'id': 118}, 1000)
        self.assertEqual(response, {'growingCycle': 3, 'growingInterval': {'start': 1000, 'finish': 1100}})
        self.assertEqual(self.world['status']['energy'], 90)
        self.reef.refresh(self.world, 1099)
        self.assertEqual(e['state'], actions.GROWING)
        self.reef.refresh(self.world, 1100)
        self.assertEqual(e['state'], actions.BUILT)
        self.assertNotIn('producingInterval', e)
        for key in ('id', 'prototypeId', 'seedPrototypeId', 'seeded', 'position'):
            self.assertEqual(e[key], before[key])
        self.assertEqual(self.reef.events, [(actions.END_FEED, {'id': 118, 'state': actions.BUILT})])
        self.reef.refresh(self.world, 1200)
        self.assertEqual(len(self.reef.events), 1)

    def test_saved_zero_time_rare_producers_recover_without_other_changes(self):
        for state in (actions.PRODUCING, actions.READY):
            with self.subTest(state=state):
                e = {'id': 1, 'prototypeId': 'rare_plant_sea_grapes_green',
                     'seedPrototypeId': 'seed_pearl', 'growingCycle': 3,
                     'state': state, 'position': {'x': 1, 'z': 2},
                     'producingInterval': {'start': 1000, 'finish': 1000}}
                expected = dict(e, state=actions.BUILT)
                expected.pop('producingInterval')
                self.reef.advance(e, 1100)
                self.assertEqual(e, expected)
                self.reef.advance(e, 1200)
                self.assertEqual(e, expected)

    def test_grown_rare_reward_is_built_without_a_harvest(self):
        e = self.reef.add_entity(self.world, 'rare_plant_sea_grapes_green', 1, 2)
        self.assertEqual(e['state'], actions.BUILT)
        self.assertNotIn('producingInterval', e)

    def test_coral_final_feed_still_starts_production(self):
        e = {'id': 2, 'prototypeId': 'coral_trial', 'state': actions.WAITING_FEED, 'growingCycle': 2}
        self.world['corals'].append(e)
        response = self.reef.a_entityFeed(self.world, {'id': 2}, 1000)
        self.assertEqual(response['producingInterval'], {'start': 1100, 'finish': 2100})
        self.reef.refresh(self.world, 1100)
        self.assertEqual(e['state'], actions.PRODUCING)
        self.reef.refresh(self.world, 2100)
        self.assertEqual(e['state'], actions.READY)


if __name__ == '__main__':
    unittest.main()
