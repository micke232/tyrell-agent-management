"""Repeatable rendering benchmark using fictional data and no terminal/agents."""
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tyrell.ui import Dashboard


class Screen:
    def __init__(self, width, height):
        self.width, self.height = width, height

    def getmaxyx(self):
        return self.height, self.width

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def measure(width, height, stream):
    items = [{'id': str(i), 'type': 'userMessage' if i % 3 == 0 else 'agentMessage',
              'text': ('**Status** åäö 猫: reviewing the workspace and checking configuration.\n' * 12)} for i in range(120)]
    thread = {'id': 'fixture', 'name': 'Performance fixture', 'status': {'type': 'active'}, 'items': items}
    ui = Dashboard('/tmp', {'connected': True, 'threads': {'fixture': thread}, 'tasks': []})
    ui.pointer_supported = False
    ui.selected = 'thread:fixture'
    ui.update()
    screen = Screen(width, height)
    with patch('curses.curs_set'):
        ui.render(screen)
        times = []
        for i in range(80):
            if stream:
                items[-1]['text'] += ' token'
            start = time.perf_counter()
            ui.render(screen)
            times.append((time.perf_counter() - start) * 1000)
    return {'size': '%dx%d' % (width, height), 'streaming': stream,
            'median_ms': round(statistics.median(times), 3), 'p95_ms': round(sorted(times)[75], 3)}


if __name__ == '__main__':
    print(json.dumps({'python': sys.version.split()[0], 'results': [measure(w, h, stream)
          for w, h in ((120, 40), (240, 60), (400, 80)) for stream in (False, True)]}, indent=2))
