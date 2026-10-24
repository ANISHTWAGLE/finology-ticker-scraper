from fastapi.testclient import TestClient

from finology_scraper import api
from finology_scraper.service import scrape_company


def test_http_response_and_health_with_fixture_not_live_source(monkeypatch, captured_tcs):
    monkeypatch.setattr(api, "scrape_company", lambda body, **kw:
                        scrape_company(body, fetcher=lambda *a, **k: captured_tcs))
    with TestClient(api.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/scrape", json={"company": "TCS", "reporting_basis": "standalone"})
    assert response.status_code == 200
    assert response.json()["status"] == "complete_with_warnings"
    assert response.json()["sections"]["shareholding_pattern"]["tables"][0]["periods"] == [None]


def test_basis_is_required_and_extra_fields_are_not_silently_ignored():
    with TestClient(api.app) as client:
        assert client.post("/scrape", json={"company": "TCS"}).status_code == 422
        assert client.post("/scrape", json={"company": "TCS", "reporting_basis": "both"}).status_code == 422
        assert client.post("/scrape", json={"company": "TCS", "reporting_basis": "standalone", "unknown": True}).status_code == 422


def test_invalid_company_url_does_not_reach_browser():
    with TestClient(api.app) as client:
        response = client.post("/scrape", json={"company": "https://example.com", "reporting_basis": "standalone"})
    assert response.status_code == 422 and response.json()["status"] == "failed"


def test_internal_failure_is_explicit(monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("Test failure")
    monkeypatch.setattr(api, "scrape_company", fail)
    with TestClient(api.app) as client:
        r = client.post("/scrape", json={"company": "TCS", "reporting_basis": "standalone"})
    assert r.status_code == 502 and r.json()["errors"][0]["code"] == "internal_error"


def test_concurrent_request_is_explicitly_busy(monkeypatch, captured_tcs):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    started, release = Event(), Event()

    def slow(body, **kw):
        started.set()
        assert release.wait(timeout=5)
        return scrape_company(body, fetcher=lambda *a, **k: captured_tcs)

    monkeypatch.setattr(api, "scrape_company", slow)
    with TestClient(api.app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/scrape", json={"company": "TCS", "reporting_basis": "standalone"})
        try:
            assert started.wait(timeout=5)
            busy = client.post("/scrape", json={"company": "INFY", "reporting_basis": "standalone"})
            assert busy.status_code == 503
            assert busy.json()["errors"][0]["code"] == "service_busy"
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200


def test_cancelled_request_keeps_slot_until_browser_work_finishes(monkeypatch, captured_tcs):
    import asyncio
    from threading import Event
    from starlette.requests import Request
    import pytest

    from finology_scraper.models import ScrapeRequest

    started, release = Event(), Event()

    def slow(body, **kw):
        started.set()
        assert release.wait(timeout=5)
        return scrape_company(body, fetcher=lambda *a, **k: captured_tcs)

    monkeypatch.setattr(api, "scrape_company", slow)

    async def exercise():
        async with api.lifespan(api.app):
            body = ScrapeRequest(company="TCS", reporting_basis="standalone")
            request = Request({"type": "http", "app": api.app})
            pending = asyncio.create_task(api.scrape(body, request))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                pending.cancel()
                await asyncio.sleep(0)
                assert api.app.state.scrape_lock.locked() and not pending.done()
                assert (await api.scrape(body, request)).status_code == 503
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert not api.app.state.scrape_lock.locked()
            assert (await api.scrape(body, request)).status_code == 200

    asyncio.run(exercise())
