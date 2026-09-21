import contextlib
import curses
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tyrell.appearance import Appearance, FIELDS, validate, parse_color, hex_color
from tyrell.presentation import PALETTE, theme
from tyrell.ui import Dashboard
from test_native_clipboard import Screen


@contextlib.contextmanager
def colors():
    with patch('curses.has_colors', return_value=True), patch('curses.COLORS', 256, create=True), \
            patch('curses.COLOR_PAIRS', 256, create=True), patch('curses.init_pair') as pairs, \
            patch('curses.color_pair', side_effect=lambda i: i << 8), patch('curses.curs_set'):
        yield pairs


class AppearanceTests(unittest.TestCase):
    def test_color_names_hex_rgb_and_invalid_input(self):
        self.assertEqual(parse_color('Coral'), 203)
        self.assertEqual(parse_color('#5fd7ff'), 81)
        self.assertEqual(parse_color('95, 215, 255'), 81)
        self.assertEqual(parse_color('rgb(95,215,255)'), 81)
        self.assertEqual(hex_color(parse_color('#fff')), '#FFFFFF')
        self.assertEqual(parse_color('grey'), parse_color('gray'))
        for text in ('', '#12345', 'redish', '256,0,0', 'rgb(1,2,3', '1,2,3)'):
            with self.assertRaises(ValueError):
                parse_color(text)

    def test_f10_arrow_selection_remains_visible_after_all_text_is_drawn(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.open_hub_settings()
            for width, height in ((70, 18), (100, 40)):
                for sequence in ('\x1b[B', '\x1bOB', '\x1b[A', '\x1bOA'):
                    for _ in range(8):
                        for char in sequence:
                            ui.input_key(char)
                        screen = Screen(width=width, height=height)
                        ui.render(screen)
                        hit = next(hit for hit in ui.hub_hits if hit[3] == ui.hub_selected)
                        # Inspect the final cell, including later spaces which
                        # previously painted over the marker after it was drawn.
                        marker = ' '
                        for y, x, text, _ in screen.draws:
                            if y == hit[2] and x <= 4 < x + len(text):
                                marker = text[4-x]
                        self.assertEqual(marker, '›', (width, sequence, ui.hub_selected))

    def test_indented_settings_keyboard_and_mouse_navigation(self):
        from tyrell.hub_settings import settings_text
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.open_hub_settings()
            screen = Screen(width=100, height=40)
            ui.render(screen)
            self.assertIn('  `[C]`', settings_text(ui.data, directory))
            self.assertTrue(any(action == 'c' for _, _, _, action in ui.hub_hits))
            for _ in range(4):
                ui.key(curses.KEY_DOWN)
            self.assertEqual(ui.hub_selected, 'c')
            ui.key('\r')
            self.assertEqual(ui.panel, 'APPEARANCE')
            ui.key('\x1b')
            ui.render(screen)
            hit = next(hit for hit in ui.hub_hits if hit[3] == 'c')
            ui.mouse(0, hit[0], hit[2])
            self.assertEqual(ui.panel, 'APPEARANCE')

    def test_typed_and_pasted_color_previews_keep_chat_draft_and_cancel_safely(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.panel = 'APPEARANCE'
            ui.buffer = 'Do not change my prompt'
            for char in '\x1b[200~#5fd7ff\x1b[201~':
                ui.input_key(char)
            self.assertEqual(ui.appearance.method, 'input')
            self.assertEqual(ui.appearance.color, 81)
            self.assertFalse(ui.appearance.path.exists())
            self.assertEqual(ui.buffer, 'Do not change my prompt')
            ui.key('\r')
            self.assertEqual(Appearance(directory).values['base'][0], 81)
            ui.key('\r')
            ui.key('h')
            for char in 'coral':
                ui.key(char)
            self.assertEqual(ui.appearance.color, 203)
            ui.key('\x1b')
            self.assertEqual(Appearance(directory).values['base'][0], 81)

    def test_mock_preview_includes_every_editable_role_and_is_clickable(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.panel = 'APPEARANCE'
            screen = Screen(width=100, height=40)
            ui.render(screen)
            a = ui.appearance
            top, bottom = a.preview_bounds
            roles = {list(FIELDS)[value[0]] for _, _, y, action, value in a.hits
                     if top <= y < bottom and action == 'role'}
            self.assertEqual(roles, set(FIELDS))
            self.assertFalse(any('status colors are fixed' in text for _, _, text, _ in screen.draws))
            role_index = list(FIELDS).index('user')
            hit = next(hit for hit in a.hits if top <= hit[2] < bottom and hit[3] == 'role' and hit[4][0] == role_index)
            ui.mouse(0, hit[0], hit[2])
            self.assertEqual(a.role, 'user')
            self.assertTrue(a.picking)

    def test_settings_are_local_persistent_and_reject_protected_or_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            appearance = Appearance(directory)
            appearance.save({'user': [45, 235], 'agentname': [200, 234]})
            self.assertEqual(Appearance(directory).values, appearance.values)
            self.assertEqual(appearance.path.stat().st_mode & 0o777, 0o600)
            for invalid in ({'working': [1, 2]}, {'startup': [1, 2]}, {'user': [256, 1]},
                            {'user': [True, 2]}, {'user': [1]}, []):
                with self.assertRaises(ValueError):
                    appearance.save(invalid)
            self.assertEqual(Appearance(directory).values['user'], [45, 235])

    def test_palette_changes_never_mutate_defaults_or_status_colors(self):
        original = dict(PALETTE)
        overrides = {key: [12, 13] for key in PALETTE}
        with colors() as pairs:
            theme(overrides)
        actual = {name: pair.args[1:] for name, pair in zip(PALETTE, pairs.call_args_list)}
        for name in ('working', 'warning', 'success', 'error', 'statusmuted', 'logerror', 'logsuccess'):
            self.assertEqual(actual[name], PALETTE[name][:2])
            self.assertNotIn(name, FIELDS)
        self.assertEqual(actual['user'], (12, 13))
        self.assertEqual(actual['agentname'], (12, 13))
        self.assertEqual(PALETTE, original)

    def test_keyboard_preview_cancel_save_and_restore_defaults(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.buffer = 'unsent draft'
            ui.key(curses.KEY_F10)
            ui.key('c')
            self.assertEqual(ui.panel, 'APPEARANCE')
            a = ui.appearance
            ui.key('\r')
            self.assertTrue(a.picking)
            original = a.color
            ui.key('p')
            ui.key(curses.KEY_LEFT)
            self.assertEqual(a.color, original-1)
            self.assertFalse(a.path.exists())
            ui.key('\x1b')
            self.assertFalse(a.picking)
            self.assertFalse(a.values)
            ui.key('\r')
            ui.key('p')
            ui.key(curses.KEY_LEFT)
            ui.key('\r')
            self.assertEqual(Appearance(directory).values['base'][0], original-1)
            ui.key('r')
            self.assertEqual(Appearance(directory).values, {})
            self.assertEqual(ui.buffer, 'unsent draft')
            ui.key('\x1b')
            self.assertEqual(ui.panel, 'HUB SETTINGS')

    def test_small_terminal_mouse_picker_pages_and_reset(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.panel = 'APPEARANCE'
            ui.render(Screen(width=70, height=18))
            a = ui.appearance
            hit = next(hit for hit in a.hits if hit[3] == 'role' and hit[4] == (0, 1))
            ui.mouse(0, hit[0], hit[2])
            self.assertTrue(a.picking)
            screen = Screen(width=70, height=18)
            ui.render(screen)
            self.assertTrue(all(0 <= y < 18 and 0 <= x < 70 for y, x, _, _ in screen.draws))
            ui.key(curses.KEY_PPAGE)
            ui.render(screen)
            hit = next(hit for hit in a.hits if hit[3] == 'color')
            ui.mouse(0, hit[0], hit[2])
            self.assertFalse(a.path.exists())
            ui.key('\r')
            self.assertEqual(Appearance(directory).values['base'][1], hit[4])
            ui.key('d')
            self.assertEqual(Appearance(directory).values, {})

    def test_corrupt_file_and_write_error_keep_ui_recoverable(self):
        with tempfile.TemporaryDirectory() as directory, colors():
            path = Path(directory)/'appearance.json'
            path.write_text('not json')
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            self.assertEqual(ui.appearance.values, {})
            self.assertIn('could not be loaded', ui.appearance.notice)
            with patch.object(ui.appearance, 'save', side_effect=OSError('Read only')):
                ui.appearance.commit(ui, {'base': [1, 2]})
            self.assertEqual(ui.appearance.values, {})
            self.assertIn('Could not save', ui.appearance.notice)

    def test_limited_terminal_does_not_offer_unavailable_palette(self):
        with tempfile.TemporaryDirectory() as directory, patch('curses.has_colors', return_value=False):
            ui = Dashboard(directory, {'threads': {}, 'tasks': []})
            ui.appearance.choose(ui)
            self.assertFalse(ui.appearance.picking)
            self.assertIn('256-color', ui.appearance.notice)
