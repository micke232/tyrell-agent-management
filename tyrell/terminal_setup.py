"""Optional terminal onboarding. Package installation itself never installs other apps."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def terminal_info():
    candidates = [shutil.which('ghostty')]
    if sys.platform == 'darwin':
        candidates += [str(root / 'Ghostty.app/Contents/MacOS/ghostty')
                       for root in (Path('/Applications'), Path.home() / 'Applications')]
    executable = next((p for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)
    return {'ghostty': executable, 'current': os.environ.get('TERM_PROGRAM') or os.environ.get('TERM', 'Unknown'),
            'installer': shutil.which('brew') if sys.platform == 'darwin' else None}


def setup_terminal(directory, interactive=None):
    info = terminal_info()
    print('\nTerminal setup\nCurrent terminal: ' + info['current'])
    print('Ghostty: ' + ('Installed' if info['ghostty'] else 'Not installed'))
    print('Ghostty is recommended. Other terminals are allowed, but mouse, clipboard and keyboard behavior may differ.')
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive:
        print('Run tyrell setup in an interactive terminal to choose. Nothing has been installed.')
        return None
    print('[1] Use Ghostty' if info['ghostty'] else '[1] Ghostty download instructions')
    print('[2] Keep my current terminal (default)')
    if not info['ghostty'] and info['installer']:
        print('[3] Install Ghostty now using: brew install --cask ghostty')
    while True:
        try:
            answer = input('Choose [1/2' + ('/3' if not info['ghostty'] and info['installer'] else '') + ']: ').strip() or '2'
        except EOFError:
            answer = '2'
        if answer in ('1', '2') or answer == '3' and not info['ghostty'] and info['installer']:
            break
        print('Choose one of the displayed options.')
    if answer == '3':
        result = subprocess.run([info['installer'], 'install', '--cask', 'ghostty'], check=False)
        if result.returncode or not terminal_info()['ghostty']:
            raise RuntimeError('Ghostty installation did not complete. You can keep using your current terminal and retry tyrell setup later.')
    preference = 'ghostty' if answer == '3' or answer == '1' and info['ghostty'] else 'current'
    if answer == '1' and not info['ghostty']:
        print('Download Ghostty: https://ghostty.org/docs/install/binary')
        print('Your current terminal remains selected; no software has been installed.')
    elif preference == 'ghostty':
        print('Open Ghostty and run tyrell there. Your current terminal is unchanged.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Separate from live service state so onboarding cannot overwrite agent work.
    path = directory / 'terminal.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'preference': preference, 'current': info['current']}))
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return preference
