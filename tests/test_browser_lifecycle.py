"""Lifecycle tests use fakes; live isolation is checked separately in the benchmark."""

from concurrent.futures import ThreadPoolExecutor
from threading import get_ident

from playwright.sync_api import Error as BrowserError
import pytest

from finology_scraper import retrieval
from finology_scraper.models import Basis


class FakeBrowser:
    def __init__(self):
        self.connected = True
        self.closed = False

    def is_connected(self):
        return self.connected

    def close(self):
        self.closed = True
        self.connected = False


def runtime(monkeypatch):
    events = []
    browsers = []

    class Driver:
        def stop(self):
            events.append(("stop", get_ident()))

    class Starter:
        def start(self):
            events.append(("start", get_ident()))
            return Driver()

    def launch(driver, timeout):
        events.append(("launch", get_ident()))
        browser = FakeBrowser()
        browsers.append(browser)
        return browser

    monkeypatch.setattr(retrieval, "sync_playwright", Starter)
    monkeypatch.setattr(retrieval, "launch_browser", launch)
    monkeypatch.setattr(retrieval, "fetch_company", lambda *a, **kw: kw["_browser"])
    return retrieval.BrowserRetriever(), events, browsers


def test_browser_and_driver_are_reused_on_one_thread(monkeypatch):
    retriever, events, browsers = runtime(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as worker:
        a = worker.submit(retriever.fetch, "https://ticker.finology.in/company/TCS?mode=S", Basis.standalone).result()
        b = worker.submit(retriever.fetch, "https://ticker.finology.in/company/INFY?mode=C", Basis.consolidated).result()
        worker.submit(retriever.close).result()
    assert a is b and len(browsers) == 1 and browsers[0].closed
    assert [e[0] for e in events] == ["start", "launch", "stop"]
    assert len({e[1] for e in events}) == 1


def test_disconnected_browser_is_replaced_on_the_next_request(monkeypatch):
    retriever, events, browsers = runtime(monkeypatch)
    first = retriever.fetch("https://ticker.finology.in/company/TCS?mode=S", Basis.standalone)
    first.connected = False
    second = retriever.fetch("https://ticker.finology.in/company/TCS?mode=C", Basis.consolidated)
    assert first is not second and len(browsers) == 2
    assert [e[0] for e in events] == ["start", "launch", "launch"]
    retriever.close()


def test_wrong_thread_cannot_use_or_close_the_browser(monkeypatch):
    retriever, _, _ = runtime(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(retriever.fetch, "https://ticker.finology.in/company/TCS?mode=S", Basis.standalone).result()
        try:
            with pytest.raises(RuntimeError, match="owning thread"):
                retriever.fetch("https://ticker.finology.in/company/TCS?mode=S", Basis.standalone)
            with pytest.raises(RuntimeError, match="owning thread"):
                retriever.close()
        finally:
            worker.submit(retriever.close).result()


def test_close_before_first_fetch_does_not_start_chrome(monkeypatch):
    retriever, events, browsers = runtime(monkeypatch)
    retriever.close()
    assert not events and not browsers


def test_context_is_closed_when_a_request_fails_before_navigation():
    class Context:
        closed = False

        def new_page(self):
            raise BrowserError("Synthetic page creation failure")

        def close(self):
            self.closed = True

    context = Context()

    class Browser:
        def new_context(self):
            return context

    with pytest.raises(retrieval.FetchFailure) as exc:
        retrieval.fetch_company("https://ticker.finology.in/company/TCS?mode=S", Basis.standalone,
                                _browser=Browser())
    assert exc.value.issue.code == "request_failed" and context.closed
