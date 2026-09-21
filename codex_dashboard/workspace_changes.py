"""Bounded, read-only Git changes for provider-independent workspaces."""
import asyncio
import hashlib
import os
from pathlib import Path


async def git_bytes(cwd, *args, limit=8 * 1024 * 1024):
    process = await asyncio.create_subprocess_exec('git', '-C', str(cwd), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0'))
    async def collect():
        chunks, size = [], 0
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('Git result exceeds the workspace snapshot limit')
            chunks.append(chunk)
        await process.wait()
        if process.returncode:
            raise ValueError('Git workspace inspection failed')
        return b''.join(chunks)
    try:
        return await asyncio.wait_for(collect(), 15)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def changes(cwd, base="HEAD"):
    root = Path((await git_bytes(cwd, 'rev-parse', '--show-toplevel')).decode().strip())
    tokens = (await git_bytes(root, 'diff', '--name-status', '-z', '--no-ext-diff', '--no-textconv', base, '--')).split(b'\0')
    items = []; index = 0
    while index < len(tokens) and tokens[index]:
        code = tokens[index].decode(); name = os.fsdecode(tokens[index + 1]); index += 2
        previous = None
        if code.startswith(('R', 'C')):
            previous, name = name, os.fsdecode(tokens[index]); index += 1
        items.append((code, name, previous))
    items.extend(('??', os.fsdecode(name), None) for name in (await git_bytes(root, 'ls-files', '--others', '--exclude-standard', '-z')).split(b'\0') if name)
    records = {}
    for code, name, previous in items:
        if len(records) >= 250:
            raise ValueError('More than 250 changed files; narrow the workspace before collecting diffs')
        path = root / name
        sensitive = any(part.startswith('.env') or part in ('.npmrc', '.netrc', '.pypirc', 'credentials') or part.endswith(('.pem', '.key')) for part in Path(name).parts)
        if sensitive:
            diff = 'Sensitive configuration file: content preview omitted'
            kind = 'add' if code == '??' else 'delete' if 'D' in code else 'update'
        elif code == '??':
            if path.is_symlink():
                diff = 'New symbolic link (contents not read)'
            elif path.is_file():
                with path.open('rb') as stream:
                    content = stream.read(60001)
                diff = 'Binary file (preview unavailable)' if b'\0' in content else '\n'.join('+' + line for line in content.decode('utf-8', 'replace').splitlines())
            else:
                continue
            kind = 'add'
        else:
            diff = (await git_bytes(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-color', base, '--', name, *([previous] if previous else []))).decode('utf-8', 'replace')
            kind = 'rename' if previous else 'delete' if 'D' in code else 'add' if 'A' in code else 'update'
        lines = diff.splitlines()
        records[str(path)] = dict(path=str(path), kind=kind, status='completed',
            diff=diff[:60000], truncated=len(diff)>60000, itemId='git-'+hashlib.sha256(os.fsencode(name)).hexdigest()[:16],
            added=sum(l.startswith('+') and not l.startswith('+++') for l in lines),
            removed=sum(l.startswith('-') and not l.startswith('---') for l in lines), **{'from': str(root/previous) if previous else None})
    return records
