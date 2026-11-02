"""Offline contract and parsing tests, not live source verification."""

import json
import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError
from screener_scraper.models import ScreenerRequest
from screener_scraper.parsing import period, cell, parse_table
from screener_scraper.service import scrape, expansion_url
from screener_scraper.retrieval import SourceError, check_url


HTML = '''<h1>MRF Ltd</h1><div id="company-info" data-company-id="2141" data-consolidated="true"></div>
<a href="https://www.nseindia.com/">NSE: MRF</a><a href="https://www.bseindia.com/">BSE: 500290</a>
<section id="quarters"><p class="sub">Consolidated Figures in Rs. Crores</p>
<table class="data-table"><thead><tr><th></th><th>Jun 2026</th><th>TTM</th></tr></thead>
<tbody><tr><td><button onclick="Company.showSchedule('Sales', 'quarters', this)">Sales +</button></td>
<td>8,416</td><td></td></tr><tr><td>OPM %</td><td>12%</td><td>0%</td></tr></tbody></table></section>'''


class FakeRetriever:
    def __init__(self, fail_nse=False):
        self.calls = []
        self.fail_nse = fail_nse

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail_nse and '/company/MRF/' in url:
            raise SourceError(404, 'not found')
        if '/schedules/' in url:
            return json.dumps({'Growth %': {'Jun 2026': '9.64%', 'TTM': None}}), url, 'http'
        return HTML, url, 'http'


def test_periods_preserve_irregular_and_undated_labels():
    p = period('Mar 2016 18m')
    assert (p.year, p.month, p.duration_months) == (2016, 3, 18)
    assert period('TTM').year is None
    assert period('Sep 2026').month == 9


def test_numeric_units_and_missing_zero():
    assert cell('0', 'Sales', 'quarters', 'Rs. Crores').numeric_value == 0
    assert cell('', 'Sales', 'quarters', None).status == 'missing'
    assert cell('-1,234.50', 'EPS in Rs', 'quarters', 'Rs. Crores').unit == 'INR per share'
    assert cell('12%', 'OPM %', 'quarters', 'Rs. Crores').unit == 'percent'
    assert cell('xx.xx', 'Sales', 'quarters', None).status == 'unparsed'


def test_reject_misaligned_table():
    soup = BeautifulSoup(HTML.replace('<td>8,416</td>', ''), 'html.parser')
    with pytest.raises(ValueError, match='period headers'):
        parse_table(soup.table, 'quarters', 'quarters', None, 'https://www.screener.in/')


@pytest.mark.parametrize('data', [{}, {'nse_symbol':'https://evil.test/'}, {'bse_code':'123'}, {'nse_symbol':'MRF','extra':1}])
def test_invalid_requests(data):
    with pytest.raises(ValidationError):
        ScreenerRequest(reporting_basis='consolidated', **data)


def test_expansion_and_bse_fallback():
    fetcher = FakeRetriever(fail_nse=True)
    result = scrape(ScreenerRequest(nse_symbol='MRF', bse_code='500290', reporting_basis='consolidated'), fetcher, ['quarters'])
    assert result.status == 'complete'
    assert result.company.nse_symbol == 'MRF'
    table = result.sections['quarterly_results'].tables[0]
    assert table.rows[-1].parent == 'Sales +'
    assert table.rows[-1].values[0].numeric_value == 9.64
    assert '/company/500290/' in fetcher.calls[1]
    assert result.warnings[0].code == 'bse_fallback'


def test_rate_limit_never_triggers_bse_fallback():
    class Limited(FakeRetriever):
        def get(self, url, **kwargs):
            self.calls.append(url)
            raise SourceError(429, 'rate limited')
    fetcher = Limited()
    with pytest.raises(SourceError) as exc:
        scrape(ScreenerRequest(nse_symbol='MRF', bse_code='500290', reporting_basis='consolidated'), fetcher, ['quarters'])
    assert exc.value.status == 429
    assert len(fetcher.calls) == 1


def test_basis_mismatch_is_not_silently_accepted():
    with pytest.raises(SourceError, match='reporting basis'):
        scrape(ScreenerRequest(nse_symbol='MRF', reporting_basis='standalone'), FakeRetriever(), ['quarters'])


def test_conflicting_identifiers_rejected():
    with pytest.raises(SourceError, match='same company'):
        scrape(ScreenerRequest(nse_symbol='MRF', bse_code='532540', reporting_basis='consolidated'), FakeRetriever(), ['quarters'])


def test_only_safe_source_urls_and_known_controls():
    assert 'parent=Material+Cost+%25' in expansion_url(
        'Company.showSchedule("Material Cost %", "profit-loss", this)', '2141', 'consolidated')
    with pytest.raises(SourceError):
        check_url('https://www.screener.in.evil.test/company/MRF/')
    with pytest.raises(ValueError):
        expansion_url('arbitraryJavaScript()', '2141', 'consolidated')


def test_endpoint_contract_and_busy_response(monkeypatch):
    from fastapi.testclient import TestClient
    from finology_scraper.api import app
    from screener_scraper import api
    monkeypatch.setattr(api, 'retriever', FakeRetriever())
    with TestClient(app) as client:
        body = {'nse_symbol': 'MRF', 'reporting_basis': 'consolidated'}
        response = client.post('/screener/scrape', json=body)
        assert response.status_code == 207  # Fixture contains only quarterly results.
        assert len(response.json()['sections']) == 6
        assert client.post('/screener/scrape', json={'nse_symbol': 'MRF'}).status_code == 422
        with api.lock:
            assert client.post('/screener/scrape', json=body).status_code == 503


def test_detail_rate_limit_returns_explicit_partial(monkeypatch):
    class LimitedDetails(FakeRetriever):
        def get(self, url, **kwargs):
            if '/schedules/' in url:
                raise SourceError(429, 'rate limited')
            return super().get(url, **kwargs)
    result = scrape(ScreenerRequest(nse_symbol='MRF', reporting_basis='consolidated'), LimitedDetails(), ['quarters'])
    assert result.status == 'partial'
    assert result.sections['quarterly_results'].issues[0].code == 'detail_unavailable'


def test_duration_badge_maps_to_detail_date_without_losing_label():
    class Duration(FakeRetriever):
        def get(self, url, **kwargs):
            if '/schedules/' in url:
                return json.dumps({'Growth %': {'Mar 2016': '4%'}}), url, 'http'
            return HTML.replace('Jun 2026', 'Mar 2016 18m'), url, 'http'
    result = scrape(ScreenerRequest(nse_symbol='MRF', reporting_basis='consolidated'), Duration(), ['quarters'])
    table = result.sections['quarterly_results'].tables[0]
    assert result.status == 'complete'
    assert table.periods[0].label == 'Mar 2016 18m'
    assert table.rows[-1].values[0].numeric_value == 4


def test_growth_summary_preserves_relative_periods():
    from screener_scraper.parsing import parse_range_table
    soup = BeautifulSoup('<table><tr><th>Sales Growth</th></tr><tr><td>10 Years:</td><td>4%</td></tr></table>', 'html.parser')
    table = parse_range_table(soup.table, 'https://www.screener.in/')
    assert table.periods[0].label == '10 Years:'
    assert table.periods[0].year is None
    assert table.rows[0].values[0].unit == 'percent'


def test_all_sections_and_shareholding_tabs_are_kept_separate():
    from screener_scraper.service import SECTION_NAMES
    soup = BeautifulSoup(HTML, 'html.parser')
    soup.button.unwrap()
    base_table = str(soup.table)
    for key in list(SECTION_NAMES)[1:]:
        content = base_table
        if key == 'shareholding':
            content = (f'<div id="quarterly-shp">{base_table}</div>'
                       f'<div id="yearly-shp">{base_table.replace("Jun 2026", "Mar 2026")}</div>')
        soup.append(BeautifulSoup(f'<section id="{key}">{content}</section>', 'html.parser'))
    class AllSections(FakeRetriever):
        def get(self, url, **kwargs):
            return str(soup), url, 'http'
    result = scrape(ScreenerRequest(nse_symbol='MRF', reporting_basis='consolidated'), AllSections(), SECTION_NAMES)
    assert result.status == 'complete'
    assert len(result.sections) == 6
    tables = result.sections['shareholding_pattern'].tables
    assert [t.name for t in tables] == ['quarterly-shp', 'yearly-shp']
    assert tables[0].periods[0].month == 6
    assert tables[1].periods[0].month == 3
