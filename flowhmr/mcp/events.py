"""Replayable UI notifications; idle clients wait on a condition, not HTTP polling."""
from collections import deque
from threading import Condition
from uuid import uuid4


class UIEvents:
    def __init__(self, capacity=256):
        self.condition = Condition()
        self.sequence = 0
        self.events = deque(maxlen=capacity)
        self.snapshots = {}
        self.commands = {}

    def request_command(self, command, timeout=5):
        with self.condition:
            token = uuid4().hex
            self.commands[token] = {'claimed': False, 'result': None}
            self.publish('viewer_command', {'request_id': token, 'command': command})
            try:
                self.condition.wait_for(lambda: self.commands[token]['result'] is not None, timeout)
                return self.commands[token]['result']
            finally:
                del self.commands[token]

    def claim_command(self, token):
        with self.condition:
            command = self.commands.get(token)
            if command is None or command['claimed']:
                return False
            command['claimed'] = True
            return True

    def reply_command(self, token, result):
        with self.condition:
            command = self.commands.get(token)
            if command is None or not command['claimed'] or command['result'] is not None:
                return False
            command['result'] = result
            self.condition.notify_all()
            return True

    def request_snapshot(self, timeout=5):
        with self.condition:
            token = uuid4().hex
            self.snapshots[token] = None
            self.publish('snapshot_request', {'request_id': token})
            try:
                self.condition.wait_for(lambda: self.snapshots[token] is not None, timeout)
                return self.snapshots[token]
            finally:
                del self.snapshots[token]

    def reply_snapshot(self, token, snapshot):
        with self.condition:
            if token not in self.snapshots or self.snapshots[token] is not None:
                return False
            self.snapshots[token] = snapshot
            self.condition.notify_all()
            return True

    def publish(self, kind, data=None):
        with self.condition:
            self.sequence += 1
            event = {'id': self.sequence, 'kind': kind, 'data': data or {}}
            self.events.append(event)
            self.condition.notify_all()
            return event

    def wait(self, after, timeout=15):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != after, timeout)
            if after > self.sequence or (self.events and after < self.events[0]['id'] - 1):
                return [{'id': self.sequence, 'kind': 'resync', 'data': {}}]
            return [event for event in self.events if event['id'] > after]
