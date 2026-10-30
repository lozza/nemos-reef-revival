"""Asset-free backend and input-module checks for the frozen desktop runtime."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'),str(ROOT/'desktop')]


class DesktopGameplayContractTest(unittest.TestCase):
    def test_worlddef_is_nul_terminated_for_the_original_arm_parser(self):
        import nemo_server
        data = nemo_server.xor(nemo_server.encode_worlddef({'version':1}))
        self.assertTrue(data.endswith(b'\0'))
        self.assertEqual(json.loads(data[:-1]),{'version':1})

    def test_gameplay_contract_has_the_required_client_and_server_features(self):
        from gameplay_contract import verify
        result=verify()
        self.assertEqual(result['quest_counters'],'passed')
        self.assertEqual(result['fish_visibility'],'included')
        self.assertEqual(result['mouse_wheel_zoom'],'included')


if __name__ == '__main__':
    unittest.main()

