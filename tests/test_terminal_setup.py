import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from codex_dashboard.terminal_setup import setup_terminal, terminal_info


class TerminalSetupTests(unittest.TestCase):
    def run_setup(self, info, answer='2', result=0):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch('codex_dashboard.terminal_setup.terminal_info', return_value=info), patch('builtins.input', return_value=answer), patch('codex_dashboard.terminal_setup.subprocess.run', return_value=Mock(returncode=result)) as run, contextlib.redirect_stdout(io.StringIO()):
            choice = setup_terminal(directory.name, interactive=True)
        return choice, run, Path(directory.name)

    def test_existing_ghostty_is_reused_without_installer(self):
        choice, run, directory = self.run_setup({'ghostty':'/Applications/Ghostty.app/Contents/MacOS/ghostty','current':'Apple_Terminal','installer':'/opt/homebrew/bin/brew'},'1')
        self.assertEqual(choice,'ghostty');run.assert_not_called()
        self.assertEqual(json.loads((directory/'terminal.json').read_text())['preference'],'ghostty')

    def test_current_terminal_and_empty_choice_never_install(self):
        for answer in ('2',''):
            choice, run, _ = self.run_setup({'ghostty':None,'current':'Apple_Terminal','installer':'/opt/homebrew/bin/brew'},answer)
            self.assertEqual(choice,'current');run.assert_not_called()

    def test_install_only_after_explicit_choice_and_check_result(self):
        missing={'ghostty':None,'current':'Apple_Terminal','installer':'/opt/homebrew/bin/brew'}
        installed={**missing,'ghostty':'/Applications/Ghostty.app/Contents/MacOS/ghostty'}
        with tempfile.TemporaryDirectory() as d, patch('codex_dashboard.terminal_setup.terminal_info',side_effect=[missing,installed]), patch('builtins.input',return_value='3'), patch('codex_dashboard.terminal_setup.subprocess.run',return_value=Mock(returncode=0)) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup_terminal(d,interactive=True),'ghostty')
            run.assert_called_once_with(['/opt/homebrew/bin/brew','install','--cask','ghostty'],check=False)

    def test_noninteractive_setup_never_installs_or_prompts(self):
        with tempfile.TemporaryDirectory() as d, patch('builtins.input') as read, patch('codex_dashboard.terminal_setup.subprocess.run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(setup_terminal(d,interactive=False))
            read.assert_not_called();run.assert_not_called()
            self.assertFalse((Path(d)/'terminal.json').exists())

    def test_missing_homebrew_offers_download_without_installing_homebrew(self):
        choice, run, _ = self.run_setup({'ghostty':None,'current':'Apple_Terminal','installer':None},'1')
        self.assertEqual(choice,'current');run.assert_not_called()

    def test_install_failure_is_not_saved_as_success(self):
        with tempfile.TemporaryDirectory() as d, patch('codex_dashboard.terminal_setup.terminal_info',return_value={'ghostty':None,'current':'Apple_Terminal','installer':'/brew'}), patch('builtins.input',return_value='3'), patch('codex_dashboard.terminal_setup.subprocess.run',return_value=Mock(returncode=1)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'did not complete'):setup_terminal(d,interactive=True)
            self.assertFalse((Path(d)/'terminal.json').exists())
