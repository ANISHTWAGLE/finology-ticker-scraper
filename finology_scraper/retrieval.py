"""Browser retrieval, isolated from parsing and the HTTP interface."""

from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import get_ident
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout, sync_playwright

from .models import Basis, Issue


def utcnow():
    return datetime.now(timezone.utc)


class FetchFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.issue = Issue(code=code, message=message)


@dataclass
class Snapshot:
    html: str
    source_url: str
    scraped_at: datetime
    chart_text: str | None = None
    chart_url: str | None = None
    chart_at: datetime | None = None
    legends: list[str] = field(default_factory=list)
    chart_error: Issue | None = None


def launch_browser(playwright, timeout_ms):
    try:
        return playwright.chromium.launch(channel="chrome", headless=False,
                                           args=["--start-minimized"], timeout=timeout_ms)
    except BrowserTimeout as exc:
        raise FetchFailure("timeout", "Chrome launch timed out.") from exc
    except BrowserError as exc:
        raise FetchFailure("request_failed", "Cannot launch Chrome. Check its installation and desktop availability.") from exc


@contextmanager
def browser_session(shared_browser, timeout_ms):
    if shared_browser is not None:
        yield shared_browser
    else:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, timeout_ms)
            try:
                yield browser
            finally:
                browser.close()


class BrowserRetriever:
    """A reusable browser owned by one worker thread; every fetch gets a new context."""

    def __init__(self):
        self._owner = None
        self._playwright = None
        self._browser = None

    def _check_thread(self):
        if self._owner is not None and self._owner != get_ident():
            raise RuntimeError("BrowserRetriever must be used and closed on its owning thread.")

    def fetch(self, url: str, basis: Basis, *, evidence_dir: Path | None = None,
              timeout_ms: int = 30000) -> Snapshot:
        self._check_thread()
        if self._owner is None:
            self._owner = get_ident()
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = launch_browser(self._playwright, timeout_ms)
        return fetch_company(url, basis, evidence_dir=evidence_dir, timeout_ms=timeout_ms,
                             _browser=self._browser)

    def close(self):
        self._check_thread()
        try:
            if self._browser is not None:
                try:
                    self._browser.close()
                except BrowserError:
                    pass  # A disconnected/crashed browser is already closed.
        finally:
            self._browser = None
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            finally:
                self._playwright = None
                self._owner = None


def fetch_company(url: str, basis: Basis, *, evidence_dir: Path | None = None,
                  timeout_ms: int = 30000, _browser=None) -> Snapshot:
    """Fetch in a fresh unauthenticated context, optionally using an owned browser."""
    expected = urlsplit(url)
    with browser_session(_browser, timeout_ms) as browser:
        context = None
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(10000)
            chart = {}
            redirect_blocked = False

            def guard(route):
                nonlocal redirect_blocked
                request = route.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    dest = urlsplit(request.url)
                    if (dest.scheme != "https" or dest.hostname != "ticker.finology.in"
                            or dest.path.rstrip("/").casefold() != expected.path.rstrip("/").casefold()):
                        redirect_blocked = True
                        route.abort()
                        return
                route.continue_()

            def capture(response):
                parsed = urlsplit(response.url)
                if parsed.hostname == "ticker.finology.in" and parsed.path == "/GetShares.ashx":
                    chart["url"] = response.url
                    chart["at"] = utcnow()
                    if response.status == 200:
                        try:
                            chart["text"] = response.text()
                        except BrowserError:
                            chart["error"] = Issue(code="request_failed", message="Cannot read shareholding response body.")
                    else:
                        chart["error"] = Issue(code="request_failed", message=f"Shareholding endpoint returned HTTP {response.status}.")

            page.route("**/*", guard)
            page.on("response", capture)
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except BrowserError:
                if redirect_blocked:
                    raise FetchFailure("page_unavailable", "Company URL redirected away from the requested company page.")
                raise
            at = utcnow()
            if response is None:
                raise FetchFailure("request_failed", "No company-page response was received.")
            if response.status == 404:
                raise FetchFailure("company_not_found", "Finology returned HTTP 404 for this company.")
            if response.status in {401, 403, 429}:
                raise FetchFailure("access_blocked", f"Finology returned HTTP {response.status}; no access challenge was bypassed.")
            if response.status != 200:
                raise FetchFailure("page_unavailable", f"Company page returned HTTP {response.status}.")
            title = page.title().casefold()
            if any(t in title for t in ["attention required", "just a moment", "access denied"]):
                raise FetchFailure("access_blocked", "The response is an access challenge rather than company data.")
            if page.locator("#mainContent_ltrlCompName").count() != 1:
                raise FetchFailure("structure_changed", "Company identity element is missing or duplicated.")
            toggle = page.locator("#mainContent_togBtn")
            if toggle.count() != 1:
                raise FetchFailure("structure_changed", "The reporting-basis control is missing or duplicated.")
            actual = Basis.consolidated if toggle.is_checked() else Basis.standalone
            if actual != basis:
                raise FetchFailure("basis_unavailable", "The source reporting basis does not match the requested basis; no fallback was used.")
            legends = []
            if page.locator("#Share-chart-area").count():
                try:
                    # Read only the legend of the displayed chart, not unrelated application state.
                    page.wait_for_function("""() => typeof Chart !== 'undefined' &&
                        Object.values(Chart.instances).some(c => c.canvas.id === 'Share-chart-area' &&
                        c.legend && c.legend.legendItems && c.legend.legendItems.length > 0)""",
                        timeout=10000)
                    legends = page.evaluate("""() => Object.values(Chart.instances)
                        .find(c => c.canvas.id === 'Share-chart-area').legend.legendItems.map(x => x.text)""")
                except BrowserTimeout:
                    chart.setdefault("error", Issue(code="timeout", message="Shareholding chart did not finish rendering within 10 seconds."))
            html = page.content()
            if evidence_dir is not None:
                evidence_dir.mkdir(parents=True, exist_ok=True)
                (evidence_dir / "source.html").write_text(html, encoding="utf-8")
                if "text" in chart:
                    (evidence_dir / "chart.json").write_text(chart["text"], encoding="utf-8")
                import json
                (evidence_dir / "capture.json").write_text(json.dumps({
                    "source_url": page.url, "scraped_at": at.isoformat(),
                    "chart_url": chart.get("url"), "chart_at": chart.get("at").isoformat() if chart.get("at") else None,
                    "chart_legends": legends,
                }, indent=2), encoding="utf-8")
                for ident in ["mainContent_quarterly", "profit", "balance", "mainContent_cashflows", "mainContent_ShareHolding"]:
                    node = page.locator("#" + ident)
                    if node.count() and node.is_visible():
                        node.screenshot(path=str(evidence_dir / f"{ident}.png"), animations="disabled")
                # Hidden investor tab content is already in the HTML; render each tab for manual review.
                for tab_id, filename in [("pills-Promoter-tab", "investors-promoter.png"),
                                          ("pills-Investors-tab", "investors-public.png")]:
                    tab = page.locator("#" + tab_id)
                    if tab.count() and tab.is_visible():
                        tab.click()
                        pane_id = tab.get_attribute("aria-controls")
                        page.locator(f"#{pane_id}.show.active").wait_for(state="visible")
                        page.locator("#mainContent_investors").screenshot(
                            path=str(evidence_dir / filename), animations="disabled")
            return Snapshot(html=html, source_url=page.url, scraped_at=at,
                            chart_text=chart.get("text"), chart_url=chart.get("url"),
                            chart_at=chart.get("at"), legends=legends, chart_error=chart.get("error"))
        except FetchFailure:
            raise
        except BrowserTimeout as exc:
            raise FetchFailure("timeout", "The source request or browser operation timed out.") from exc
        except BrowserError as exc:
            raise FetchFailure("request_failed", "The browser could not load the source. Check Chrome installation, network access and source availability.") from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except BrowserError:
                    pass  # A browser crash may have already disposed of this context.
