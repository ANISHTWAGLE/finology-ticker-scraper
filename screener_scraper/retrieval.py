"""HTTP retrieval with paced, isolated Chrome or Lightpanda browser sessions."""

import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from playwright.sync_api import sync_playwright, Error as BrowserError
from .pacing import source_gate


class SourceError(Exception):
    def __init__(self, status, message, *, registration_required=False):
        super().__init__(message)
        self.status = status
        self.registration_required = registration_required


def registration_redirect(status, headers):
    """Recognize Screener's explicit login or registration redirect on a 403."""
    if status != 403:
        return False
    target = headers.get('X-Redirect-To', headers.get('x-redirect-to', ''))
    path = target.partition('?')[0].partition('#')[0]
    return path in ('/register', '/register/', '/login', '/login/')


def check_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != "www.screener.in" or p.username:
        raise SourceError(502, "Unexpected source URL or redirect.")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Retriever:
    def __init__(self, *, gate=None, browser_backend='chrome', browser_only=False, http_fallback=True,
                 shared_investors=None):
        if browser_backend not in ('chrome', 'lightpanda'):
            raise ValueError('Unsupported Screener browser backend.')
        self.opener = build_opener(SafeRedirect())
        self.gate = gate if gate is not None else source_gate
        self.browser_backend = browser_backend
        self.browser_only = browser_only
        self.http_fallback = http_fallback
        self.shared_investors = shared_investors

    def get(self, url, *, browser_fallback=False):
        check_url(url)
        if self.shared_investors is not None:
            return self.shared_investors.get(self._fetch, url, browser_fallback=browser_fallback)
        return self._fetch(url, browser_fallback=browser_fallback)

    def _fetch(self, url, *, browser_fallback):
        if self.browser_only:
            return self.browser_get(url)
        self.gate.acquire()
        try:
            with self.opener.open(url, timeout=30) as response:
                if urlsplit(response.url).path.startswith('/login'):
                    raise SourceError(401, "This data requires login.")
                return response.read().decode("utf-8"), response.url, "http"
        except HTTPError as exc:
            if exc.code == 429:
                retry = exc.headers.get("Retry-After", "60")
                self.gate.cooldown(int(retry) if retry.isdigit() else 60)
            if exc.code == 403 and browser_fallback and self.http_fallback:
                return self.browser_get(url)
            if registration_redirect(exc.code, exc.headers):
                raise SourceError(exc.code, 'Screener requires registration or login for this detail.',
                                  registration_required=True) from exc
            raise SourceError(exc.code, f"Screener returned HTTP {exc.code}.") from exc
        except (TimeoutError, URLError) as exc:
            raise SourceError(504, "Screener HTTP request failed or timed out.") from exc

    def browser_get(self, url):
        check_url(url)
        self.gate.acquire()
        try:
            with sync_playwright() as p:
                if self.browser_backend == 'lightpanda':
                    endpoint = os.environ.get('SCREENER_LIGHTPANDA_CDP_URL', 'ws://127.0.0.1:19222')
                    browser = p.chromium.connect_over_cdp(endpoint, timeout=30000)
                else:
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
                            self.gate.cooldown()
                        if response and registration_redirect(status, response.headers):
                            raise SourceError(status, 'Screener requires registration or login for this detail.',
                                              registration_required=True)
                        raise SourceError(status, f"Screener browser request failed ({status}).")
                    check_url(page.url)
                    if urlsplit(page.url).path.startswith('/login'):
                        raise SourceError(401, "This data requires login.")
                    # Expansion endpoints return JSON, not a company document.
                    content = response.text() if urlsplit(page.url).path.startswith('/api/') else page.content()
                    return content, page.url, "browser"
                finally:
                    browser.close()
        except BrowserError as exc:
            raise SourceError(502, f"{self.browser_backend.title()} retrieval failed.") from exc
