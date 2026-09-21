"""Real curses and polling loop connected only to the synthetic test service."""
import curses
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tyrell import cli
from tyrell.ui import Dashboard
from tyrell.presentation import PALETTE

root = Path(sys.argv[1])


class TrackedDashboard(Dashboard):
    renders = 0

    def render(self, screen):
        super().render(screen)
        self.renders += 1
        state = {'at': time.monotonic(), 'buffer': self.buffer, 'renders': self.renders,
                 'settingsSelected': self.hub_selected,
                 'settingsMarker': screen.instr(next((y for _, _, y, action in self.hub_hits if action == self.hub_selected), 0), 4, 3).decode('utf-8', errors='replace') if self.panel == 'HUB SETTINGS' else '',
                 'plan': self.current().get('plan', []), 'panel': self.panel, 'picking': self.appearance.picking, 'appearance': self.appearance.values,
                 'basePair': curses.pair_content(list(PALETTE).index('base')+1),
                 'statusPairs': {name: curses.pair_content(list(PALETTE).index(name)+1) for name in ('working', 'success', 'warning', 'error')},
                 'text': [v['text'] for v in self.history_cells.values()]}
        temporary = root/'frame.tmp'
        temporary.write_text(json.dumps(state))
        temporary.replace(root/'frame.json')


cli.Dashboard = TrackedDashboard
ui = cli.prepare_dashboard(root, 'unused-test-provider', False)
cli.run_dashboard(ui)
