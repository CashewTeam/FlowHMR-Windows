import threading
import unittest
from flowhmr.mcp.events import UIEvents


class UIEventsTest(unittest.TestCase):
    def test_commands_are_claimed_once_and_expire(self):
        events = UIEvents()
        result = []
        thread = threading.Thread(target=lambda: result.append(events.request_command({'action': 'seek'}, timeout=1)))
        thread.start()
        token = events.wait(0, timeout=1)[0]['data']['request_id']
        self.assertFalse(events.reply_command(token, {'frame': 3}))
        self.assertTrue(events.claim_command(token))
        self.assertFalse(events.claim_command(token))
        self.assertTrue(events.reply_command(token, {'frame': 3}))
        thread.join(2)
        self.assertEqual(result, [{'frame': 3}])
        self.assertFalse(events.claim_command(token))
        self.assertIsNone(events.request_command({'action': 'pause'}, timeout=0))

    def test_snapshot_is_correlated_and_expires(self):
        events = UIEvents()
        result = []
        thread = threading.Thread(target=lambda: result.append(events.request_snapshot(timeout=1)))
        thread.start()
        request = events.wait(0, timeout=1)[0]
        token = request['data']['request_id']
        self.assertFalse(events.reply_snapshot('wrong', {}))
        self.assertTrue(events.reply_snapshot(token, {'frame': 17}))
        thread.join(2)
        self.assertEqual(result, [{'frame': 17}])
        self.assertFalse(events.reply_snapshot(token, {'frame': 18}))
        self.assertIsNone(events.request_snapshot(timeout=0))

    def test_idle_wait_and_publish_wakeup(self):
        events = UIEvents()
        self.assertEqual(events.wait(0, timeout=0), [])
        entered = threading.Event()
        result = []
        def listen():
            entered.set()
            result.extend(events.wait(0, timeout=2))
        listener = threading.Thread(target=listen)
        listener.start()
        entered.wait(1)
        sent = events.publish('jobs', {'job_id': 'test'})
        listener.join(2)
        self.assertFalse(listener.is_alive())
        self.assertEqual(result, [sent])

    def test_replay_and_expired_cursor(self):
        events = UIEvents(capacity=2)
        events.publish('jobs')
        second = events.publish('characters')
        third = events.publish('exports')
        self.assertEqual(events.wait(1, timeout=0), [second, third])
        self.assertEqual(events.wait(0, timeout=0)[0]['kind'], 'resync')
        self.assertEqual(events.wait(999, timeout=0)[0]['kind'], 'resync')
        self.assertEqual(events.wait(3, timeout=0), [])
