"""Durable, single-process batch coordinator with bounded company workers."""

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from functools import partial
from itertools import groupby
from operator import itemgetter
from threading import Event, Lock
from uuid import uuid4
import logging
import hashlib
from copy import deepcopy

import psycopg
from psycopg.types.json import Jsonb

from .batch import execute_item
from .constituents import fetch_constituents, parse_constituents
from .database import connection_options, initialize
from .database import save_latest
from .retrieval import Retriever, SourceError
from .pacing import source_gate
from .shared_details import SharedInvestorDetails

log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


class JobConflict(Exception):
    pass


class JobManager:
    def __init__(self, options=None, *, worker=execute_item):
        self.options = options
        self.worker = worker
        self.lock = Lock()
        self.stop = Event()
        self.lease = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='screener-batch')

    def _ready(self):
        if self.stop.is_set():
            raise JobConflict('Batch manager is shutting down.')
        if self.lease is not None:
            if self.lease.closed:
                raise JobConflict('Batch coordinator database connection was lost. Restart the API.')
            # Do not reacquire a lost lease while workers from the old lease may still run.
            self.lease.execute('SELECT 1')
            return
        self.options = self.options or connection_options()
        if not self.options:
            raise JobConflict('Configure PostgreSQL before starting a seed job.')
        lease = psycopg.connect(**self.options, autocommit=True)
        try:
            if not lease.execute('SELECT pg_try_advisory_lock(7311265042001)').fetchone()[0]:
                raise JobConflict('Another batch coordinator owns this database. Run one API worker.')
            initialize(self.options)
            self.lease = lease
            with psycopg.connect(**self.options) as conn:
                rows = conn.execute("SELECT document FROM screener_batch_jobs WHERE state IN ('queued','running') FOR UPDATE").fetchall()
                for (job,) in rows:
                    self._interrupt(job, 'API stopped before the job finished. Explicit retry is required.')
                    self._write(job, conn)
        except Exception:
            self.lease = None
            lease.close()
            raise

    def _write(self, job, conn=None):
        job['updated_at'] = now()
        counts = {state: 0 for state in ('queued','running','complete','partial','failed','interrupted')}
        for item in job['items']:
            counts[item['status']] += 1
        job['counts'] = counts
        if conn is None:
            with psycopg.connect(**self.options) as connection:
                self._write(job, connection)
            return
        conn.execute('''INSERT INTO screener_batch_jobs(id,state,document) VALUES (%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET state=EXCLUDED.state, document=EXCLUDED.document, updated_at=now()''',
            (job['id'], job['state'], Jsonb(job)))

    @staticmethod
    def _interrupt(job, message):
        job.update(state='interrupted', message=message, finished_at=now())
        for item in job['items']:
            if item['status'] in ('queued','running'):
                item['status'] = 'interrupted'

    def get(self, job_id):
        with self.lock:
            self._ready()
            with psycopg.connect(**self.options) as conn:
                row = conn.execute('SELECT document FROM screener_batch_jobs WHERE id=%s', (job_id,)).fetchone()
            return row[0] if row else None

    def create(self, engine, concurrency, constituent_csv=None):
        with self.lock:
            self._ready()
            with psycopg.connect(**self.options) as conn:
                if conn.execute("SELECT 1 FROM screener_batch_jobs WHERE state IN ('queued','running')").fetchone():
                    raise JobConflict('A Nifty 50 batch is already active.')
            job = {'id': str(uuid4()), 'state': 'queued', 'created_at': now(),
                   'engine': engine, 'concurrency': concurrency, 'snapshot': None,
                   'total': 100, 'items': [], 'message': None, 'attempt': 1}
            # Keep fallback CSV out of the status document; save its validated snapshot instead.
            self._write(job)
            try:
                self.executor.submit(self._run, job, constituent_csv)
            except RuntimeError:
                self._interrupt(job, 'Coordinator is unavailable. Retry explicitly.')
                self._write(job)
                raise JobConflict(job['message'])
            return deepcopy(job)

    def retry(self, job_id):
        with self.lock:
            self._ready()
            with psycopg.connect(**self.options) as conn:
                row = conn.execute('SELECT document FROM screener_batch_jobs WHERE id=%s', (job_id,)).fetchone()
                if not row:
                    return None
                job = row[0]
                if job['state'] in ('queued','running','complete'):
                    raise JobConflict('Only partial, failed or interrupted jobs can be retried.')
                if conn.execute("SELECT 1 FROM screener_batch_jobs WHERE state IN ('queued','running')").fetchone():
                    raise JobConflict('Another Nifty 50 batch is active.')
            if not job['snapshot']:
                raise JobConflict('No constituent snapshot was saved. Start a new job, supplying a CSV if necessary.')
            for item in job['items']:
                if item['status'] != 'complete':
                    symbol, basis = item['nse_symbol'], item['reporting_basis']
                    item.clear()
                    item.update(nse_symbol=symbol, reporting_basis=basis, status='queued')
            job.update(state='queued', message=None, attempt=job['attempt']+1)
            job.pop('finished_at', None)
            self._write(job)
            self.executor.submit(self._run, job, None)
            return deepcopy(job)

    @staticmethod
    def _cooling_down():
        with source_gate.lock:
            return source_gate.clock() < source_gate.blocked_until

    def _retriever(self, engine, shared):
        manager = self
        class StoppableRetriever(Retriever):
            def get(self, url, **kwargs):
                if manager.stop.is_set():
                    raise SourceError(503, 'Batch shutdown requested.')
                return super().get(url, **kwargs)
        return StoppableRetriever(browser_backend='lightpanda' if engine == 'lightpanda' else 'chrome',
                                  browser_only=engine == 'lightpanda', http_fallback=False,
                                  shared_investors=shared)

    @staticmethod
    def _pending_items(items):
        for _, pair in groupby(items, key=itemgetter('nse_symbol')):
            queued = [item for item in pair if item['status'] == 'queued']
            shared = SharedInvestorDetails() if len(queued) == 2 else None
            for item in queued:
                yield item, shared

    def _run(self, job, constituent_csv):
        try:
            with self.lock:
                job.update(state='running', started_at=now())
                self._write(job)
            if job['snapshot'] is None:
                try:
                    snapshot = fetch_constituents()
                except ValueError:
                    if not constituent_csv:
                        raise
                    snapshot = {'source_url': 'caller_supplied_csv', 'fetched_at': now(),
                                'sha256': hashlib.sha256(constituent_csv.encode()).hexdigest(),
                                'warning': 'Caller supplied this CSV; authenticity and effective date are not verified.',
                                'companies': parse_constituents(constituent_csv.lstrip('\ufeff'))}
                with self.lock:
                    job['snapshot'] = snapshot
                    job['items'] = [{'nse_symbol': company['nse_symbol'], 'reporting_basis': basis, 'status': 'queued'}
                                    for company in snapshot['companies']
                                    for basis in ('standalone','consolidated')]
                    self._write(job)
            with ThreadPoolExecutor(max_workers=job['concurrency'], thread_name_prefix='screener-company') as pool:
                pending = self._pending_items(job['items'])
                futures = {}
                def dispatch_available():
                    while (not self.stop.is_set() and not self._cooling_down()
                           and len(futures) < job['concurrency']):
                        entry = next(pending, None)
                        if entry is None:
                            break
                        item, shared = entry
                        item.update(status='running', started_at=now())
                        futures[pool.submit(self.worker, item['nse_symbol'], item['reporting_basis'],
                                            retriever_factory=partial(self._retriever, job['engine'], shared),
                                            save=partial(save_latest, options=self.options))] = item

                with self.lock:
                    self._ready()  # Fail closed if the coordinator lease is lost.
                    dispatch_available()
                    self._write(job)
                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    with self.lock:
                        for future in done:
                            item = futures.pop(future)
                            item.update(future.result(), finished_at=now())
                        if not self.stop.is_set():
                            self._ready()  # Recheck the lease before dispatching more work.
                            dispatch_available()
                        # Persist finished items and replacements in one job snapshot.
                        self._write(job)
                with self.lock:
                    if self.stop.is_set() or self._cooling_down():
                        reason = 'Source rate limit cooldown' if self._cooling_down() else 'API shutdown'
                        self._interrupt(job, reason + ' interrupted the batch. Retry explicitly after recovery.')
                    else:
                        statuses = {item['status'] for item in job['items']}
                        job.update(state='complete' if statuses == {'complete'} else
                                   'failed' if statuses == {'failed'} else 'partial', finished_at=now())
                    self._write(job)
        except Exception as exc:
            log.error('Batch coordinator stopped (%s).', type(exc).__name__)
            with self.lock:
                self._interrupt(job, 'Batch interrupted by a source-list, database or worker failure. Retry requires a saved constituent snapshot.')
                if job['snapshot'] is None and isinstance(exc, ValueError):
                    job['state'] = 'failed'
                    job['message'] = 'Official constituent list unavailable or invalid; supply a valid official CSV on a new job.'
                try:
                    self._write(job)
                except psycopg.Error:
                    log.error('Unable to persist interruption; startup recovery will mark the job interrupted.')

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True)
        with self.lock:
            if self.lease is not None:
                self.lease.close()
                self.lease = None
