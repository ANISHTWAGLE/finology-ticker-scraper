"""Reuse identical investor detail requests within one company's two-basis scrape."""

from concurrent.futures import Future
import re
from threading import Lock


INVESTOR_URL = re.compile(
    r'https://www\.screener\.in/api/3/\d+/investors/[a-z_]+/(?:quarterly|yearly)/'
)


class SharedInvestorDetails:
    def __init__(self):
        self.responses = {}
        self.lock = Lock()

    def get(self, fetch, url, **kwargs):
        if not INVESTOR_URL.fullmatch(url):
            return fetch(url, **kwargs)
        with self.lock:
            future = self.responses.get(url)
            if future is None:
                future = self.responses[url] = Future()
                owner = True
            else:
                owner = False

        if owner:
            try:
                response = fetch(url, **kwargs)
            except BaseException as exc:
                with self.lock:
                    future.set_exception(exc)
                    del self.responses[url]
                raise
            future.set_result(response)
        return future.result()
