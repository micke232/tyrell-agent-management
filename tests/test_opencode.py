import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from tyrell.agent_setup import effective_config
from tyrell.local_http import HTTPError
from tyrell.service import Service
from tyrell.ui import Dashboard
from tyrell.worktrees import git


class OpenCodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s=Service(self.temp.name,['fixture'])
        self.r=self.s.opencode
        self.r.ensure=AsyncMock()
        self.r.call=AsyncMock(return_value={})
        self.r.status={'connected':True,'status':'connected','name':'OpenCode','models':[{'id':'local/shared','name':'Local / Shared'}]}

    async def asyncTearDown(self):
        await self.r.close()

    async def agent(self):
        result=await self.s.dispatch({'action':'create_agent','name':'OpenCode test','provider':'opencode','model':'local/shared'})
        return result['thread']

    async def test_creation_models_and_local_lifecycle_with_codex_offline(self):
        t=await self.agent()
        self.assertTrue(t['id'].startswith('opencode-'))
        self.assertFalse(self.s.state.connected)
        self.assertEqual(self.s.models_for(t)[0]['model'],'local/shared')
        await self.s.dispatch({'action':'rename','threadId':t['id'],'name':'Renamed'})
        await self.s.dispatch({'action':'archive','threadId':t['id']})
        self.assertIn(t['id'],(await self.s.dispatch({'action':'archives'}))['archived'])
        await self.s.dispatch({'action':'restore','threadId':t['id']})
        await self.s.dispatch({'action':'select','threadId':t['id']})
        self.assertEqual(self.s.state.thread(t['id'])['name'],'Renamed')

    async def test_send_uses_isolated_worktree_and_shared_plan_instructions(self):
        t=await self.agent()
        repo=Path(self.temp.name)/'repo';repo.mkdir()
        await git('init','-b','main',str(repo))
        await git('-C',str(repo),'-c','user.name=Fixture','-c','user.email=test@example.invalid','commit','--allow-empty','-m','initial')
        await self.s.dispatch({'action':'setup_import','path':str(repo),'target':'thread:'+t['id']})
        self.r.message=AsyncMock(return_value={})
        await self.s.dispatch({'action':'send','threadId':t['id'],'text':'Do work'})
        passed=self.r.message.call_args.args
        self.assertNotEqual(passed[0]['setupCwd'],str(repo))
        self.assertTrue(Path(passed[0]['setupCwd']).is_dir())
        self.assertIn('dashboard-progress',passed[3])
        self.assertIn('dashboard-worktree',passed[3])
        self.assertEqual(await git('-C',str(repo),'branch','--show-current'),'main')

    async def test_model_and_access_never_inherit_codex_engine_defaults(self):
        t=await self.agent()
        self.s.state.data['settings'].update(nextModel='codex-only',nextEffort='high',agentConfig={'fileAccess':'Full access','approvalMode':'Never'})
        config=effective_config(self.s.state.data,'thread:'+t['id'])
        self.assertEqual(config['model'],'local/shared')
        self.assertEqual(config['effort'],'')
        self.assertEqual(config['fileAccess'],'Keep current')
        self.assertEqual(config['opencodeAccess'],'Ask')
        with self.assertRaises(ValueError):
            await self.s.dispatch({'action':'agent_setup','scope':'agent','target':'thread:'+t['id'],'patch':{'fileAccess':'Full access'}})

    async def test_workspace_context_permissions_and_client_message_identity(self):
        t=await self.agent()
        t['setupCwd']=self.temp.name
        self.r.call.side_effect=[{'id':'ses_fixture'}, {}, None]
        await self.r.message(t,'Hello',{'model':'local/shared','opencodeAccess':'Ask'}, {'plan':{'value':'Keep a current plan'}},'client-fixture')
        calls=self.r.call.call_args_list
        self.assertEqual(calls[0].args[3],self.temp.name)
        self.assertEqual(calls[1].args[2]['permission'][0]['action'],'ask')
        self.assertEqual(calls[2].args[2]['model'],{'providerID':'local','modelID':'shared'})
        self.assertIn('Keep a current plan',calls[2].args[2]['system'])
        self.assertEqual(t['items'][0]['clientId'],'client-fixture')
        self.assertEqual(t['items'][0]['text'],'Hello')

    async def test_foreign_events_ignored_and_user_echo_not_rendered_as_agent(self):
        t=await self.agent()
        self.r.sessions['ses_fixture']=t['id']
        self.r.event({'type':'message.updated','properties':{'sessionID':'ses_fixture','info':{'id':'msg_user','role':'user'}}})
        self.r.event({'type':'message.part.updated','properties':{'sessionID':'ses_fixture','part':{'id':'part_user','messageID':'msg_user','type':'text','text':'Private system instructions'}}})
        self.r.event({'type':'message.part.delta','properties':{'sessionID':'foreign','partID':'unknown','messageID':'msg_other','field':'text','delta':'FOREIGN'}})
        self.assertFalse(t['items'])
        self.r.event({'type':'message.updated','properties':{'sessionID':'ses_fixture','info':{'id':'msg_agent','role':'assistant'}}})
        self.r.event({'type':'message.part.delta','properties':{'sessionID':'ses_fixture','partID':'part_agent','messageID':'msg_agent','field':'text','delta':'Visible reply'}})
        self.assertEqual(t['items'][0]['text'],'Visible reply')

    async def test_permissions_and_questions_route_to_opencode_not_codex(self):
        t=await self.agent();t['opencodeSession']='ses_fixture'
        self.r.sessions['ses_fixture']=t['id']
        self.r.event({'type':'permission.asked','properties':{'sessionID':'ses_fixture','id':'per_fixture','permission':'bash','patterns':['npm run lint']}})
        self.assertEqual(t['status']['activeFlags'],['waitingOnApproval'])
        self.s.clear_codex_requests()
        await self.s.dispatch({'action':'respond','requestId':'opencode-per_fixture','response':{'decision':'accept'}})
        self.assertEqual(self.r.call.call_args.args[1:3],('/permission/per_fixture/reply',{'reply':'once'}))
        self.r.event({'type':'question.asked','properties':{'sessionID':'ses_fixture','id':'que_fixture','questions':[{'question':'Which folder?','header':'Folder','options':[]}]}})
        self.assertEqual(t['status']['activeFlags'],['waitingOnUserInput'])
        await self.s.dispatch({'action':'respond','requestId':'opencode-que_fixture','response':{'answers':{'0':{'answers':['src']}}}})
        self.assertEqual(self.r.call.call_args.args[1:3],('/question/que_fixture/reply',{'answers':[['src']]}))
        self.assertFalse(self.s.state.requests)

    async def test_rejected_prompt_does_not_leave_a_permanently_working_agent(self):
        t=await self.agent();t.update(opencodeSession='ses_fixture',opencodeCwd=t['cwd'])
        self.r.call.side_effect=[{},HTTPError(400)]
        with self.assertRaises(HTTPError):
            await self.r.message(t,'Hello',{'model':'local/shared','opencodeAccess':'Ask'},{})
        self.assertEqual(t['lastTurnStatus'],'failed')
        self.assertEqual(len(self.r.call.call_args_list),2)

    async def test_uncertain_delivery_is_not_automatically_retried(self):
        t=await self.agent();t.update(opencodeSession='ses_fixture',opencodeCwd=t['cwd'])
        self.r.call.side_effect=[{},OSError('connection closed')]
        with self.assertRaises(OSError):
            await self.r.message(t,'Hello',{'model':'local/shared','opencodeAccess':'Ask'},{})
        self.assertIn('unconfirmed',t['activity'])
        self.assertEqual(len(self.r.call.call_args_list),2)

    async def test_setup_and_settings_expose_separate_opencode_controls(self):
        t=await self.agent()
        ui=Dashboard(Path(self.temp.name),{'connected':False,'threads':{t['id']:t},'tasks':[],'providers':self.s.providers()})
        ui.selected='thread:'+t['id'];ui.setup.scope='agent';ui.setup.expanded.add('Access')
        ui.setup.build_rows(ui)
        self.assertTrue(ui.provider_connected(t))
        ui.hub_action('3')
        self.assertIn('OpenCode',ui.panel)
        self.assertIn('own API keys',ui.panel)
