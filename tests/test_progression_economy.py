"""Progression budgets, migrated saves and repeat-purchase/harvest regressions."""
import copy
import json
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


class ProgressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        apk = Path(cls.folder.name) / 'fixture.apk'
        make_apk(apk)
        cls.classic = quests.Story(economy_mode='classic')
        cls.revival = quests.Story(economy_mode='revival')
        cls.old = actions.Prototypes(worlddef.build(str(apk), cls.classic))
        cls.new = actions.Prototypes(worlddef.build(str(apk), cls.revival))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_growth_and_production_budgets_across_entire_nursery(self):
        changed = 0
        for pid, it in self.revival.data['items'].items():
            if not (pid.startswith(('coral_', 'plant_', 'orn_plant_')) and it.get('price')):
                continue
            p, old = self.new.get(pid)[1], self.old.get(pid)[1]
            with self.subTest(pid=pid):
                cycles = p['growingCycles']
                self.assertLessEqual(p['growingCycleCost']['energy'] * cycles, 25 * it.get('level', 1))
                self.assertLessEqual(p['growingTime']['duration'] * cycles, 3600000)
                self.assertEqual(p['cost'], old['cost'])
                self.assertLessEqual(p['refundReward']['virtualMoney'], p['cost']['virtualMoney'])
                if not it.get('produce'):
                    continue
                before, after = old['producingTime']['duration'], p['producingTime']['duration']
                self.assertEqual(p['producingTime']['timeWarp'], old['producingTime']['timeWarp'])
                self.assertLessEqual(after, 14400000)
                self.assertLessEqual(p['producedReward']['xp'] * before,
                                     old['producedReward']['xp'] * after)
                for currency in ('energy', 'virtualMoney'):
                    self.assertGreaterEqual(p['producedReward'][currency], old['producedReward'][currency])
                changed += p['producedReward'] != old['producedReward']
        self.assertGreater(changed, 25)

    def test_production_override_preserves_native_time_metadata(self):
        p = {'id': 'coral_trial', 'growingCycles': 3,
             'growingCycleCost': {'energy': 30}, 'growingTime': {'duration': 1000, 'timeWarp': 0},
             'producedReward': {'energy': 10, 'xp': 6},
             'producingTime': {'duration': 28800000, 'timeWarp': 7}}
        economy.balance_prototype(p, {'price': {'virtualMoney': 100}, 'level': 10}, 'revival')
        self.assertEqual(p['producingTime'], {'duration': 14400000, 'timeWarp': 7})

    def test_representative_values_are_worthwhile_without_constant_tapping(self):
        expected = {'coral_bell_blue': (30, 3600000, 20),
                    'coral_circles_green': (75, 14400000, 120),
                    'coral_bamboo': (300, 14400000, 240),
                    'plant_barrel_sponge': (498, 14400000, 800)}
        for pid, (growth, milliseconds, harvest) in expected.items():
            p = self.new.get(pid)[1]
            with self.subTest(pid=pid):
                self.assertEqual(p['growingCycleCost']['energy'] * p['growingCycles'], growth)
                self.assertEqual(p['producingTime']['duration'], milliseconds)
                self.assertEqual(max(p['producedReward']['energy'], p['producedReward']['virtualMoney']), harvest)
        # Four upgraded plants preserve the native patch's fourfold reward.
        self.assertEqual(self.new.get('coral_bamboo_upr')[1]['producedReward']['energy'], 960)

    def test_all_pearl_fish_have_affordable_food_and_no_profitable_paid_speedup(self):
        count = 0
        for pid, it in self.revival.data['items'].items():
            if 'fishFeed' not in it or not it.get('produce', {}).get('hardMoney'):
                continue
            p = self.new.get(pid)[1]
            pearls = p['producedReward']['hardMoney']
            self.assertLessEqual(p['producingCost']['energy'], 250 * pearls)
            self.assertLessEqual(p['producingTime']['duration'], 86400000)
            self.assertGreater(p['producingSpeedupCost']['hardMoney'], pearls)
            count += 1
        self.assertGreater(count, 50)

    def test_quest_rewards_are_bounded_and_repeating_activities_are_unchanged(self):
        for qid, q in self.revival.quests.items():
            old = self.classic.quests[qid]
            if q.get('repeat') or q.get('type') != 'STORY' or not old.get('reward'):
                self.assertEqual(q.get('reward'), old.get('reward'))
            if q.get('reward', {}).get('hardMoney'):
                self.assertLessEqual(q['reward']['hardMoney'], 4)
                self.assertTrue(qid.startswith(('story_quest_', 'kids_story_quest_', 'tankgang_')))
        self.assertEqual(self.revival.quests['story_quest_08']['reward']['hardMoney'], 2)
        self.assertEqual(self.revival.quests['side_quest_us_03']['reward'].get('hardMoney', 0), 0)
        self.assertEqual(next(l for l in self.new.levels if l['level'] == 10)['reward']['hardMoney'], 3)
        self.assertEqual(next(l for l in self.new.levels if l['level'] == 11)['reward']['hardMoney'], 1)

    def test_migration_is_idempotent_preserves_paid_stages_and_credits_only_completed_milestones(self):
        now = 100000000
        w = {'status': {'hardMoney': 8, 'energy': 786, 'xp': 4201, 'virtualMoney': 4377},
             'finishedTutorialQuests': ['story_quest_08', 'story_quest_08', 'side_quest_us_03'],
             'corals': [{'id': 3, 'prototypeId': 'coral_bamboo', 'state': 6, 'growingCycle': 3,
                         'producingInterval': {'start': now - 43200000, 'finish': now + 43200000}}]}
        economy.migrate_progression(w, self.new, self.revival.quests, now)
        self.assertEqual(w['status'], {'hardMoney': 10, 'energy': 786, 'xp': 4201, 'virtualMoney': 4377})
        e = w['corals'][0]
        self.assertEqual(e['prototypeId'], 'coral_bamboo')
        self.assertEqual(e['growingCycle'], 3)
        self.assertEqual(e['producingInterval'], {'start': now - 7200000, 'finish': now + 7200000})
        before = copy.deepcopy(w)
        economy.migrate_progression(w, self.new, self.revival.quests, now + 1000)
        self.assertEqual(w, before)

    def test_current_quest_reward_overrides_cached_or_forged_client_amount(self):
        story = quests.Story(economy_mode='revival')
        story.catch_up = lambda *a, **k: None
        q = story.quests['story_quest_08']
        w = {'quests': [], 'finishedTutorialQuests': [], 'nextQuestId': 1,
             'status': {'level': 6}, 'messages': []}
        story.open(w, q['id'], push=False)
        entry = w['quests'][0]
        for task in entry['tasks']:
            task['finished'] = True
        paid = []
        request = {'id': entry['id'], 'tid': entry['tasks'][0]['tid'], 'finished': True,
                   'reward': {'hardMoney': 999999}}
        story.task_state(w, request, lambda q, r: paid.append(r))
        story.task_state(w, request, lambda q, r: paid.append(r))
        self.assertEqual(paid, [q['reward']])

    def test_currency_guards_and_fish_harvest_replay(self):
        reef = actions.Reef(self.new)
        fish = {'id': 1, 'prototypeId': 'fish_rare_bicolor_angelfish_blue', 'state': 1}
        w = {'status': {'level': 40, 'energy': 999, 'hardMoney': 8}, 'fishes': [fish]}
        before = copy.deepcopy(w)
        feed = {'action': 'fishFeed', 'data': {'id': 1}}
        self.assertEqual(reef.apply(w, feed, 1000)['resultCode'], 402)
        self.assertEqual(w, before)
        w['status']['energy'] = 1000
        self.assertEqual(reef.apply(w, feed, 1000)['resultCode'], 200)
        self.assertEqual(w['status']['energy'], 0)
        before = copy.deepcopy(w)
        self.assertEqual(reef.apply(w, feed, 1001)['resultCode'], 409)
        self.assertEqual(reef.apply(w, {'action': 'entityFreeSpeedupProduction', 'data': {'id': 1}}, 1001)['resultCode'], 403)
        self.assertEqual(w, before)
        # It costs five pearls to rush a four-pearl fish; never self-funding.
        self.assertEqual(reef.apply(w, {'action': 'entitySpeedupProduction', 'data': {'id': 1}}, 1002)['resultCode'], 200)
        self.assertEqual(w['status']['hardMoney'], 3)
        collect = {'action': 'entityCollectReward', 'data': {'id': 1}}
        self.assertEqual(reef.apply(w, collect, 1003)['resultCode'], 200)
        self.assertEqual(w['status']['hardMoney'], 7)
        before = copy.deepcopy(w)
        self.assertEqual(reef.apply(w, collect, 1004)['resultCode'], 409)
        self.assertEqual(w, before)

    def test_future_timestamp_cannot_unlock_unearned_harvests(self):
        reef = actions.Reef(self.new)
        w = {'status': {}, 'corals': [{'id': 4, 'prototypeId': 'coral_bamboo', 'state': 6,
             'producingInterval': {'start': 1000, 'finish': 14401000}}]}
        before = copy.deepcopy(w)
        result = reef.apply(w, {'action': 'entityCollectReward',
                  'data': {'id': 4, 'performed': 999999999999}}, 2000)
        self.assertEqual(result['resultCode'], 409)
        self.assertEqual(w, before)


if __name__ == '__main__':
    unittest.main()
