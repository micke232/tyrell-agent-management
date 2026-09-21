import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.install import install


class InstallTests(unittest.TestCase):
    def test_idempotent_install_preserves_codex_and_shell_config(self):
        with tempfile.TemporaryDirectory(prefix="cdx-install-") as directory:
            home = Path(directory)
            original = "# User settings\nexport DASHBOARD_INSTALL_TEST=kept\n"
            (home / ".zshrc").write_text(original)
            bin_dir = home / ".local/bin"
            bin_dir.mkdir(parents=True)
            codex = bin_dir / "codex"
            codex.write_text('#!/bin/sh\nprintf "original:%s\\n" "$*"\n')
            codex.chmod(0o755)
            with contextlib.redirect_stdout(io.StringIO()):
                for _ in range(2):
                    install(home, Path(__file__).resolve().parents[1], "/usr/bin/python3")
            config = (home / ".zshrc").read_text()
            self.assertTrue(config.startswith(original))
            self.assertEqual(config.count("source "), 1)
            self.assertEqual(len(list((home / ".codex-dashboard").glob("zshrc.backup.*"))), 1)
            env = dict(os.environ, HOME=str(home), PATH="/usr/bin:/bin")
            env.pop("ZDOTDIR", None)
            output = subprocess.check_output(["/bin/zsh", "-ic", 'codex resume session-id'], env=env).decode()
            self.assertEqual(output.strip(), "original:resume session-id")
            help_output = subprocess.check_output(["/bin/zsh", "-ic", 'codex dashboard --help'], env=env).decode()
            self.assertIn("Tyrell Agent Management", help_output)
            self.assertEqual(subprocess.check_output([str(bin_dir / "tyrell"), "--help"]).decode(), help_output)
            path_output = subprocess.check_output(["/bin/zsh", "-ic", 'source "$1"; print -r -- "$PATH"', "test", str(home / ".codex-dashboard/shell.zsh")], env=env).decode()
            self.assertEqual(path_output.strip().split(":" ).count(str(bin_dir)), 1)
