"""Application settings and read-only dependency checks, independent of agents."""
import asyncio
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .opencode_runtime import opencode_executable
from .terminal_setup import terminal_info
from .connections import CopilotProbe, copilot_executable, providers, LABELS


def command_info(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return result.returncode, (result.stdout + result.stderr)[:30000]
    except (OSError, subprocess.TimeoutExpired):
        return None, ''


def local_report(codex='codex'):
    report = {'appVersion': __version__, 'python': platform.python_version(),
              'platform': sys.platform, 'terminal': terminal_info(), 'git': bool(shutil.which('git')), 'node': None, 'providers': {}}
    node = shutil.which('node')
    if node:
        code, version = command_info([node, '--version'])
        report['node'] = version.strip() if code == 0 else None
    for key, executable in (('codex', shutil.which(codex)), ('copilot', copilot_executable()), ('opencode', opencode_executable())):
        info = {'installed': bool(executable), 'path': executable, 'compatible': False, 'version': None}
        if executable:
            code, version = command_info([executable, '--version'])
            info['version'] = version.strip().splitlines()[0] if code == 0 and version.strip() else 'Unknown'
            args = ['app-server', '--help'] if key == 'codex' else ['serve', '--help'] if key == 'opencode' else ['--help']
            code, help_text = command_info([executable, *args])
            if key == 'codex':
                info['compatible'] = code == 0 and '--stdio' in help_text
            elif key == 'opencode':
                info['compatible'] = code == 0 and '--pure' in help_text and str(info['version']).startswith('1.')
            else:
                async def protocol():
                    probe = CopilotProbe()
                    try:
                        await probe.start(executable, str(Path.home()))
                        return (await probe.call('ping', timeout=8)).get('protocolVersion')
                    finally:
                        await probe.close()
                try:
                    info['protocolVersion'] = asyncio.run(protocol())
                    info['compatible'] = info['protocolVersion'] == 3
                except (OSError, ValueError, RuntimeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                    info['compatible'] = False
        report['providers'][key] = info
    return report


def settings_text(data, directory):
    values = providers(data)
    settings = data.get('settings', {})
    lines = ['# Tyrell Agent Management · Settings', '', '## Preferences',
             '`[H]` Edit GitHub host: ' + (values.get('copilot', {}).get('expectedHost') or settings.get('copilotHost') or 'https://github.com'),
             '`[M]` [' + ('x' if settings.get('mouseEnabled', True) else ' ') + '] Mouse navigation',
             '`[K]` [' + ('x' if settings.get('keepAwake', True) else ' ') + '] Keep Mac awake while agents work',
             '  Prevents automatic sleep while agents are active, including when waiting for input.',
             '  The display may still turn off. Normal sleep resumes when all agents are idle.',
             '`[G]` Edit global agent defaults…',
             '`[C]` Appearance · Custom interface colors…',
             'Changes save immediately. Agent and project overrides take precedence over global defaults.',
             'Select with ↑/↓ and Enter, click a setting, or use its letter.', '', '## Connections']
    for key, name in (('codex', 'Codex'), ('copilot', 'GitHub Copilot'), ('opencode', 'OpenCode')):
        info = values.get(key, {})
        status = info.get('status', 'offline')
        connected = info.get('connected', status == 'connected')
        installed = data.get('installation', {}).get('providers', {}).get(key)
        # A live connection probe can discover a CLI installed since the last scan.
        detected = bool(installed and installed.get('installed')) or status in ('connected', 'signin', 'account', 'unavailable')
        cli = 'Detected' if detected else 'Not detected' if installed is not None or status == 'missing' else 'Checking…'
        if installed and installed.get('installed'):
            cli += ' · ' + (installed.get('version') or 'Version unknown')
            cli += ' · ' + ('Compatible' if installed.get('compatible') else 'Setup needed')
        connection = 'Connected' if connected else 'Not connected'
        if not connected and status not in ('missing', 'offline'):
            connection += ' · ' + LABELS.get(status, ('Unavailable', 'warning'))[0]
        lines.extend(['**' + name + '**', '  CLI: ' + cli, '  Connection: ' + connection,
                      '  Models: ' + (str(len(info.get('models', []))) + ' available' if connected else 'Available after connection')])
        if installed and installed.get('installed') and installed.get('path'):
            lines.append('  CLI path: ' + installed['path'])
        if key == 'codex' and info.get('sessionStorage') == 'migrationPending':
            lines.append('  Session isolation pending: existing agents can finish; new Codex agents wait for migration.')
        lines.append('')
    lines += ['', 'One connected provider is enough. CLI installation and account access are separate.',
              '`[1]` Codex installation & sign-in', '`[2]` Copilot installation & sign-in', '`[3]` OpenCode installation & model access',
              '`[D]` Check installed CLIs and compatibility', '', '## GitHub account',
              'Host: ' + (values.get('copilot', {}).get('expectedHost') or values.get('copilot', {}).get('host') or 'https://github.com'),
              'Use your company hostname for GitHub Enterprise Cloud.',
              'Change the host before signing in; existing Copilot work must be idle.', '', '## App & local data',
              'App version: ' + __version__, 'Background service: ' + str(data.get('appVersion') or 'Older version'),
              'Data folder: ' + str(directory),
              'Model CLIs own their sign-in credentials. Tyrell Agent Management does not bundle credentials or accounts.',
              'Agent permissions and project preferences are in the agent\'s Setup tab.', '', '## Terminal',
              'Ghostty: ' + ('Installed' if data.get('installation', {}).get('terminal', {}).get('ghostty') else 'Not detected'),
              'Run `tyrell setup` to choose a terminal or optionally install Ghostty on macOS using Homebrew.',
              'Other terminals are allowed; mouse, clipboard and keyboard support varies.', '', '## Updates',
              'Wait for agents to finish, then run `tyrell stop` before upgrading the package.',
              'Removing the pipx package keeps your local chat history and worktrees.',
              '', '`[Esc]` Close settings. Connections refresh automatically.']
    return '\n'.join('  ' + line if line and not line.startswith('#') else line for line in lines)


def provider_guide(provider, host='https://github.com'):
    if provider == 'opencode':
        return ('# Connect OpenCode\n\n## 1 · Install OpenCode CLI 1.x\n`npm install -g opencode-ai`\n'
                'Official installation: https://opencode.ai/docs/\n\n'
                '## 2 · Configure your model access\n`tyrell login opencode`\n'
                'Use your own API keys or supported provider login. No OpenCode account is required by Tyrell.\n'
                'For a local model, configure OpenCode for your local provider instead.\n\n'
                '## 3 · Create an agent\nF3 → OpenCode → provider/model. Permissions are in Setup.\n'
                'Tyrell keeps separate sessions and starts an authenticated loopback server.\n'
                'External OpenCode plugins are disabled in this integration. Subscriptions and API charges are yours.\n')
    if provider == 'codex':
        return ('# Connect Codex\n\n## 1 · Install its CLI\n'
                '`npm install -g @openai/codex`\n'
                'Official installation: https://developers.openai.com/codex/cli/\n\n'
                '## 2 · Sign in\n`tyrell login codex`\n\n'
                '## 3 · Start Tyrell’s local server\n`tyrell start-provider codex`\n'
                'Tyrell starts a private app-server with separate session storage.\n'
                'Check with `tyrell doctor`; installation alone does not guarantee compatibility.\n\n'
                'The CLI uses your own account and model access.\nRun these commands in another terminal, then reopen F10 Settings.')
    return ('# Connect GitHub Copilot\n\n## 1 · Install its CLI\n'
            '`npm install -g @github/copilot`\nThe npm installation requires Node.js 22 or newer.\n'
            'Official installation: https://docs.github.com/en/copilot/get-started/cli-quickstart\n\n'
            '## 2 · Choose your GitHub host\n'
            'GitHub.com or your company\'s GitHub Enterprise Cloud hostname.\n'
            'Current host: ' + host + '\n\n## 3 · Sign in\n'
            '`tyrell login copilot --host ' + shlex.quote(host) + '`\n\n'
            'Your account needs Copilot access; organization policy may restrict CLI usage.\n'
            'Tyrell Agent Management starts the Copilot connection after sign-in.\n'
            'Run these commands in another terminal, then reopen F10 Settings.')


def diagnostics_text(report):
    lines = ['# Installation check', '', 'Python: ' + report['python'],
             'Git: ' + ('Installed' if report['git'] else 'Missing — install Git to use worktrees and handovers'),
             'Node.js: ' + (report['node'] or 'Not found — needed for npm-based CLI installations'), '']
    for key, name in (('codex', 'Codex'), ('copilot', 'Copilot'), ('opencode', 'OpenCode')):
        info = report['providers'].get(key, {'installed': False, 'compatible': False, 'version': None})
        lines += ['## ' + name, 'Installed: ' + ('Yes' if info['installed'] else 'No'),
                  'Version: ' + (info['version'] or 'Not installed'),
                  'Required CLI features: ' + ('Detected' if info['compatible'] else 'Missing or unavailable')]
        if info.get('path'):
            lines.append('Executable: ' + info['path'])
    lines += ['', 'This checks local CLI features, not account access.',
              'Live connection and model access appear in F10 Settings.',
              'Copilot tasks also require protocol 3; the runtime checks it before starting work.']
    return '\n'.join(lines)
