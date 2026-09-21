#!/usr/bin/env python3
"""Opt-in OpenCode smoke test with a local fake model, never a cloud model.

Usage: python3 scripts/check_opencode.py [path/to/opencode]
The selected CLI must already be installed. All session/config data is temporary.
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import asyncio,json,os,tempfile,time
from pathlib import Path
from unittest.mock import patch
from tyrell.service import Service

async def main():
 requests=[]
 async def model(reader,writer):
  try:
   head=await reader.readuntil(b'\r\n\r\n');headers={k.lower():v.strip() for k,v in (line.split(b':',1) for line in head.split(b'\r\n')[1:] if b':' in line)}
   body=json.loads(await reader.readexactly(int(headers.get(b'content-length',b'0'))));requests.append(body)
   messages=body.get('messages',[])
   tool_done=any(m.get('role')=='tool' for m in messages)
   if tool_done:
    chunks=[{'role':'assistant'}, {'content':'Plan:\n- [x] Run fixture command\n\n'}, {'content':'OpenCode fixture completed.'}];finish='stop'
   else:
    chunks=[{'role':'assistant'}, {'tool_calls':[{'index':0,'id':'call_fixture','type':'function','function':{'name':'bash','arguments':json.dumps({'command':'printf opencode-fixture','description':'Print local fixture text'})}}]}];finish='tool_calls'
   writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n');await writer.drain()
   for delta in chunks:
    data={'id':'chatcmpl-fixture','object':'chat.completion.chunk','created':int(time.time()),'model':'fixture','choices':[{'index':0,'delta':delta,'finish_reason':None}]}
    writer.write(('data: '+json.dumps(data)+'\n\n').encode());await writer.drain();await asyncio.sleep(.08)
   data={'id':'chatcmpl-fixture','object':'chat.completion.chunk','created':int(time.time()),'model':'fixture','choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
   writer.write(('data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n').encode());await writer.drain()
  finally:writer.close();await writer.wait_closed()
 server=await asyncio.start_server(model,'127.0.0.1',0)
 port=server.sockets[0].getsockname()[1]
 with tempfile.TemporaryDirectory(prefix='tyrell-oc-full-') as root:
  config={'model':'fixture/fixture','enabled_providers':['fixture'],'provider':{'fixture':{'npm':'@ai-sdk/openai-compatible','options':{'baseURL':f'http://127.0.0.1:{port}/v1','apiKey':'fixture'},'models':{'fixture':{'name':'Fixture'}}}}}
  with patch.dict(os.environ,{'TYRELL_OPENCODE':(sys.argv[1] if len(sys.argv)>1 else 'opencode'),'XDG_DATA_HOME':root+'/source','XDG_CONFIG_HOME':root+'/config','XDG_CACHE_HOME':root+'/cache','OPENCODE_CONFIG_CONTENT':json.dumps(config)}):
   s=Service(Path(root)/'hub',['unused']);r=s.opencode
   try:
    await r.ensure()
    catalog=await r.call('GET','/provider',cwd=root)
    assert 'fixture' in catalog['connected']
    assert any(p['id']=='fixture' and 'fixture' in p['models'] for p in catalog['all'])
    t=s.state.thread('opencode-fixture');t.update(managed=True,provider='opencode',name='Fixture',cwd=root)
    await r.message(t,'Run the fixture command, then reply.',{'model':'fixture/fixture','opencodeAccess':'Ask'},{'fixture':{'value':'Use bash to run printf opencode-fixture, then finish.'}})
    async def permission():
     while not s.state.requests:await asyncio.sleep(.05)
    await asyncio.wait_for(permission(),20)
    assert t['status']['activeFlags']==['waitingOnApproval'],t
    await r.respond(next(iter(s.state.requests.values())),{'decision':'accept'})
    async def done():
     while t.get('turnId'):await asyncio.sleep(.05)
    await asyncio.wait_for(done(),20)
    answers=[i['text'] for i in t['items'] if i['type']=='agentMessage']
    assert any('OpenCode fixture completed.' in x for x in answers),answers
    assert t['plan'] and t['plan'][0]['status']=='completed',t['plan']
    assert not s.state.requests
    assert len(requests)==2,len(requests)
    await r.stop_transport()
    await r.ensure()
    restored=await r.call('GET','/session/'+t['opencodeSession'],cwd=root)
    assert restored['permission'][0]['action']=='ask'
    assert any('OpenCode fixture completed.' in i['text'] for i in t['items'] if i['type']=='agentMessage')
    print('PASS real OpenCode + local fake model: streamed response, tool approval, command output, plan, completion and session restart; no cloud model calls.')
   finally:await r.close()
 server.close();await server.wait_closed()
asyncio.run(main())
