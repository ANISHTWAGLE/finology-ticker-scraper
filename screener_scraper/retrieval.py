"""Unauthenticated HTTP first; fresh Chrome context only on HTTP 403."""

import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from playwright.sync_api import sync_playwright, Error as BrowserError


class SourceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def check_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != "www.screener.in" or p.username:
        raise SourceError(502, "Unexpected source URL or redirect.")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Retriever:
    def __init__(self):
        self.opener = build_opener(SafeRedirect())
        self.next_request = 0.0
        self.blocked_until = 0.0

    def get(self, url, *, browser_fallback=False):
        check_url(url)
        if time.monotonic() < self.blocked_until:
            raise SourceError(429, "Screener rate limit cooldown is active. Retry later.")
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + 2
        try:
            with self.opener.open(url, timeout=30) as response:
                if urlsplit(response.url).path.startswith('/login'):
                    raise SourceError(401, "This data requires login.")
                return response.read().decode("utf-8"), response.url, "http"
        except HTTPError as exc:
            if exc.code == 429:
                retry = exc.headers.get("Retry-After", "60")
                self.blocked_until = time.monotonic() + max(60, int(retry) if retry.isdigit() else 60)
            if exc.code == 403 and browser_fallback:
                return self.browser_get(url)
            raise SourceError(exc.code, f"Screener returned HTTP {exc.code}.") from exc
        except (TimeoutError, URLError) as exc:
            raise SourceError(504, "Screener HTTP request failed or timed out.") from exc

    def browser_get(self, url):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=False, args=["--start-minimized"])
                try:
                    context = browser.new_context()
                    page = context.new_page()

                    def guard(route):
                        if route.request.is_navigation_request():
                            try:
                                check_url(route.request.url)
                            except SourceError:
                                route.abort()
                                return
                        route.continue_()

                    page.route("**/*", guard)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if response is None or response.status >= 400:
                        status = response.status if response else 502
                        if status == 429:
                            self.blocked_until = time.monotonic() + 60
                        raise SourceError(status, f"Screener browser request failed ({status}).")
                    check_url(page.url)
                    if urlsplit(page.url).path.startswith('/login'):
                        raise SourceError(401, "This data requires login.")
                    return page.content(), page.url, "browser"
                finally:
                    browser.close()
        except BrowserError as exc:
            raise SourceError(502, "Chrome retrieval failed.") from exc
