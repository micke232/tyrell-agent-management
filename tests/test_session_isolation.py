import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tyrell.codex_storage import environment
from tyrell.rpc import RpcError
from tyrell.paths import reuse_credentials
from tyrell.service import Service


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = Service(self.root / 'hub', ['fixture'])
        self.service.state.thread('owned')['managed'] = True
        self.service.state.thread('ide').update(status={'type':'active'})
        self.service.rpc = AsyncMock()
        self.service.rpc.call.return_value = {'data': []}

    async def test_foreign_sessions_stay_out_of_snapshot_events_requests_and_refresh(self):
        s = self.service
        s.codex_event('thread/started', {'thread': {'id':'another-ide'}})
        s.codex_event('turn/started', {'threadId':'ide', 'turn':{'id':'foreign-turn'}})
        s.on_request({'id':'foreign-request','method':'item/commandExecution/requestApproval','params':{'threadId':'ide'}})
        s.rpc.call.return_value = {'data':[{'id':'another-ide','status':{'type':'active'}}]}
        s.subscribed.add('owned')
        await s.refresh()
        snapshot = await s.dispatch({'action':'snapshot','full':True})
        self.assertEqual(set(snapshot['threads']), {'owned'})
        self.assertNotIn('another-ide', s.state.data['threads'])
        self.assertNotIn('turnId', s.state.data['threads']['ide'])
        self.assertFalse(s.state.requests)
        self.assertEqual([call.args[0] for call in s.rpc.call.call_args_list], ['thread/list'])
        with self.assertRaisesRegex(ValueError, 'another application'):
            await s.subscribe('ide')

    def test_only_owned_parents_can_introduce_subagents(self):
        s = self.service
        s.codex_event('thread/started', {'thread': {'id':'own-child','parentThreadId':'owned'}})
        s.codex_event('thread/started', {'thread': {'id':'ide-child','parentThreadId':'ide'}})
        self.assertTrue(s.owns_thread('own-child'))
        self.assertNotIn('ide-child', s.state.data['threads'])
        s.state.thread('old-child')['parentThreadId']='owned'
        self.assertTrue(s.owns_thread('old-child'))
        s.state.thread('cycle')['parentThreadId']='cycle'
        self.assertFalse(s.owns_thread('cycle'))

    async def test_archive_catalog_never_imports_foreign_sessions(self):
        s = self.service
        s.rpc.call.return_value = {'data':[{'id':'ide-archived'}, {'id':'owned'}]}
        result = await s.archives()
        self.assertEqual(set(result['archived']), {'owned'})

    def test_only_configuration_is_shared_not_session_storage(self):
        source = self.root / 'ide-home'; source.mkdir()
        for name in ('config.toml','auth.json','state_5.sqlite'):
            (source/name).write_text('fixture')
        (source/'sessions').mkdir()
        with patch.dict(os.environ, {'CODEX_HOME': str(source)}):
            env, private = environment(self.root/'hub')
            self.assertEqual(os.environ['CODEX_HOME'], str(source))
        self.assertEqual(env['CODEX_HOME'], str(private))
        self.assertTrue((private/'auth.json').is_symlink())
        self.assertFalse((private/'sessions').exists())
        self.assertFalse((private/'state_5.sqlite').exists())

    def test_new_signin_replaces_private_auth_after_atomic_cli_refresh(self):
        source, target = self.root/'source-auth', self.root/'private-auth'
        source.write_text('old fixture')
        reuse_credentials(source, target)
        self.assertTrue(target.is_symlink())
        target.unlink();target.write_text('private refresh fixture')
        os.utime(target, ns=(1,1))
        source.write_text('new sign-in fixture')
        reuse_credentials(source, target)
        self.assertEqual(target.read_text(),'new sign-in fixture')
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        self.assertEqual(source.read_text(),'new sign-in fixture')

    async def test_pending_migration_never_creates_new_shared_codex_agents(self):
        s = self.service
        s.private_codex = True
        s.using_private = False
        s.state.connected = True
        with self.assertRaisesRegex(RpcError, 'finish'):
            await s.dispatch({'action':'create_agent','provider':'codex','name':'New agent'})
        s.rpc.call.assert_not_awaited()
        self.assertEqual(s.providers()['codex']['sessionStorage'],'migrationPending')

    async def test_migration_checks_active_before_copying_or_archiving(self):
        s = self.service
        s.private_codex = True
        s.codex_home = self.root/'private'
        shared = AsyncMock()
        shared.call.return_value = {'thread': {'id':'owned','status':{'type':'active'}}}
        with patch('tyrell.service.Rpc', return_value=shared):
            with self.assertRaisesRegex(RpcError, 'finish'):
                await s.migrate_codex_sessions()
        self.assertEqual([c.args[0] for c in shared.call.call_args_list], ['thread/read'])
        s.rpc.call.assert_not_awaited()
        shared.close.assert_awaited_once()

    async def test_migration_preserves_original_and_verifies_copy_before_archive(self):
        s = self.service
        s.private_codex = True
        s.codex_home = self.root/'private'
        source = self.root/'rollout.jsonl'; source.write_text('saved history')
        shared = AsyncMock()
        shared.call.return_value = {'thread': {'id':'owned','path':str(source),'status':{'type':'idle'}}}
        s.rpc.call.return_value = {'thread': {'id':'owned'}}
        with patch('tyrell.service.Rpc', return_value=shared):
            await s.migrate_codex_sessions()
        self.assertEqual(source.read_text(), 'saved history')
        self.assertEqual((s.codex_home/'sessions'/source.name).read_text(), 'saved history')
        self.assertEqual([c.args[0] for c in shared.call.call_args_list], ['thread/read','thread/archive'])
        self.assertEqual(s.state.thread('owned')['codexStorage'], 'private')

    async def test_failed_migration_never_archives_original(self):
        s = self.service
        s.private_codex = True
        s.codex_home = self.root/'private'
        source = self.root/'rollout.jsonl'; source.write_text('saved history')
        shared = AsyncMock()
        shared.call.return_value = {'thread': {'id':'owned','path':str(source),'status':{'type':'idle'}}}
        s.rpc.call.side_effect = RpcError('resume failed')
        with patch('tyrell.service.Rpc', return_value=shared):
            with self.assertRaises(RpcError):
                await s.migrate_codex_sessions()
        self.assertEqual([c.args[0] for c in shared.call.call_args_list], ['thread/read'])
        self.assertNotIn('codexStorage', s.state.thread('owned'))
