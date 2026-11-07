"""Batch safety tests; simulated retrieval is not live scraping."""

from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from screener_scraper.constituents import parse_constituents
from screener_scraper.pacing import RequestGate
from screener_scraper.retrieval import Retriever, SourceError
from screener_scraper.shared_details import SharedInvestorDetails


def constituent_csv(count=50):
    return 'Company Name,Symbol,ISIN Code,Series\n' + ''.join(
        f'Company {i},STOCK{i},INE{i:09d},EQ\n' for i in range(count))


def test_paired_workers_share_only_identical_investor_requests():
    from threading import Barrier, Event
    details = SharedInvestorDetails()
    url = 'https://www.screener.in/api/3/3365/investors/promoters/yearly/'
    calls = []
    ready = Barrier(3)
    release = Event()
    def fetch(target):
        calls.append(target)
        assert release.wait(3)
        return 'payload', target, 'http'
    def run():
        ready.wait(3)
        return details.get(fetch, url)
    with ThreadPoolExecutor(2) as pool:
        workers = [pool.submit(run) for _ in range(2)]
        ready.wait(3)
        release.set()
        assert workers[0].result() == workers[1].result()
    assert calls == [url]
    quarterly = url.replace('yearly', 'quarterly')
    schedule = 'https://www.screener.in/api/company/3365/schedules/?parent=Sales'
    details.get(fetch, quarterly)
    details.get(fetch, schedule)
    details.get(fetch, schedule)
    assert calls == [url, quarterly, schedule, schedule]


def test_failed_investor_request_can_be_fetched_again():
    shared = SharedInvestorDetails()
    url = 'https://www.screener.in/api/3/3365/investors/promoters/yearly/'
    def fail(target):
        raise SourceError(429, 'rate limited')
    with pytest.raises(SourceError):
        shared.get(fail, url)
    assert shared.get(lambda target: 'fresh', url) == 'fresh'


def test_shared_details_scoped_to_company_pair_and_attempt():
    from screener_scraper.jobs import JobManager
    items = [dict(nse_symbol=s, status='queued') for s in ['TCS','TCS','INFY','INFY']]
    first = [shared for _, shared in JobManager._pending_items(items)]
    assert first[0] is first[1] and first[2] is first[3] and first[0] is not first[2]
    items[0]['status'] = 'complete'
    retry = [shared for _, shared in JobManager._pending_items(items)]
    assert retry[0] is None and retry[1] is not first[2]


def test_constituents_require_exactly_50_unique_companies():
    assert len(parse_constituents(constituent_csv())) == 50
    with pytest.raises(ValueError, match='exactly 50'):
        parse_constituents(constituent_csv(49))
    with pytest.raises(ValueError, match='Duplicate'):
        parse_constituents(constituent_csv().replace('STOCK1,', 'STOCK0,'))
    with pytest.raises(ValueError, match='columns'):
        parse_constituents('<html>Access denied</html>')


def test_concurrent_workers_share_request_spacing():
    gate = RequestGate(interval=0.03)
    times = []
    def request():
        gate.acquire()
        times.append(time.monotonic())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: request(), range(4)))
    times.sort()
    assert all(b-a >= 0.025 for a,b in zip(times,times[1:]))


def test_cooldown_applies_to_all_retrievers():
    gate = RequestGate()
    first, second = Retriever(gate=gate), Retriever(gate=gate)
    first.gate.cooldown(90)
    with pytest.raises(SourceError) as exc:
        second.get('https://www.screener.in/company/TCS/')
    assert exc.value.status == 429


def test_waiting_worker_rechecks_cooldown():
    now = [1.0]
    gate = RequestGate(clock=lambda: now[0])
    gate.acquire()
    def sleep(delay):
        now[0] += delay
        gate.cooldown()
    gate.sleep = sleep
    with pytest.raises(SourceError) as exc:
        gate.acquire()
    assert exc.value.status == 429


def test_batch_partial_preserves_previous_snapshot(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from screener_scraper import batch
    result = SimpleNamespace(status='partial', sections={},
                             scraped_at=datetime.now(timezone.utc),
                             source_url='https://www.screener.in/company/TCS/')
    monkeypatch.setattr(batch, 'scrape', lambda *args: result)
    item = batch.execute_item('TCS', 'standalone')
    assert item['status'] == 'partial'
    assert item['persistence'] == 'skipped_partial'


def test_batch_storage_failure_is_separate_from_section_success(monkeypatch):
    import psycopg
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from screener_scraper import batch
    result = SimpleNamespace(status='complete', sections={},
                             scraped_at=datetime.now(timezone.utc),
                             source_url='https://www.screener.in/company/TCS/')
    monkeypatch.setattr(batch, 'scrape', lambda *args: result)
    def fail(result):
        raise psycopg.OperationalError('private connection details')
    item = batch.execute_item('TCS', 'standalone', save=fail)
    assert item['status'] == 'failed'
    assert item['persistence'] == 'failed'
    assert item['error']['code'] == 'database_write_failed'
    assert 'private' not in str(item)


def test_batch_source_failure_retains_rate_limit_status(monkeypatch):
    from screener_scraper import batch
    def fail(*args):
        raise SourceError(429, 'Source rate limit')
    monkeypatch.setattr(batch, 'scrape', fail)
    item = batch.execute_item('TCS', 'standalone')
    assert item['status'] == 'failed'
    assert item['error']['http_status'] == 429


def test_unexpected_save_failure_cannot_report_complete(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from screener_scraper import batch
    result = SimpleNamespace(status='complete', sections={},
                             scraped_at=datetime.now(timezone.utc),
                             source_url='https://www.screener.in/company/TCS/')
    monkeypatch.setattr(batch, 'scrape', lambda *args: result)
    def invalid(result):
        raise ValueError('Malformed complete dataset')
    item = batch.execute_item('TCS','standalone',save=invalid)
    assert item['status'] == 'failed'
    assert item['error']['code'] == 'unexpected_failure'
