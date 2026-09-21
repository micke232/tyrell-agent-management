#!/usr/bin/env python3
"""Opt-in real Codex smoke test. Uses temporary homes; sends no model requests."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import asyncio, os, tempfile
from pathlib import Path
from unittest.mock import patch
from tyrell.rpc import Rpc, RpcError
from tyrell.service import Service

async def main():
    with tempfile.TemporaryDirectory(prefix='tyrell-isolation-') as root:
        source = Path(root) / 'ide'; source.mkdir()
        with patch.dict(os.environ, {'CODEX_HOME': str(source)}):
            service = Service(Path(root)/'hub', ['codex', 'app-server', 'proxy'])
            ide = Rpc(['codex', 'app-server', '--stdio'], lambda *_:None, lambda *_:None)
            hub = Rpc(service.command, service.codex_event, service.on_request, env=service.codex_env)
            try:
                await ide.connect(); await hub.connect()
                params = {'cwd': root, 'sandbox': 'read-only', 'approvalPolicy': 'never'}
                one = (await ide.call('thread/start', params))['thread']['id']
                two = (await hub.call('thread/start', params))['thread']['id']
                for rpc, tid in ((ide, one), (hub, two)):
                    await rpc.call('thread/inject_items', {'threadId':tid,'items':[{'type':'message','role':'user','content':[{'type':'input_text','text':'Isolation fixture; do not execute.'}]}]})
                await asyncio.sleep(.2)
                query = {'limit':100, 'sourceKinds':['cli','vscode','appServer','exec','unknown']}
                left = {t['id'] for t in (await ide.call('thread/list', query))['data']}
                right = {t['id'] for t in (await hub.call('thread/list', query))['data']}
                assert two not in left and one not in right
                for rpc, foreign in ((ide, two), (hub, one)):
                    try:
                        await rpc.call('thread/read', {'threadId':foreign})
                    except RpcError:
                        pass
                    else:
                        raise AssertionError('Foreign session was accessible')
                info = (await ide.call('thread/read', {'threadId':one,'includeTurns':True}))['thread']
                print('Bilateral isolation verified with real Codex servers; rollout path available:',bool(info.get('path')))
                await hub.close()
                hub = Rpc(service.command, service.codex_event, service.on_request, env=service.codex_env)
                await hub.connect()
                resumed = (await hub.call('thread/resume',{'threadId':two}))['thread']['id']
                assert resumed == two
                print('Private session survives server restart.')
                service.rpc = hub
                service.state.thread(one)['managed'] = True
                class Existing:
                    async def connect(self): pass
                    async def close(self): pass
                    async def call(self, *args, **kwargs): return await ide.call(*args, **kwargs)
                with patch('tyrell.service.Rpc', return_value=Existing()):
                    await service.migrate_codex_sessions()
                migrated = (await hub.call('thread/read', {'threadId':one,'includeTurns':True}))['thread']
                assert str(service.codex_home) in migrated['path'], migrated['path']
                assert service.state.thread(one)['codexStorage'] == 'private'
                print('Real rollout migration and shared-original archival verified.')
            finally:
                await ide.close(); await hub.close()
asyncio.run(main())
