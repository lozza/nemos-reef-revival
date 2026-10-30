"""Daily item renderer compatibility and daily claim exhaustion regressions."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import actions
import nemo_server


class DailyRewardTest(unittest.TestCase):
    def setUp(self):
        self.reef = actions.Reef(actions.Prototypes({}))
        self.world = {'status': {'level': 11, 'hardMoney': 10, 'virtualMoney': 100,
                                 'energy': 208, 'xp': 4263}}

    def claim(self):
        return self.reef.apply(self.world, {'action': 'dailyTapReward', 'aid': 0}, int(time.time()*1000))

    def test_every_possible_daily_roll_returns_a_supported_currency_not_an_xp_only_or_plant_result(self):
        for kind in ('virtualMoney', 'energy', 'hardMoney'):
            with self.subTest(kind=kind):
                self.setUp()
                with patch('actions.random.choices', return_value=[kind]) as roll:
                    response = self.claim()
                options, weights = roll.call_args.args
                self.assertNotIn('xp', options)
                self.assertEqual(weights[options.index('hardMoney')], 15)
                self.assertEqual(sum(weights), 100)
                reward = response['data']['reward']
                self.assertEqual(reward['xp'], 0)
                self.assertGreater(reward[kind], 0)
                self.assertEqual(self.world['status']['xp'], 4263)
                self.assertEqual(self.world['status']['tapRewardsCount'], 1)

    def test_third_claim_guarantees_pearls_and_fourth_never_sends_empty_success(self):
        with patch('actions.random.choices', return_value=['energy']):
            self.claim()
            self.claim()
        self.assertTrue(nemo_server.user_info(self.world)['resumeGame']['dailyTapRewardReady'])
        response = self.claim()
        self.assertEqual(response['data']['reward']['hardMoney'], 3)
        self.assertEqual(self.world['status']['hardMoney'], 13)
        self.assertFalse(nemo_server.user_info(self.world)['resumeGame']['dailyTapRewardReady'])
        before = copy.deepcopy(self.world)
        response = self.claim()
        self.assertEqual(response['resultCode'], 409)
        self.assertNotIn('reward', response.get('data', {}))
        self.assertEqual(self.world, before)

    def test_next_day_resets_claims_but_reconnects_preserve_partial_claims(self):
        self.world['dailyTapDay'] = '2000-01-01'
        self.world['status']['tapRewardsCount'] = 3
        with patch('actions.random.choices', return_value=['virtualMoney']):
            self.assertEqual(self.claim()['resultCode'], 200)
            self.reef.new_day(self.world)
            self.assertEqual(self.world['status']['tapRewardsCount'], 1)
            self.assertEqual(self.claim()['resultCode'], 200)
        self.assertEqual(self.world['status']['tapRewardsCount'], 2)
        self.assertFalse(self.world['status']['tapRewardsExtraTapAvail'])
        self.assertFalse(self.world['status']['tapRewardsGetAllAvail'])


if __name__ == '__main__':
    unittest.main()

