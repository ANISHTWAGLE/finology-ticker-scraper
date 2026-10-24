"""Application orchestration, independent of FastAPI."""

from pathlib import Path

from bs4 import BeautifulSoup

from .input import InvalidCompanyInput, parse_company_input
from .models import Company, Issue, ScrapeRequest, ScrapeResponse, Section, Sections
from .parsing import SECTION_NAMES, StructureError, parse_company, parse_sections
from .retrieval import FetchFailure, fetch_company, utcnow


def failed_response(request: ScrapeRequest, identifier: str, url: str, issue: Issue) -> ScrapeResponse:
    return ScrapeResponse(status="failed", company=Company(identifier=identifier),
        requested_reporting_basis=request.reporting_basis, source_url=url, scraped_at=utcnow(),
        sections=Sections(**{key: Section(name=name, status="failed", errors=[issue])
                             for key, name in SECTION_NAMES.items()}), errors=[issue])


def scrape_company(request: ScrapeRequest, *, fetcher=fetch_company,
                    evidence_dir: Path | None = None) -> ScrapeResponse:
    code = "C" if request.reporting_basis.value == "consolidated" else "S"
    try:
        target = parse_company_input(request.company)
        if target.url_mode and target.url_mode != code:
            raise InvalidCompanyInput("URL mode conflicts with reporting_basis; provide matching values.")
    except InvalidCompanyInput as exc:
        return failed_response(request, request.company, "", Issue(code="invalid_company_input", message=str(exc)))
    url = f"https://ticker.finology.in/company/{target.identifier}?mode={code}"
    try:
        snapshot = fetcher(url, request.reporting_basis, evidence_dir=evidence_dir)
    except FetchFailure as exc:
        return failed_response(request, target.identifier, url, exc.issue)
    try:
        soup = BeautifulSoup(snapshot.html, "html.parser")
        company = parse_company(soup, target.identifier)
    except StructureError as exc:
        return failed_response(request, target.identifier, url, Issue(code="structure_changed", message=str(exc)))
    sections = parse_sections(soup, snapshot.source_url, snapshot.scraped_at, request.reporting_basis,
        chart_text=snapshot.chart_text, chart_url=snapshot.chart_url, chart_at=snapshot.chart_at,
        legends=snapshot.legends, chart_error=snapshot.chart_error)
    states = [getattr(sections, key).status for key in SECTION_NAMES]
    if all(s in {"ok", "warning"} for s in states):
        status = "complete_with_warnings" if "warning" in states else "complete"
    elif any(getattr(sections, key).tables for key in SECTION_NAMES):
        status = "partial"
    else:
        status = "failed"
    return ScrapeResponse(status=status, company=company, requested_reporting_basis=request.reporting_basis,
        source_url=snapshot.source_url, scraped_at=snapshot.scraped_at, sections=sections)


def response_http_status(result: ScrapeResponse) -> int:
    if result.status in {"complete", "complete_with_warnings"}:
        return 200
    if result.status == "partial":
        return 207
    code = result.errors[0].code if result.errors else "extraction_failed"
    return {"invalid_company_input": 422, "company_not_found": 404,
            "timeout": 504}.get(code, 502)
