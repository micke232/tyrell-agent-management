"""OpenCode 1.x HTTP/SSE adapter with provider-owned sessions and permissions."""
import asyncio
import contextlib
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from urllib.parse import quote, urlencode
import uuid

from .local_http import LocalHTTP, HTTPError
from .provider_runtime import ProviderRuntime
from .rpc import RpcError
from .paths import reuse_credentials


def opencode_executable():
    configured = os.environ.get('TYRELL_OPENCODE')
    for value in ([configured] if configured else ['opencode', str(Path.home()/'.opencode/bin/opencode')]):
        if value and (found := shutil.which(os.path.expanduser(value))):
            return found
    return None


class OpenCodeRuntime(ProviderRuntime):
    def __init__(self, state, directory, on_request):
        self.state, self.directory, self.on_request = state, Path(directory), on_request
        self.process = self.http = self.reader_task = None
        self.sessions, self.roles, self.part_types = {}, {}, {}
        self.background, self.diff_again = set(), set()
        self.diff_jobs = {}
        self.start_lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.auth_signature = None
        self.status = {'name':'OpenCode','status':'connecting','connected':False,'models':[]}

    def update(self, status, **details):
        self.status = {'name':'OpenCode','status':status,'connected':status=='connected','models':[],**details}

    def environment(self):
        root = self.directory/'providers/opencode'
        data = root/'data'
        auth = data/'opencode/auth.json'
        auth.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'opencode/auth.json'
        reuse_credentials(source, auth)
        return {**os.environ, 'XDG_DATA_HOME':str(data), 'XDG_STATE_HOME':str(root/'state'),
                'OPENCODE_SERVER_USERNAME':'opencode','OPENCODE_SERVER_PASSWORD':secrets.token_urlsafe(32),
                'OPENCODE_DISABLE_AUTOUPDATE':'true'}

    async def ensure(self):
        async with self.start_lock:
            if self.process and self.process.returncode is None and self.reader_task and not self.reader_task.done():
                return
            await self.stop_transport()
            executable = opencode_executable()
            if not executable:
                self.update('missing')
                raise RpcError('OpenCode CLI is not installed')
            env = self.environment()
            self.process = await asyncio.create_subprocess_exec(executable,'serve','--hostname','127.0.0.1','--port','0','--pure',
                cwd=str(self.directory),env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:
                async def address():
                    while line := await self.process.stdout.readline():
                        match = re.search(rb'http://127\.0\.0\.1:(\d+)',line)
                        if match:
                            return int(match[1])
                    raise RpcError('OpenCode server exited during startup')
                port = await asyncio.wait_for(address(),20)
                self.http = LocalHTTP(port,env['OPENCODE_SERVER_PASSWORD'])
                health = await self.http.request('GET','/global/health')
                if not health.get('healthy') or not str(health.get('version','')).startswith('1.'):
                    raise RpcError('This integration requires OpenCode CLI 1.x')
                schema = await self.http.request('GET','/doc')
                required = {'/global/event', '/session/{sessionID}/prompt_async', '/session/{sessionID}/todo', '/permission/{requestID}/reply', '/question/{requestID}/reply'}
                if not required.issubset(schema.get('paths',{})):
                    raise RpcError('OpenCode does not expose the required session API')
                self.version = health['version']
                self.sessions = {t['opencodeSession']:t['id'] for t in self.state.data['threads'].values()
                                 if t.get('provider')=='opencode' and t.get('opencodeSession')}
                # Restore absolute message parts before opening the stream, avoiding replayed deltas.
                for tid in list(self.sessions.values()):
                    await self.resync(tid)
                self.ready.clear()
                self.reader_task = asyncio.create_task(self.read_events())
                await asyncio.wait_for(self.ready.wait(),15)
            except BaseException:
                await self.stop_transport()
                raise

    async def call(self, method, path, body=None, cwd=None):
        separator = '&' if '?' in path else '?'
        return await self.http.request(method, path + (separator + urlencode({'directory':cwd}) if cwd else ''), body)

    def credentials_signature(self):
        data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
        config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'opencode'
        result=[]
        for path in (data/'opencode/auth.json', config/'opencode.json', config/'opencode.jsonc'):
            try:
                stat=path.stat()
                result.append((stat.st_mtime_ns,stat.st_size))
            except OSError:
                result.append(None)
        return tuple(result)

    async def run(self, stop):
        while not stop.is_set():
            try:
                signature = self.credentials_signature()
                active = any(t.get('provider')=='opencode' and t.get('status',{}).get('type')=='active' for t in self.state.data['threads'].values())
                if signature != self.auth_signature and not active:
                    await self.stop_transport()
                    self.auth_signature = signature
                await self.ensure()
                result = await self.call('GET','/provider',cwd=str(self.directory))
                connected = set(result.get('connected',[]))
                models = []
                for provider in result.get('all',[]):
                    if provider['id'] in connected:
                        for key, model in provider.get('models',{}).items():
                            models.append({'id':provider['id']+'/'+key,'name':provider['id']+' / '+model.get('name',key)})
                self.update('connected' if models else 'signin',models=models,version=self.version)
            except (OSError, ValueError, KeyError, RpcError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                self.update('offline' if opencode_executable() else 'missing')
            try:
                await asyncio.wait_for(stop.wait(),15)
            except asyncio.TimeoutError:
                pass

    async def read_events(self):
        try:
            async for envelope in self.http.events(self.ready):
                self.event(envelope.get('payload',envelope))
        except (OSError, ValueError, KeyError, RpcError, asyncio.IncompleteReadError):
            pass
        finally:
            self.update('offline')
            for tid in self.sessions.values():
                t = self.state.thread(tid)
                if t.get('status',{}).get('type')=='active':
                    t['activity'] = 'OpenCode connection lost; the prompt will not be resent'
                    self.finish(tid,'interrupted')

    def event(self, event):
        kind, p = event.get('type'), event.get('properties',event.get('data',{}))
        sid = p.get('sessionID') or p.get('info',{}).get('sessionID') or p.get('part',{}).get('sessionID')
        tid = self.sessions.get(sid)
        if not tid:
            return
        t = self.state.thread(tid)
        if kind == 'message.updated':
            info = p['info']
            self.roles[info['id']] = info.get('role')
            if info.get('error'):
                self.failure(tid)
        elif kind == 'message.part.updated':
            self.part(tid,p['part'])
        elif kind == 'message.part.delta':
            if self.roles.get(p.get('messageID'))=='assistant' and p.get('field')=='text':
                method = 'item/reasoning/summaryTextDelta' if self.part_types.get(p['partID'])=='reasoning' else 'item/agentMessage/delta'
                self.emit(tid,method,itemId=p['partID'],delta=p.get('delta',''))
        elif kind == 'todo.updated':
            self.emit(tid,'turn/plan/updated',plan=[{'step':v['content'],'status':{'in_progress':'inProgress','completed':'completed','cancelled':'completed'}.get(v['status'],'pending')} for v in p.get('todos',[])])
        elif kind in ('permission.asked','question.asked'):
            rid = 'opencode-'+p['id']
            params = {'threadId':tid}
            question = kind=='question.asked'
            if question:
                params['questions'] = [{'id':str(i),'header':q.get('header','OpenCode'),'question':q['question'],
                    'options':q.get('options',[])} for i,q in enumerate(p['questions'])]
            else:
                params.update(command=p.get('permission','Permission')+': '+', '.join(p.get('patterns',[])),
                              availableDecisions=['accept','cancel'],reason='OpenCode requests permission for this operation')
            self.on_request({'id':rid,'provider':'opencode','wireId':p['id'],'kind':'question' if question else 'permission',
                'method':'item/tool/requestUserInput' if question else 'item/commandExecution/requestApproval','params':params})
        elif kind in ('permission.replied','question.replied','question.rejected'):
            self.state.requests.pop('opencode-'+str(p.get('requestID')),None)
            self.refresh_waiting(tid)
        elif kind == 'session.status':
            status = p['status']['type']
            if status=='idle':
                self.finish(tid,'completed')
            elif status in ('busy','retry'):
                if not t.get('turnId'):
                    self.emit(tid,'turn/started',turn={'id':str(uuid.uuid4())})
                if status=='retry':
                    t['activity']='OpenCode is retrying the model request'
                self.refresh_waiting(tid)
        elif kind == 'session.idle':
            self.finish(tid,'completed')
        elif kind == 'session.error':
            self.failure(tid)

    def failure(self, tid):
        self.finish(tid,'failed')
        self.emit(tid,'item/completed',item={'id':str(uuid.uuid4()),'type':'agentMessage',
            'text':'OpenCode could not complete this turn. Check the selected model and its account/API access in OpenCode. The prompt was not retried by Tyrell.'})

    def part(self, tid, part):
        iid, kind = part['id'],part.get('type')
        self.part_types[iid] = kind
        if self.roles.get(part.get('messageID'))!='assistant':
            return
        if kind in ('text','reasoning'):
            self.emit(tid,'item/completed',item={'id':iid,'type':'agentMessage' if kind=='text' else 'reasoning','text':part.get('text','')})
        elif kind=='tool':
            state = part.get('state',{})
            args = state.get('input',{})
            item = {'id':iid,'type':'dynamicToolCall','tool':part.get('tool','OpenCode tool'),
                    'status':state.get('status','running'),'output':str(state.get('output') or state.get('error') or '')[-30000:]}
            if isinstance(args,dict) and args.get('command'):
                item.update(type='commandExecution',command=args['command'],aggregatedOutput=item['output'])
            self.emit(tid,'item/completed',item=item)
            if state.get('status') in ('completed','error'):
                self.request_changes(tid)

    async def resync(self, tid):
        t = self.state.thread(tid)
        sid, cwd = t['opencodeSession'],t.get('setupCwd') or t['cwd']
        messages = await self.call('GET','/session/'+quote(sid)+'/message?limit=200',cwd=cwd)
        for message in messages:
            self.roles[message['info']['id']] = message['info']['role']
            for part in message.get('parts',[]):
                self.part(tid,part)
        todos = await self.call('GET','/session/'+quote(sid)+'/todo',cwd=cwd)
        if todos:
            self.event({'type':'todo.updated','properties':{'sessionID':sid,'todos':todos}})
        for path,kind in (('/permission','permission.asked'),('/question','question.asked')):
            for pending in await self.call('GET',path,cwd=cwd):
                self.event({'type':kind,'properties':pending})

    async def configure_access(self, t, config):
        value = config.get('opencodeAccess','Ask')
        rules = [{'permission':'*','pattern':'*','action':'allow' if value=='Autonomous' else 'ask'}]
        await self.call('PATCH','/session/'+quote(t['opencodeSession']),{'permission':rules},t.get('setupCwd') or t['cwd'])
        t['reportedAccess']={'provider':'opencode','opencodeAccess':value,'approvalPolicy':value}
        self.state.dirty=True

    async def message(self, t, text, config, context, client_id=None):
        await self.ensure()
        cwd, tid = t.get('setupCwd') or t['cwd'],t['id']
        active = t.get('status',{}).get('type')=='active'
        if active and (t.get('model')!=config['model'] or cwd!=t.get('cwd')):
            raise ValueError('Finish or interrupt the OpenCode turn before changing model or workspace')
        if not t.get('opencodeSession'):
            session = await self.call('POST','/session',{'title':t['name']},cwd)
            t['opencodeSession']=session['id']
            t['opencodeCwd']=cwd
            self.sessions[session['id']]=tid
            self.state.save()
        elif t.get('opencodeCwd',cwd)!=cwd:
            raise ValueError('An OpenCode session keeps its workspace. Create a new agent to use another folder.')
        if not active:
            await self.configure_access(t,config)
        sid=t['opencodeSession']
        params={'messageID':'msg'+uuid.uuid4().hex,'parts':[{'type':'text','text':text}],
                'system':'\n\n'.join(v['value'] for v in context.values())}
        if config['model']:
            provider,model=config['model'].split('/',1)
            params['model']={'providerID':provider,'modelID':model}
        if not active:
            self.emit(tid,'turn/started',turn={'id':str(uuid.uuid4())})
        t.update(cwd=cwd,model=config['model'])
        self.emit(tid,'item/completed',item={'id':client_id or params['messageID'],'clientId':client_id,
            'type':'userMessage','content':[{'text':text}]})
        try:
            await self.call('POST','/session/'+quote(sid)+'/prompt_async',params,cwd)
        except HTTPError:
            if not active:
                self.finish(tid,'failed')
            raise
        except Exception:
            t['activity']='OpenCode delivery unconfirmed; inspect or interrupt before retrying'
            self.state.dirty=True
            raise
        return {'threadId':tid,'turnId':t.get('turnId')}

    async def interrupt(self, t):
        await self.ensure()
        if not t.get('opencodeSession'):
            raise ValueError('No OpenCode turn to interrupt')
        await self.call('POST','/session/'+quote(t['opencodeSession'])+'/abort',{},t.get('setupCwd') or t['cwd'])
        self.finish(t['id'],'interrupted')

    async def respond(self, request, response):
        await self.ensure()
        t=self.state.thread(request['params']['threadId'])
        if request['kind']=='permission':
            decision=response.get('decision')
            if decision not in ('accept','decline','cancel'):
                raise ValueError('Choose approve once or decline')
            path='/permission/'+quote(request['wireId'])+'/reply'
            body={'reply':'once' if decision=='accept' else 'reject'}
        else:
            answers=[response.get('answers',{}).get(q['id'],{}).get('answers',[]) for q in request['params']['questions']]
            if not all(answers):
                raise ValueError('Answer each OpenCode question')
            path='/question/'+quote(request['wireId'])+'/reply'
            body={'answers':answers}
        await self.call('POST',path,body,t.get('setupCwd') or t['cwd'])
        self.state.requests.pop(request['id'],None)
        self.refresh_waiting(t['id'])
        return {}

    async def stop_transport(self):
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task,return_exceptions=True)
            self.reader_task=None
        if self.process:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(),3)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process=None
        self.http=None

    async def close(self):
        await self.stop_transport()
        for task in list(self.background):
            task.cancel()
        await asyncio.gather(*self.background,return_exceptions=True)
        self.background.clear()
