"""Read-only process inventory and separate per-agent dev ports."""
import asyncio
import os
from pathlib import Path
import re
import shutil
import socket
import time


async def lsof_records(*args):
    executable = shutil.which('lsof') or '/usr/sbin/lsof'
    process = await asyncio.create_subprocess_exec(executable, *args, '-Fpcfn',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 4)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode not in (0, 1):
        raise OSError('Process inventory unavailable')
    records, current = {}, None
    for line in output.decode(errors='replace').splitlines():
        if line.startswith('p') and line[1:].isdigit():
            current = records.setdefault(int(line[1:]), {'name': '', 'names': []})
        elif current is not None and line.startswith('c'):
            current['name'] = line[1:]
        elif current is not None and line.startswith('n'):
            current['names'].append(line[1:])
    return records


async def inventory(threads):
    uid = str(os.getuid())
    cwd, listeners = await asyncio.gather(lsof_records('-a', '-u', uid, '-d', 'cwd'),
                        lsof_records('-a', '-u', uid, '-nP', '-iTCP', '-sTCP:LISTEN'))
    roots = [(tid, t.get('name') or tid, (t.get('agentWorktree') or {}).get('root') or (t.get('agentWorktree') or {}).get('cwd'))
             for tid, t in threads.items()]
    rows = []
    for pid in sorted(set(cwd) | set(listeners)):
        info = cwd.get(pid, listeners.get(pid))
        directory = next(iter(cwd.get(pid, {}).get('names', [])), '')
        matches = [(tid, name) for tid, name, root in roots if root and (directory == root or directory.startswith(root.rstrip('/') + '/'))]
        ports = sorted({int(m[1]) for n in listeners.get(pid, {}).get('names', [])
                        for m in [re.search(r':(\d+)$', n)] if m})
        if not matches and not ports:
            continue
        rows.append({'pid': pid, 'name': info['name'], 'ports': ports,
                     'agentId': matches[0][0] if len(matches) == 1 else None,
                     'agentName': matches[0][1] if len(matches) == 1 else None,
                     'cwd': directory if len(matches) == 1 else '', 'association': 'Workspace match' if len(matches) == 1 else 'Unassigned'})
    return {'rows': rows, 'checkedAt': time.time(), 'error': None}


def choose_port(excluded):
    for port in range(42000, 43000):
        if port in excluded:
            continue
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
                with socket.socket(socket.AF_INET6) as ipv6:
                    ipv6.bind(('::1', port))
            return port
        except OSError:
            continue
    raise ValueError('No free agent port in 42000–42999')


def process_rows(data, selected, width, wrap):
    status = data.get('processInventory', {})
    thread = data.get('threads', {}).get((selected or '').removeprefix('thread:'), {})
    lines = [('PROCESSES', 'accent'), ('Agent dev: ' + str(thread.get('agentPort') or 'Not assigned') + ' · Test: ' + str(thread.get('agentTestPort') or 'Not assigned'), 'accent'),
             ('Workspace match identifies location, not who started the process.', 'muted')]
    if status.get('error'):
        lines.append((status['error'], 'error'))
    elif not status.get('checkedAt'):
        lines.append(('Loading process inventory…', 'muted'))
    else:
        lines.append(('Updated %ds ago · TCP listeners and agent workspaces' % max(0, time.time() - status['checkedAt']), 'muted'))
    rows = status.get('rows', [])
    for process in sorted(rows, key=lambda p: (p.get('agentId') != thread.get('id'), p.get('agentName') or '~', p['pid'])):
        lines.append(('%s · PID %s · Ports %s' % (process['name'], process['pid'], ', '.join(map(str, process['ports'])) or '—'), 'base'))
        lines.append(('  ' + (process.get('agentName') or 'Unassigned') + ' · ' + process['association'], 'muted'))
        if process.get('cwd'):
            lines.append(('  ' + process['cwd'], 'muted'))
    if status.get('checkedAt') and not rows:
        lines.append(('No matching processes or TCP listeners found.', 'muted'))
    return [{'text': part, 'tone': tone, 'title': '', 'inset': 0, 'header': False, 'copy_text': None}
            for line, tone in lines for part in wrap(line, max(8, width))]
