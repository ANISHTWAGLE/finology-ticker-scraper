"""Real PostgreSQL tests use a temporary schema, enabled explicitly by environment."""
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
import psycopg
from psycopg import sql
from screener_scraper.database import connection_options, initialize, save_latest
from screener_scraper.models import ScreenerResponse
from screener_scraper.service import SECTION_NAMES


@pytest.fixture
def complete_result():
    return ScreenerResponse.model_validate({
        'status': 'complete', 'company': {'identifier':'MRF','name':'MRF Ltd','nse_symbol':'MRF','bse_code':'500290'},
        'requested_reporting_basis':'consolidated','source_url':'https://www.screener.in/company/MRF/consolidated/',
        'scraped_at':datetime.now(timezone.utc),'retrieval_method':'http',
        'sections':{name:{'status':'ok','tables':[{'name':name,'periods':[{'label':'Jun 2026','year':2026,'month':6}],
            'rows':[{'label':'Sales +','source_url':'https://www.screener.in/',
                     'values':[{'raw_text':'8,416','numeric_value':8416,'unit':'INR crore','status':'present'}]}]}]}
                    for name in SECTION_NAMES.values()}})


@pytest.fixture
def database():
    if os.environ.get('SCREENER_TEST_POSTGRES') != '1':
        pytest.skip('Set SCREENER_TEST_POSTGRES=1 to run real PostgreSQL tests in isolated schemas.')
    options = connection_options()
    assert options is not None
    schema = 'test_screener_' + uuid4().hex
    with psycopg.connect(**options) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    isolated = {**options, 'options':f'-c search_path={schema}'}
    try:
        initialize(isolated)
        yield isolated
    finally:
        with psycopg.connect(**options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_partial_never_opens_database(complete_result):
    complete_result.status = 'partial'
    assert save_latest(complete_result, {'conninfo':'invalid'}) == 'skipped_partial'


def test_replace_latest_and_preserve_other_basis(database, complete_result):
    assert save_latest(complete_result, database) == 'saved'
    standalone = complete_result.model_copy(deep=True)
    standalone.requested_reporting_basis = type(standalone.requested_reporting_basis)('standalone')
    assert save_latest(standalone, database) == 'saved'
    newer = complete_result.model_copy(deep=True)
    newer.scraped_at += timedelta(seconds=1)
    newer.company.identifier = '500290'
    newer.sections['quarterly_results'].tables[0].rows[0].values[0].numeric_value = 9999
    assert save_latest(newer, database) == 'saved'
    assert save_latest(complete_result, database) == 'skipped_older'
    with psycopg.connect(**database) as conn:
        assert conn.execute('SELECT count(*) FROM screener_companies').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM screener_values').fetchone()[0] == 12
        values = conn.execute("SELECT reporting_basis,numeric_value FROM screener_financials WHERE section='quarterly_results' ORDER BY reporting_basis").fetchall()
        assert values == [('consolidated',9999),('standalone',8416)]


def test_failed_replacement_rolls_back(database, complete_result):
    save_latest(complete_result, database)
    newer = complete_result.model_copy(deep=True)
    newer.scraped_at += timedelta(seconds=1)
    newer.sections['quarterly_results'].tables[0].periods[0].month = 99
    with pytest.raises(psycopg.errors.CheckViolation):
        save_latest(newer, database)
    with psycopg.connect(**database) as conn:
        assert conn.execute('SELECT count(*) FROM screener_values').fetchone()[0] == 6
        assert conn.execute('SELECT scraped_at FROM screener_datasets').fetchone()[0] == complete_result.scraped_at


def test_partial_does_not_replace_existing(database, complete_result):
    save_latest(complete_result, database)
    complete_result.status = 'partial'
    complete_result.sections = {}
    assert save_latest(complete_result, database) == 'skipped_partial'
    with psycopg.connect(**database) as conn:
        assert conn.execute('SELECT count(*) FROM screener_values').fetchone()[0] == 6


def test_endpoint_reports_storage_failure_without_losing_response(monkeypatch, complete_result):
    from fastapi.testclient import TestClient
    from finology_scraper.api import app
    from screener_scraper import api
    monkeypatch.setattr(api, 'scrape', lambda *args: complete_result)
    def fail(*args):
        raise psycopg.OperationalError('simulated unavailable DB')
    monkeypatch.setattr(api, 'save_latest', fail)
    with TestClient(app) as client:
        response = client.post('/screener/scrape',json={'nse_symbol':'MRF','reporting_basis':'consolidated'})
    assert response.status_code == 503
    assert response.json()['persistence'] == 'failed'
    assert len(response.json()['sections']) == 6
