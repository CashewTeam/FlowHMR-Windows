"""Bounded backend-owned operation receipts for desktop/MCP synchronization."""
from collections import deque
from threading import Lock
import time
from uuid import uuid4


class Activity:
    def __init__(self):
        self.rows = deque()
        self.lock = Lock()

    def record(self, value):
        with self.lock:
            if value['state'] == 'running':
                row = {'id': uuid4().hex, 'tool': value['tool'], 'state': 'running', 'created': time.time()}
                self.rows.appendleft(row)
                if len(self.rows) > 100:
                    completed = next((old for old in reversed(self.rows) if old['state'] != 'running'), None)
                    if completed is not None:
                        self.rows.remove(completed)
            else:
                row = next((row for row in self.rows if row['id'] == value.get('id')), None)
                if row is None:
                    raise ValueError('Unknown activity receipt')
                row.update(state=value['state'], finished=time.time(),
                           details=value.get('details', {}), error=value.get('error'))
            return dict(row)

    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.rows]
