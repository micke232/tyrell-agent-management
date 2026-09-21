import unittest
from unittest.mock import patch

from tyrell.presentation import TimelineCache, timeline
from tyrell.ui import crop, wrap
from test_native_clipboard import fixture


class ResponsivenessTests(unittest.TestCase):
    def test_task_status_does_not_stop_snapshot_polling(self):
        ui, _ = fixture()
        task = {"id": "planned", "status": "planned"}
        ui.selected, ui.rows = "task:planned", [("task:planned", task)]
        calls = []
        def request(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                ui.stopped.set()
            return {"threads": {}, "tasks": [task]}
        with patch("tyrell.ui.request", side_effect=request), patch.object(ui.refresh_requested, "wait"):
            ui.polling()
        self.assertEqual(len(calls), 2)
        self.assertEqual(ui.updates.qsize(), 2)
        self.assertTrue(all(call["threadId"] is None for call in calls))

    def test_activity_accepts_task_and_agent_status_shapes(self):
        ui, _ = fixture()
        for status in ("planned", "running", None, {}, {"type": "idle"}):
            self.assertFalse(ui.is_active({"status": status}))
        self.assertTrue(ui.is_active({"status": {"type": "active"}}))

    def test_only_text_cursor_blinks_while_prompt_and_text_stay_stable(self):
        import curses
        from test_native_clipboard import Screen
        ui, _ = fixture()
        ui.focus = 'chat'
        ui.buffer = 'Keep this readable'
        self.assertEqual(ui.cursor_blink_phase(0.1), ui.cursor_blink_phase(0.8))
        self.assertNotEqual(ui.cursor_blink_phase(0.1), ui.cursor_blink_phase(0.9))
        styles = []
        for now in (0.1, 0.9):
            screen = Screen()
            with patch('tyrell.ui.time.monotonic', return_value=now), patch('tyrell.ui.curses.curs_set') as cursor:
                ui.render(screen)
                cursor.assert_called_with(1 if now == 0.1 else 0)
            styles.append(next(style for y, _, text, style in screen.draws if y == ui.draft_top-1 and text.startswith('╭')))
            draft = next(style for _, _, text, style in screen.draws if text == ui.buffer)
            self.assertFalse(draft & curses.A_DIM)
        self.assertEqual(styles[0], styles[1])
        ui.focus = 'sidebar'
        self.assertIsNone(ui.cursor_blink_phase(0.9))
        ui.focus, ui.panel = 'chat', 'HUB SETTINGS'
        self.assertIsNone(ui.cursor_blink_phase(0.9))

    def test_cursor_stays_visible_during_typing_then_resumes_blinking(self):
        ui, _ = fixture()
        ui.focus = 'chat'
        for now in (1.0, 1.4, 1.8, 2.2):
            with patch('tyrell.ui.time.monotonic', return_value=now):
                ui.key('a')
            self.assertEqual(ui.cursor_blink_phase(now + .4), 0)
        self.assertEqual(ui.cursor_blink_phase(3.1), 1)
        with patch('tyrell.ui.time.monotonic', return_value=3.2):
            ui.key('b')
        self.assertEqual(ui.cursor_blink_phase(3.3), 0)

    def test_already_queued_snapshot_for_previous_agent_is_discarded(self):
        ui, _ = fixture()
        ui.updates.put(('snapshot_for', ('thread:other', {'threads': {}})))
        ui.update()
        self.assertEqual(ui.current()['id'], 'fixture')

    def test_unchanged_snapshot_does_not_request_idle_repaint_but_reply_does(self):
        import copy
        ui, _ = fixture()
        snapshot = copy.deepcopy(ui.data)
        ui.updates.put(('snapshot_for', (ui.selected, snapshot)))
        self.assertFalse(ui.update())
        snapshot = copy.deepcopy(snapshot)
        snapshot['threads']['fixture']['items'].append({'id': 'new', 'type': 'agentMessage', 'text': 'New reply'})
        ui.updates.put(('snapshot_for', (ui.selected, snapshot)))
        self.assertTrue(ui.update())
        self.assertEqual(ui.current()['items'][-1]['text'], 'New reply')

    def test_width_fast_paths_preserve_unicode_and_control_sanitization(self):
        import unicodedata
        from tyrell.ui import clean, cells, cell_width
        samples = ['', 'plain ASCII', 'åäö', '猫🙂', 'e\u0301', '\x1b[31mred\x1b[0m\ttext', '\u0301']
        for text in samples:
            cleaned = clean(text)
            expected_width = sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in cleaned)
            self.assertEqual(cells(cleaned), expected_width)
            for width in range(-1, expected_width + 2):
                result, used = '', 0
                for c in cleaned:
                    n = 0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
                    if used + n > width:
                        break
                    used += n
                    result += c
                self.assertEqual(crop(text, width), result)
        self.assertEqual(cell_width.cache_info().maxsize, 4096)

    def test_streamed_reply_formats_only_changed_message(self):
        items = [{'id': str(i), 'type': 'agentMessage', 'text': '**message** ' + str(i)} for i in range(120)]
        cache = TimelineCache()
        baseline = cache.render(items, 80, 'chat', wrap, crop, 'a')
        self.assertEqual(baseline, timeline(items, 80, 'chat', wrap, crop))
        items[-1]['text'] += ' new token'
        with patch('tyrell.presentation.timeline', wraps=timeline) as format_rows:
            actual = cache.render(items, 80, 'chat', wrap, crop, 'a')
            format_rows.assert_called_once()
        self.assertEqual(actual, timeline(items, 80, 'chat', wrap, crop))

    def test_cache_discards_old_versions_deleted_items_and_other_agents(self):
        cache = TimelineCache()
        items = [{'id': 'a', 'type': 'agentMessage', 'text': 'text'}, {'id': 'b', 'type': 'userMessage', 'text': 'draft'}]
        for i in range(100):
            items[0]['text'] += 'x'
            cache.render(items, 80, 'chat', wrap, crop, 'a')
            self.assertEqual(len(cache.entries), 2)
        cache.render(items[:1], 80, 'chat', wrap, crop, 'a')
        self.assertEqual(len(cache.entries), 1)
        other = [{'id': 'a', 'type': 'agentMessage', 'text': 'other agent'}]
        self.assertEqual(cache.render(other, 40, 'chat', wrap, crop, 'b'), timeline(other, 40, 'chat', wrap, crop))

    def test_polling_discards_previous_agent_response_and_immediately_fetches_new_agent(self):
        ui, _ = fixture()
        calls = []
        def request(*args, **kwargs):
            calls.append(kwargs['threadId'])
            if len(calls) == 1:
                ui.selected = 'thread:other'
            else:
                ui.stopped.set()
                ui.refresh_requested.set()
            return {'threads': {}, 'marker': len(calls)}
        with patch('tyrell.ui.request', side_effect=request):
            ui.polling()
        self.assertEqual(calls, ['fixture', 'other'])
        self.assertEqual(ui.updates.get_nowait(), ('snapshot_for', ('thread:other', {'threads': {}, 'marker': 2})))
        self.assertTrue(ui.updates.empty())

    def test_send_and_worker_completion_wake_polling(self):
        ui, _ = fixture()
        ui.demo = False
        ui.submit('send', threadId='fixture', text='hello', clientId='fresh')
        self.assertTrue(ui.refresh_requested.is_set())
        self.assertEqual(ui.display_items(ui.current())[-1]['text'], 'hello')
        ui.refresh_requested.clear()
        def request(*args, **kwargs):
            ui.stopped.set()
            return {}
        with patch('tyrell.ui.request', side_effect=request):
            ui.worker()
        self.assertTrue(ui.refresh_requested.is_set())


if __name__ == '__main__':
    unittest.main()
