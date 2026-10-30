"""Daily availability, original-client popup settings, and saved claim state.

Both the Android route and desktop capability route use Game.game. All data
below uses a synthetic APK and a private temporary database.
"""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import nemo_server
from test_server_runtime import make_apk


class DailyRewardProtocolTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        apk = self.folder / 'fixture.apk'
        make_apk(apk)
        self.args = SimpleNamespace(apk=str(apk), data=str(self.folder / 'data'),
            no_story=False, worlddef=None, economy='revival', reset_all=False,
            reset_worlds=False, event=[])
        self.game = self.open_game()
        self.uid = self.game.store.create_user('daily-test-device', 'DailyTest')['uid']
        self.today = '2026-10-30'
        original_strftime = time.strftime
        self.day = patch('actions.time.strftime', side_effect=lambda fmt, *args:
            self.today if fmt == '%Y-%m-%d' else original_strftime(fmt, *args))
        self.day.start()
        self.addCleanup(self.day.stop)

    def open_game(self):
        game = nemo_server.Game(self.args)
        self.addCleanup(game.store.db.close)
        return game

    def call(self, endpoint='api', actions=None, desktop=False):
        raw, _ = self.game.game(endpoint, {'uid': self.uid,
            '_exchange_client': desktop, 'json': json.dumps(
                {'uid': self.uid, 'actions': actions or []})})
        return json.loads(raw)

    def resume(self, desktop=False):
        return self.call(actions=[{'aid': 91, 'action': 'resumeGame'}], desktop=desktop)

    def claim(self, aid, desktop=False):
        return self.call(actions=[{'aid': aid, 'action': 'dailyTapReward'}], desktop=desktop)

    def test_popup_configuration_limits_presentations_not_reward_picks(self):
        settings = self.game.worlddef['globalSettings']
        self.assertEqual(settings['dailyTapRewardsCount'], 3)
        self.assertEqual(settings['dailyRewardDisplayCount'], 1)
        # Withdraw is a delay in minutes, not a presentation count.
        self.assertEqual(settings['dailyRewardDisplayDelayWithdraw'], 240)

    def test_three_claims_stay_claimed_after_resume_reopen_and_server_restart(self):
        for desktop in (False, True):
            with self.subTest(desktop=desktop):
                w = self.game.user_world(self.uid)
                w['dailyTapDay'] = '2026-10-29'
                self.game.store.save_world(self.uid, w)
                self.assertTrue(self.call('startgame', desktop=desktop)['resumeGame']['dailyTapRewardReady'])
                for n in range(1, 4):
                    response = self.claim(n, desktop)
                    self.assertEqual(response['reactions'][0]['resultCode'], 200)
                    self.assertEqual(response['status']['tapRewardsCount'], n)
                before = self.game.store.world(self.uid)['status'].copy()
                for _ in range(2):
                    response = self.resume(desktop)
                    self.assertFalse(response['reactions'][0]['data']['resumeGame']['dailyTapRewardReady'])
                    self.assertEqual(response['status']['tapRewardsCount'], 3)
                    self.assertFalse(self.call('startgame', desktop=desktop)['resumeGame']['dailyTapRewardReady'])
                self.game = self.open_game()
                self.assertFalse(self.call('startgame', desktop=desktop)['resumeGame']['dailyTapRewardReady'])
                response = self.claim(4, desktop)
                self.assertEqual(response['reactions'][0]['resultCode'], 409)
                for field in ('virtualMoney', 'energy', 'hardMoney', 'tapRewardsCount'):
                    self.assertEqual(response['status'][field], before[field])

    def test_resume_after_midnight_returns_new_day_count_with_new_day_availability(self):
        self.call('startgame')
        for n in range(3):
            self.claim(n)
        self.today = '2026-10-31'
        response = self.resume()
        self.assertTrue(response['reactions'][0]['data']['resumeGame']['dailyTapRewardReady'])
        self.assertEqual(response['status']['tapRewardsCount'], 0)
        self.assertEqual(self.claim(10)['status']['tapRewardsCount'], 1)
        self.game = self.open_game()
        response = self.call('startgame')
        self.assertEqual(response['status']['tapRewardsCount'], 1)
        self.assertTrue(response['resumeGame']['dailyTapRewardReady'])


if __name__ == '__main__':
    unittest.main()

