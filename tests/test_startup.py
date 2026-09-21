import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from tyrell.cli import prepare_dashboard
from tyrell.startup import Startup, provider_status


class StartupTests(unittest.TestCase):
    def test_failed_work_does_not_print_success(self):
        output = io.StringIO()
        startup = Startup(output)
        def action():
            self.assertNotIn('OK', output.getvalue())
            raise RuntimeError('offline')
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            startup.step('Connecting', action)
        self.assertIn('FAILED', output.getvalue())
        self.assertNotIn('\x1b', output.getvalue())

    def test_partial_and_disconnected_providers_are_not_reported_as_ready(self):
        self.assertEqual(provider_status({'providers': {'codex': {'connected': True}}}), 'OK')
        self.assertIn('1/2 CONNECTED', provider_status({'providers': {
            'codex': {'connected': True}, 'copilot': {'connected': False}}}))
        self.assertIn('WAITING', provider_status({'providers': {}}))

    def test_initialization_restores_snapshot_without_starting_turns_or_noninteractive_sleep(self):
        for connected in (False, True):
            snapshot = {'threads': {}, 'tasks': [], 'providers': {'codex': {'connected': connected}}}
            output = io.StringIO()
            with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output), \
                    patch('tyrell.cli.ensure_service') as ensure, \
                    patch('tyrell.cli.request', return_value=snapshot) as request, \
                    patch('time.sleep', side_effect=AssertionError('Artificial delay')):
                ui = prepare_dashboard(directory, 'fixture', False)
            ensure.assert_called_once_with(directory, 'fixture')
            request.assert_called_once_with(directory, 'snapshot')
            self.assertIs(ui.data, snapshot)
            self.assertFalse(ui.demo)
            self.assertEqual(ui.panel, None if connected else 'HUB SETTINGS')
            self.assertIn('INTERFACE 2037\nREADY FOR INQUIRY', output.getvalue())
            self.assertIn('SYSTEM INITIALIZATION\nPRIMARY PROCESSOR ONLINE\nMEMORY CORE VERIFIED\nNAVIGATION INTERFACE ACTIVE\nENVIRONMENTAL CONTROL ONLINE\n', output.getvalue())
            self.assertEqual('PROVIDERS: WAITING' in output.getvalue(), not connected)

    def test_color_is_muted_and_reset_and_respects_no_color(self):
        output = io.StringIO()
        output.isatty = lambda: True
        with patch.dict('os.environ', {'TERM': 'xterm-256color'}, clear=True):
            Startup(output).begin()
        self.assertIn('\x1b[38;5;108m', output.getvalue())
        self.assertTrue(output.getvalue().endswith('\x1b[0m'))
        output = io.StringIO()
        output.isatty = lambda: True
        with patch.dict('os.environ', {'NO_COLOR': ''}):
            Startup(output).begin()
        self.assertNotIn('\x1b', output.getvalue())
