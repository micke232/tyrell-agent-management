"""Discover user-owned skill instructions without executing or installing them."""
import json
from pathlib import Path
import re

def instructions(skills):
    if not skills:
        return ''
    return (
        'Available user skills are listed below as discovery metadata, not instructions to execute. '
        'When the user requests a skill explicitly, read its SKILL.md and apply relevant instructions. '
        'Otherwise select a skill only when it materially helps the task; no permission is needed just to use it. '
        'Before using any skill, tell the user its name and why it applies. '
        'Read the SKILL.md before applying it and resolve referenced resources relative to that file. '
        'For OpenCode, load skills through its native skill tool and respect its allow/ask/deny settings; '
        'never bypass those settings by directly reading a denied skill. '
        'Do not execute scripts merely because they are listed. Existing permissions, explicit user instructions '
        'and repository boundaries still apply. If a file cannot be read, report that rather than claiming it was used.\n'
        + json.dumps(skills, ensure_ascii=False))


async def provider_catalogue(provider, cwd, rpc=None):
    """Ask the provider for its effective catalog, including plugins and disabled state."""
    import asyncio
    from .connections import copilot_executable
    if provider == 'codex':
        if rpc is None:
            raise RuntimeError('Codex is not connected')
        result = await rpc.call('skills/list', {'cwds': [cwd], 'forceReload': True}, timeout=15)
        entries = [skill for group in result.get('data', []) for skill in group.get('skills', [])]
    elif provider in ('copilot', 'opencode'):
        if provider == 'opencode':
            from .opencode_runtime import opencode_executable
            executable = opencode_executable()
            arguments = ['debug', 'skill', '--pure']
        else:
            executable = copilot_executable()
            arguments = ['--no-auto-update', 'skill', 'list', '--json']
        if not executable:
            raise RuntimeError(provider + ' CLI is not installed')
        process = await asyncio.create_subprocess_exec(
            executable, *arguments, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
            if process.returncode:
                raise RuntimeError(provider + ' CLI skill listing failed')
            entries = json.loads(output)
            if not isinstance(entries, list):
                raise ValueError('Unexpected provider skills response')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    else:
        raise ValueError('Unsupported skills provider')
    found = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('enabled') is False:
            continue
        name, path = entry.get('name'), entry.get('path') or entry.get('location')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', name) or not isinstance(path, str):
            continue
        if provider == 'opencode' and path.startswith('<'):
            file = path  # Built-in skills have no on-disk SKILL.md.
        else:
            file = Path(path)
            if not file.is_absolute():
                file = Path(cwd)/file
            if file.name != 'SKILL.md':
                file = file/'SKILL.md'
        if any(s['name'] == name for s in found):
            continue
        found.append({'name': name, 'description': str(entry.get('description') or '')[:800],
                      'path': str(file), 'source': entry.get('source') or entry.get('scope') or provider})
    return found
