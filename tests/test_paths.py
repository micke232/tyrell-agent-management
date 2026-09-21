import tempfile
import unittest
from pathlib import Path
from tyrell.paths import state_directory


class StatePathTests(unittest.TestCase):
    def test_new_install_uses_tyrell_and_existing_install_keeps_its_data(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(state_directory({}, home), str(home/'.tyrell'))
            legacy = home/'.codex-dashboard'
            legacy.mkdir()
            (legacy/'state.json').write_text('{"saved": true}')
            self.assertEqual(state_directory({}, home), str(legacy))
            self.assertEqual((legacy/'state.json').read_text(), '{"saved": true}')
            self.assertFalse((home/'.tyrell').exists())

    def test_explicit_new_override_wins_and_legacy_override_is_supported(self):
        self.assertEqual(state_directory({'TYRELL_HOME': '/tmp/new', 'CODEX_DASHBOARD_HOME': '/tmp/old'}), '/tmp/new')
        self.assertEqual(state_directory({'CODEX_DASHBOARD_HOME': '/tmp/old'}), '/tmp/old')
