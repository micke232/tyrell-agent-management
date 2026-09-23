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


class ChangeSet(dict):
    pass


async def comparison_base(cwd):
    """Include committed feature work; stay at HEAD on the default branch."""
    try:
        branch = (await git_bytes(cwd, 'symbolic-ref', '--short', 'HEAD')).decode().strip()
    except ValueError:
        return 'HEAD'
    candidates = []
    try:
        remote = (await git_bytes(cwd, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')).decode().strip()
        candidates.append(remote)
    except ValueError:
        pass
    candidates.extend(['origin/main', 'origin/master', 'main', 'master'])
    for candidate in candidates:
        if branch == candidate.removeprefix('origin/'):
            return 'HEAD'
        try:
            return (await git_bytes(cwd, 'merge-base', 'HEAD', candidate)).decode().strip()
        except ValueError:
            continue
    return 'HEAD'


async def changes(cwd, base=None):
    root = Path((await git_bytes(cwd, 'rev-parse', '--show-toplevel')).decode().strip())
    base = base or await comparison_base(root)
    tokens = (await git_bytes(root, 'diff', '--name-status', '-z', '--no-ext-diff', '--no-textconv', base, '--')).split(b'\0')
    items = []; index = 0
    while index < len(tokens) and tokens[index]:
        code = tokens[index].decode(); name = os.fsdecode(tokens[index + 1]); index += 2
        previous = None
        if code.startswith(('R', 'C')):
            previous, name = name, os.fsdecode(tokens[index]); index += 1
        items.append((code, name, previous))
    items.extend(('??', os.fsdecode(name), None) for name in (await git_bytes(root, 'ls-files', '--others', '--exclude-standard', '-z')).split(b'\0') if name)
    records = ChangeSet()
    records.base = base
    records.truncated = len(items) > 250
    records.total = len(items)
    for code, name, previous in items:
        if len(records) >= 250:
            break
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
