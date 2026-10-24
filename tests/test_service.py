import pytest

from finology_scraper.models import ScrapeRequest
from finology_scraper.retrieval import FetchFailure
from finology_scraper.service import response_http_status, scrape_company


def request(company="TCS", basis="standalone"):
    return ScrapeRequest(company=company, reporting_basis=basis)


def test_full_fixture_result_with_approved_warning(captured_tcs):
    r = scrape_company(request(), fetcher=lambda *a, **kw: captured_tcs)
    assert r.status == "complete_with_warnings" and response_http_status(r) == 200
    assert r.company.name == "Tata Consultancy Services Ltd."
    assert r.company.nse_symbol == "TCS" and r.company.bse_code == "532540"
    assert r.scraped_at == captured_tcs.scraped_at


@pytest.mark.parametrize("code,http", [("company_not_found", 404), ("timeout", 504),
    ("request_failed", 502), ("page_unavailable", 502), ("access_blocked", 502),
    ("structure_changed", 502), ("basis_unavailable", 502)])
def test_page_failure_has_all_five_section_statuses(code, http):
    def fail(*a, **kw):
        raise FetchFailure(code, "Source failed")
    r = scrape_company(request(), fetcher=fail)
    assert r.status == "failed" and response_http_status(r) == http
    assert len(r.sections.model_dump()) == 5
    assert all(s["status"] == "failed" for s in r.sections.model_dump().values())


def test_conflicting_url_mode_is_rejected_before_fetching():
    def fail(*a, **kw):
        raise AssertionError("Network must not be used")
    r = scrape_company(request("https://ticker.finology.in/company/TCS?mode=C"), fetcher=fail)
    assert r.errors[0].code == "invalid_company_input" and response_http_status(r) == 422


def test_missing_chart_returns_partial_not_complete(captured_tcs):
    captured_tcs.chart_text = None
    r = scrape_company(request(), fetcher=lambda *a, **kw: captured_tcs)
    assert r.status == "partial" and response_http_status(r) == 207


def test_wrong_page_identity_is_rejected(captured_tcs):
    captured_tcs.html = "<h1>Maintenance</h1>"
    r = scrape_company(request(), fetcher=lambda *a, **kw: captured_tcs)
    assert r.status == "failed" and r.errors[0].code == "structure_changed"
