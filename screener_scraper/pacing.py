"""Process-wide request pacing shared by single and bulk Screener retrieval."""

import time
from threading import Lock


class RequestGate:
    def __init__(self, interval=2.0, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self.lock = Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0

    def acquire(self):
        from .retrieval import SourceError
        while True:
            with self.lock:
                now = self.clock()
                if now < self.blocked_until:
                    raise SourceError(429, 'Screener rate limit cooldown is active. Retry later.')
                delay = self.next_request - now
                if delay <= 0:
                    self.next_request = now + self.interval
                    return
            self.sleep(delay)

    def cooldown(self, seconds=60):
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + max(60, seconds))


source_gate = RequestGate()
