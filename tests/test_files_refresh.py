import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tyrell.service import Service
from tyrell.worktrees import git
from tyrell.ui import Dashboard


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
            with patch.object(s,'dispatch',side_effect=dispatch), patch('tyrell.service.asyncio.sleep',side_effect=sleep):
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

    async def test_committed_feature_changes_remain_visible_and_main_stays_clean(self):
        from tyrell.workspace_changes import changes
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)/'repo'; repo.mkdir()
            await git('init', '-b', 'main', str(repo))
            await git('-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
            base = await git('-C', str(repo), 'rev-parse', 'HEAD')
            await git('-C', str(repo), 'switch', '-c', 'feature/test')
            (repo/'committed.txt').write_text('committed work')
            await git('-C', str(repo), 'add', '.')
            await git('-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'work')
            self.assertEqual(await git('-C', str(repo), 'status', '--porcelain'), '')
            records = await changes(repo)
            self.assertIn(str((repo/'committed.txt').resolve()), records)
            self.assertEqual(records.base, base)
            self.assertEqual(await changes(repo, 'HEAD'), {})
            await git('-C', str(repo), 'switch', 'main')
            self.assertEqual(await changes(repo), {})

    async def test_setup_detects_git_initialized_after_folder_import_without_changing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)/'repo'; repo.mkdir()
            service = Service(Path(directory)/'state', ['unused'])
            path = str(repo)
            service.state.data.setdefault('projectProfiles', {})[path] = {'path': path, 'gitRoot': None,
                'config': {'baseBranch': 'custom-choice', 'runTests': False}}
            await service.refresh_setup_git(path)
            self.assertIsNone(service.state.data['projectProfiles'][path]['gitRoot'])
            await git('init', '-b', 'main', path)
            await git('-C', path, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
            await service.refresh_setup_git(path)
            profile = service.state.data['projectProfiles'][path]
            self.assertEqual(Path(profile['gitRoot']).resolve(), repo.resolve())
            self.assertIn('main', profile['baseBranches'])
            self.assertEqual(profile['config'], {'baseBranch': 'custom-choice', 'runTests': False})

    async def test_large_change_set_retains_first_page_instead_of_becoming_empty(self):
        from tyrell.workspace_changes import changes
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            await git('init', '-b', 'main', directory)
            await git('-C', directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
            for index in range(251):
                (repo/('%03d.txt' % index)).write_text('new')
            records = await changes(repo)
            self.assertEqual(len(records), 250)
            self.assertTrue(records.truncated)
            self.assertEqual(records.total, 251)
