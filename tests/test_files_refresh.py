import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_dashboard.service import Service
from codex_dashboard.worktrees import git
from codex_dashboard.ui import Dashboard


class FilesRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_completion_collects_actual_git_changes_and_removes_reverted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'; repo.mkdir()
            await git('init', '-b', 'main', str(repo))
            await git('-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
            s = Service(Path(directory)/'state', ['unused'])
            t = s.state.thread('fixture'); t.update(cwd=str(repo), setupCwd=str(repo))
            (repo/'new.txt').write_text('new file')
            s.codex_event('turn/started', {'threadId':'fixture', 'turn':{'id':'turn'}})
            s.codex_event('turn/completed', {'threadId':'fixture', 'turn':{'id':'turn','status':'completed'}})
            await s.copilot_runtime.diff_jobs['fixture']
            path = str((repo/'new.txt').resolve())
            self.assertIn(path,t['changedFiles'])
            self.assertEqual(t['filesSource'],'git')
            (repo/'new.txt').unlink()
            await s.copilot_runtime.collect_changes('fixture')
            self.assertEqual(t['changedFiles'],{})
            await s.copilot_runtime.close()

    async def test_custom_folder_refresh_is_delivered_to_matching_files_view_only(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Service(Path(directory)/'state',['unused']); s.stop=asyncio.Event()
            s.files_requested=('fixture',directory,time.monotonic())
            async def dispatch(req):
                s.stop.set()
                return {'root':directory,'records':{'new':{'path':'new'}},'error':''}
            async def sleep(_): pass
            with patch.object(s,'dispatch',side_effect=dispatch), patch('codex_dashboard.service.asyncio.sleep',side_effect=sleep):
                await s.monitor_files()
            ui=Dashboard(directory,{'threads':{'fixture':{'id':'fixture'}},'tasks':[]})
            ui.selected='thread:fixture';ui.files.locations[ui.selected]={'root':directory,'records':{}}
            ui.updates.put(('snapshot',{'threads':{'fixture':{'id':'fixture'}},'filesBrowser':s.files_browser}))
            ui.update()
            self.assertIn('new',ui.files.locations[ui.selected]['records'])
            ui.files.locations[ui.selected]={'root':'/different','records':{}}
            ui.updates.put(('snapshot',{'threads':{'fixture':{'id':'fixture'}},'filesBrowser':s.files_browser}))
            ui.update()
            self.assertEqual(ui.files.locations[ui.selected]['records'],{})
