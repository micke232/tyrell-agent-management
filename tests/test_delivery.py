import unittest

from tyrell.ui import Dashboard
from tyrell.presentation import timeline
from tyrell.ui import crop, wrap


class DeliveryTests(unittest.TestCase):
    def ui(self):
        ui = Dashboard("/tmp")
        ui.data = {"connected": True, "threads": {"t": {"id": "t", "name": "Agent", "items": [], "status": {"type": "idle"}}}}
        ui.selected = "thread:t"
        ui.update()
        return ui

    def test_immediate_sending_delivery_and_server_echo_are_one_message(self):
        ui = self.ui()
        ui.buffer = "Hello agent"
        ui.entered()
        action, params = ui.commands.get_nowait()
        items = ui.display_items(ui.current())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Hello agent")
        self.assertEqual(items[0]["delivery"], "sending")
        self.assertEqual(ui.buffer, "")
        ui.updates.put(("done", (action, {}, params)))
        ui.update()
        self.assertEqual(ui.display_items(ui.current())[0]["delivery"], "delivered")
        self.assertNotIn("Message sent", ui.notice)
        ui.current()["items"] = [{"id": "server-id", "clientId": params["clientId"], "type": "userMessage", "text": "Hello agent"}]
        items = ui.display_items(ui.current())
        self.assertEqual(len(items), 1)
        self.assertFalse(ui.pending_messages)
        self.assertIn("YOU · Delivered", timeline(items, 80, "chat", wrap, crop)[0]["text"])

    def test_repeated_identical_messages_are_matched_by_id_and_failure_is_visible(self):
        ui = self.ui()
        ui.submit("send", threadId="t", text="same text")
        ui.submit("send", threadId="t", text="same text")
        first, second = ui.commands.get_nowait(), ui.commands.get_nowait()
        self.assertNotEqual(first[1]["clientId"], second[1]["clientId"])
        self.assertEqual(len(ui.display_items(ui.current())), 2)
        ui.current()["items"] = [{"id": "server", "clientId": first[1]["clientId"], "type": "userMessage", "text": "same text"}]
        self.assertEqual(len(ui.display_items(ui.current())), 2)
        ui.updates.put(("error", ("Connection lost", second[0], second[1])))
        ui.update()
        self.assertEqual(ui.display_items(ui.current())[-1]["delivery"], "unconfirmed")
        self.assertEqual(ui.buffer, "same text")
