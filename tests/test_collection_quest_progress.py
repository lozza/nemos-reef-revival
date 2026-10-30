"""Native parameter order, proven historical harvests, and generic rare growth."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions
import quest_history
import quests


class QuestProgressTest(unittest.TestCase):
    def setUp(self):
        self.story = quests.Story()
        self.story.catch_up = lambda *a, **k: None
        self.world = {'quests': [], 'finishedTutorialQuests': [], 'nextQuestId': 50,
                      'nextMessageId': 1, 'status': {'level': 13}, 'messages': [],
                      'rarePlants': [], 'ornamentalPlants': [],
                      'corals': [{'id': 1, 'prototypeId': 'coral_trial'}],
                      'livingPlants': [{'id': 2, 'prototypeId': 'plant_trial'}]}
        self.protos = actions.Prototypes({
            'coralPrototypeList': [{'id': 'coral_trial', 'shopCategory': 'CORAL'}],
            'livingPlantPrototypeList': [{'id': 'plant_trial', 'shopCategory': 'LIVINGPLANT'}],
            'rarePlantPrototypeList': [{'id': 'rare_trial', 'shopCategory': 'RAREPLANT', 'growingCycles': 3}],
            'ornamentalPlantPrototypeList': [{'id': 'orn_trial', 'shopCategory': 'ORNAMENTALPLANT', 'growingCycles': 3}],
            'seedPrototypeList': [{'id': 'seed_coin', 'growingCycles': 3}]})
        self.paid = []

    def finish(self, q, reward):
        self.paid.append(q['id'])

    def row(self, status, actions_list=None, messages=None, uid='player', reactions=None):
        return {'status': 200, 'params': {'json': json.dumps({'uid': uid, 'actions': actions_list or []})},
                'response': json.dumps({'status': status, 'messages': messages or [],
                                       'reactions': reactions or []})}

    def harvest(self, ident=1):
        return {'aid': 0, 'action': 'entityCollectReward', 'data': {'id': ident}}

    def recover(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requests.log'
            path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n{broken trailing line', encoding='utf-8')
            self.story.recover_collection_tasks(self.world, self.protos, 'player', str(path), self.finish)

    def test_native_count_any_amount_is_first_but_grow_any_category_is_first(self):
        for qid, amount, category in [('side_quest_03', 300, 'LIVINGPLANT'),
                                       ('side_quest_us_06', 50, 'CORAL')]:
            task = self.story.quest_prototype(self.story.quests[qid])['endConditionList'][0]
            self.assertEqual(task['parameterValueList'], [{'index': 1, 'value': str(amount)},
                                                          {'index': 2, 'value': category}])
        q = self.story.quest_prototype(self.story.quests['side_quest_us_01'])
        self.assertEqual(q['endConditionList'][0]['parameterValueList'],
                         [{'index': 1, 'value': 'RAREPLANT'}, {'index': 2, 'value': '2'}])
        for definition in self.story.data['quests']:
            for source, native in zip(definition['tasks'], self.story.quest_prototype(definition)['endConditionList']):
                if quest_history.collection_spec(source):
                    self.assertGreater(int(native['parameterValueList'][0]['value']), 0)

    def test_four_grown_rares_complete_rarity_and_credit_halloween_once(self):
        self.story.open(self.world, 'side_quest_us_01', push=False)
        self.story.open(self.world, 'time_event_halloween_02', push=False)
        for ident in range(10, 14):
            self.world['rarePlants'].append({'id': ident, 'prototypeId': 'rare_trial',
                'seedPrototypeId': 'seed_coin', 'state': actions.BUILT, 'growingCycle': 3})
        # Grown ornamental seed results and unfinished rare seeds cannot count.
        self.world['ornamentalPlants'].append({'id': 20, 'prototypeId': 'orn_trial',
            'seedPrototypeId': 'seed_coin', 'state': actions.BUILT, 'growingCycle': 3})
        self.world['rarePlants'].append({'id': 21, 'prototypeId': 'rare_trial',
            'seedPrototypeId': 'seed_coin', 'state': actions.GROWING, 'growingCycle': 3})
        plants = copy.deepcopy(self.world['rarePlants'])
        for _ in range(2):
            self.story.recover_seed_tasks(self.world, self.protos, self.finish)
        self.assertEqual(self.paid, ['side_quest_us_01'])
        task = self.world['quests'][0]['tasks'][0]
        self.assertEqual(task['progress']['count'], 10)
        self.assertEqual(task['progress']['counter'], 6)
        self.assertEqual(self.world['rarePlants'], plants)

    def test_zero_target_repaired_without_treating_current_balance_as_income(self):
        self.world['status']['energy'] = 9999
        self.story.open(self.world, 'side_quest_us_06', push=False)
        task = self.world['quests'][0]['tasks'][0]
        task['progress'] = {'count': 0, 'counter': 0, 'state': 1}
        self.recover([])
        self.assertEqual(task['progress'], {'count': 50, 'counter': 50, 'state': 1})
        self.assertEqual(self.paid, [])

    def test_confirmed_harvest_completes_once_and_ignores_other_income(self):
        self.story.open(self.world, 'side_quest_us_06', push=False)
        entry = self.world['quests'][0]
        entry['tasks'][0]['progress'] = {'count': 0, 'counter': 0, 'state': 1}
        opened = [{'data': {'prototypeId': entry['prototypeId'], 'id': entry['id']}}]
        ok = [{'aid': 0, 'resultCode': 200}]
        rows = [self.row({'level': 13, 'energy': 100}, messages=opened),
                self.row({'level': 13, 'energy': 130}, [self.harvest()], reactions=ok),
                # Duplicate reply changes no balance and must not count twice.
                self.row({'level': 13, 'energy': 130}, [self.harvest()], reactions=ok),
                self.row({'level': 13, 'energy': 1000}, [self.harvest()], uid='another-player', reactions=ok),
                self.row({'level': 13, 'energy': 160}, [self.harvest()], reactions=ok)]
        self.recover(rows)
        self.assertEqual(self.paid, ['side_quest_us_06'])
        self.recover(rows)
        self.assertEqual(self.paid, ['side_quest_us_06'])

    def test_wrong_category_rejected_harvests_level_gifts_and_mixed_actions_do_not_count(self):
        self.story.open(self.world, 'side_quest_us_06', push=False)
        e = self.world['quests'][0]
        target = {(e['id'], 1): (e['prototypeId'], {'CORAL'}, 'energy')}
        opened = [{'data': {'prototypeId': e['prototypeId'], 'id': e['id']}}]
        ok = [{'aid': 0, 'resultCode': 200}]
        rows = [self.row({'level': 13, 'energy': 0}),
                self.row({'level': 13, 'energy': 100}, [self.harvest()], reactions=ok),
                self.row({'level': 13, 'energy': 100}, messages=opened),
                self.row({'level': 13, 'energy': 200}, [self.harvest(2)], reactions=ok),
                self.row({'level': 13, 'energy': 300}, [self.harvest()], reactions=[{'aid': 0, 'resultCode': 409}]),
                self.row({'level': 14, 'energy': 500}, [self.harvest()], reactions=ok),
                self.row({'level': 14, 'energy': 600}, [self.harvest(), {'action': 'exchangeMoney'}], reactions=ok)]
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'log'
            p.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            self.assertEqual(quest_history.confirmed_harvests(p, 'player', self.world, self.protos, target),
                             {(e['id'], 1): 0})

    def test_valid_partial_progress_is_preserved_if_history_is_missing(self):
        self.story.open(self.world, 'side_quest_03', push=False)
        task = self.world['quests'][0]['tasks'][0]
        task['progress'] = {'count': 300, 'counter': 125, 'state': 1}
        self.recover([])
        self.assertEqual(task['progress']['counter'], 125)


if __name__ == '__main__':
    unittest.main()
