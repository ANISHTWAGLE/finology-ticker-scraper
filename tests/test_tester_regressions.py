"""Regression checks for audit fixes; saved source/TestClient, not live scraping."""

from copy import deepcopy

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from finology_scraper import api
from finology_scraper.models import ScrapeRequest
from finology_scraper.service import response_http_status, scrape_company


def parse_modified_snapshot(snapshot, soup):
    snapshot.html = str(soup)
    return scrape_company(
        ScrapeRequest(company="TCS", reporting_basis="standalone"),
        fetcher=lambda *args, **kwargs: snapshot,
    )


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("template", [
    '{{"company":{number},"reporting_basis":"standalone"}}',
    '{{"company":"TCS","reporting_basis":{number}}}',
    '{{"company":"TCS","reporting_basis":"standalone","extra":{number}}}',
    '{{"company":"TCS","reporting_basis":"standalone","extra":{{"nested":[{number}]}}}}',
])
def test_nonfinite_request_value_returns_validation_json(number, template):
    body = template.format(number=number)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post("/scrape", content=body,
                               headers={"Content-Type": "application/json"})
        assert client.get("/health").status_code == 200
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("remove", [False, True])
def test_investor_container_change_cannot_report_complete(captured_tcs, remove):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    soup.find(id="mainContent_investors")["id"] = "investor-details"
    # Both source tables and their data are still present in the document.
    assert soup.select_one("#pills-Promoter tbody tr") is not None
    assert soup.select_one("#pills-Investors tbody tr") is not None
    if remove:
        soup.find(id="investor-details").decompose()
    result = parse_modified_snapshot(captured_tcs, soup)
    share = result.sections.shareholding_pattern
    assert result.status == "partial" and response_http_status(result) == 207
    assert share.status == "partial" and len(share.tables) == 2
    assert any(issue.code == "structure_changed" for issue in share.errors)
    assert all(getattr(result.sections, key).status == "ok" for key in
               ["quarterly_results", "profit_and_loss", "balance_sheet", "cash_flow"])


@pytest.mark.parametrize("empty_body", [True, False])
def test_empty_pledging_is_reported_even_when_chart_has_data(captured_tcs, empty_body):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    body = soup.find(id="mainContent_ShareHolding").find("tbody")
    if empty_body:
        body.clear()
    else:
        for row in body.find_all("tr"):
            for cell in row.find_all(["td", "th"], recursive=False)[1:]:
                cell.string = "-"
    result = parse_modified_snapshot(captured_tcs, soup)
    share = result.sections.shareholding_pattern
    assert any(issue.code == "table_empty" for issue in share.errors)
    assert share.status == "partial" and len(share.tables) == 4
    assert result.status == "partial" and response_http_status(result) == 207


@pytest.mark.parametrize("container_id,key", [
    ("mainContent_quarterly", "quarterly_results"), ("profit", "profit_and_loss"),
    ("balance", "balance_sheet"), ("mainContent_cashflows", "cash_flow"),
])
def test_duplicate_financial_container_is_not_silently_selected(captured_tcs, container_id, key):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    original = soup.find(id=container_id)
    duplicate = deepcopy(original)
    duplicate.find("tbody").find("tr").find("td").string = "999999999"
    original.insert_after(duplicate)
    result = parse_modified_snapshot(captured_tcs, soup)
    section = getattr(result.sections, key)
    assert section.status == "failed"
    assert any(issue.code == "structure_changed" for issue in section.errors)
    assert section.tables == []
    assert result.status == "partial" and response_http_status(result) == 207
