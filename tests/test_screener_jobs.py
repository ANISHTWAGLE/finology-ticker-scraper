"""Real PostgreSQL progress/API tests with simulated company workers, not live scrapes."""

import time
from collections import Counter
from threading import Event, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_screener_database import database, complete_result  # Shared isolated PostgreSQL fixtures.
from test_screener_batch import constituent_csv
from screener_scraper.constituents import parse_constituents
from screener_scraper.jobs import JobManager, JobConflict, now


@pytest.fixture
def snapshot(monkeypatch):
    snapshot = {'source_url':'https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv',
                'fetched_at':now(), 'sha256':'test', 'companies':parse_constituents(constituent_csv())}
    monkeypatch.setattr('screener_scraper.jobs.fetch_constituents', lambda: snapshot)
    return snapshot


def wait_finished(manager, job_id, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job['state'] not in ('queued','running'):
            return job
        time.sleep(0.01)
    pytest.fail('Test job did not finish')


def success(symbol, basis, **kwargs):
    return dict(nse_symbol=symbol, reporting_basis=basis, status='complete', persistence='saved')


def test_api_schedules_100_items_limits_concurrency_and_rejects_overlap(database, snapshot):
    from finology_scraper.api import app
    release, started = Event(), Event()
    lock = Lock()
    counts = {'active':0, 'peak':0}
    def worker(symbol, basis, **kwargs):
        with lock:
            counts['active'] += 1
            counts['peak'] = max(counts['peak'], counts['active'])
            if counts['active'] == 2:
                started.set()
        assert release.wait(5)
        with lock:
            counts['active'] -= 1
        return success(symbol,basis)
    with TestClient(app) as client:
        app.state.screener_jobs.close()
        manager = app.state.screener_jobs = JobManager(database,worker=worker)
        try:
            assert client.post('/screener/jobs/nifty50',json={}).status_code == 422
            assert client.post('/screener/jobs/nifty50',json={'engine':'http','concurrency':5}).status_code == 422
            response = client.post('/screener/jobs/nifty50',json={'engine':'http','concurrency':2})
            assert response.status_code == 202
            job_id = response.json()['id']
            assert started.wait(5)
            assert client.post('/screener/jobs/nifty50',json={'engine':'http'}).status_code == 409
            assert client.post(f'/screener/jobs/{job_id}/retry').status_code == 409
            progress = client.get(f'/screener/jobs/{job_id}').json()
            assert progress['counts']['running'] == 2
            assert progress['total'] == len(progress['items']) == 100
            assert client.get(f'/screener/jobs/{uuid4()}').status_code == 404
        finally:
            release.set()
        finished = wait_finished(manager,job_id)
        assert finished['state'] == 'complete'
        assert finished['counts']['complete'] == 100
        assert Counter(i['reporting_basis'] for i in finished['items']) == {'standalone':50,'consolidated':50}
        assert counts['peak'] == 2


def test_retry_preserves_complete_items_and_original_snapshot(database, snapshot):
    calls = Counter()
    def worker(symbol,basis,**kwargs):
        calls[symbol,basis] += 1
        result = success(symbol,basis)
        if symbol == 'STOCK0' and basis == 'standalone' and calls[symbol,basis] == 1:
            result.update(status='partial',persistence='skipped_partial')
        return result
    manager = JobManager(database,worker=worker)
    try:
        job_id = manager.create('http',2)['id']
        assert wait_finished(manager,job_id)['state'] == 'partial'
        manager.retry(job_id)
        result = wait_finished(manager,job_id)
        assert result['state'] == 'complete' and result['attempt'] == 2
        assert sum(calls.values()) == 101
        assert result['snapshot'] == snapshot
    finally:
        manager.close()


def test_scheduler_persists_each_completion_with_its_next_dispatch(database, snapshot):
    class CountingManager(JobManager):
        def __init__(self, *args, **kwargs):
            self.persisted_snapshots = 0
            super().__init__(*args, **kwargs)

        def _write(self, job, conn=None):
            if conn is None:
                self.persisted_snapshots += 1
            return super()._write(job, conn)

    manager = CountingManager(database, worker=success)
    try:
        result = wait_finished(manager, manager.create('http', 1)['id'])
        assert result['state'] == 'complete'
        assert result['counts']['complete'] == 100
        # create + running + snapshot + first dispatch + 100 combined
        # completion/dispatch snapshots + final state
        assert manager.persisted_snapshots == 105
    finally:
        manager.close()


def test_restart_marks_unfinished_items_interrupted_and_keeps_completed(database, snapshot):
    manager = JobManager(database)
    job_id = str(uuid4())
    try:
        with manager.lock:
            manager._ready()
            job = dict(id=job_id,state='running',snapshot=snapshot,total=100,engine='http',
                       concurrency=2,attempt=1,created_at=now(),items=[
                           dict(nse_symbol='STOCK0',reporting_basis='standalone',status='complete'),
                           dict(nse_symbol='STOCK0',reporting_basis='consolidated',status='running'),
                           dict(nse_symbol='STOCK1',reporting_basis='standalone',status='queued')])
            manager._write(job)
    finally:
        manager.close()
    recovered = JobManager(database)
    try:
        result = recovered.get(job_id)
        assert result['state'] == 'interrupted'
        assert [i['status'] for i in result['items']] == ['complete','interrupted','interrupted']
        other = JobManager(database)
        try:
            with pytest.raises(JobConflict,match='Another batch coordinator'):
                other.get(job_id)
        finally:
            other.close()
    finally:
        recovered.close()


def test_official_list_failure_uses_validated_csv_fallback(database,monkeypatch):
    def fail():
        raise ValueError('source unavailable')
    monkeypatch.setattr('screener_scraper.jobs.fetch_constituents',fail)
    manager = JobManager(database,worker=success)
    try:
        result = wait_finished(manager,manager.create('http',2,constituent_csv())['id'])
        assert result['state'] == 'complete'
        assert result['snapshot']['source_url'] == 'caller_supplied_csv'
        assert 'not verified' in result['snapshot']['warning']
        failed = wait_finished(manager,manager.create('http',2,'<html>Denied</html>')['id'])
        assert failed['state'] == 'failed' and not failed['items']
    finally:
        manager.close()


def test_429_stops_dispatch_instead_of_failing_all_remaining_companies(database,snapshot,monkeypatch):
    from screener_scraper.pacing import source_gate
    calls = []
    def limited(symbol,basis,**kwargs):
        calls.append(symbol)
        source_gate.cooldown()
        return dict(nse_symbol=symbol,reporting_basis=basis,status='failed',error={'http_status':429})
    manager = JobManager(database,worker=limited)
    try:
        result = wait_finished(manager,manager.create('http',1)['id'])
        assert result['state'] == 'interrupted'
        assert result['counts']['interrupted'] == 99
        assert len(calls) == 1
    finally:
        manager.close()
        with source_gate.lock:
            source_gate.blocked_until = 0


def test_real_worker_saves_50_companies_and_both_bases_transactionally(database,snapshot,complete_result,monkeypatch):
    import psycopg
    def simulated_scrape(body,*args):
        result = complete_result.model_copy(deep=True)
        result.company.identifier = result.company.nse_symbol = body.nse_symbol
        result.company.bse_code = None
        result.requested_reporting_basis = body.reporting_basis
        return result
    monkeypatch.setattr('screener_scraper.batch.scrape',simulated_scrape)
    manager = JobManager(database)
    try:
        result = wait_finished(manager,manager.create('http',2)['id'],timeout=60)
        assert result['state'] == 'complete'
        assert all(i['persistence'] == 'saved' for i in result['items'])
        with psycopg.connect(**database) as conn:
            assert conn.execute('SELECT count(*) FROM screener_companies').fetchone()[0] == 50
            assert conn.execute('SELECT count(*) FROM screener_datasets').fetchone()[0] == 100
            assert conn.execute('SELECT count(*) FROM screener_financials').fetchone()[0] == 600
    finally:
        manager.close()
