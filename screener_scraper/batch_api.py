"""HTTP endpoints for durable Nifty 50 seed jobs."""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from .jobs import JobConflict
from .models import BatchJob, BatchRequest

router = APIRouter(prefix='/screener/jobs', tags=['Screener batches'])


def call_manager(function, *args):
    try:
        return function(*args)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(503, 'Batch PostgreSQL storage is unavailable.') from exc


@router.post('/nifty50', status_code=202, response_model=BatchJob, response_model_exclude_unset=True,
             summary='Seed all Nifty 50 sections for both reporting bases')
def create_job(body: BatchRequest, request: Request):
    return call_manager(request.app.state.screener_jobs.create,
                        body.engine, body.concurrency, body.constituent_csv)


@router.get('/{job_id}', response_model=BatchJob, response_model_exclude_unset=True,
            summary='Get saved progress and per-company extraction/storage status')
def get_job(job_id: UUID, request: Request):
    job = call_manager(request.app.state.screener_jobs.get, str(job_id))
    if job is None:
        raise HTTPException(404, 'Batch job not found.')
    return job


@router.post('/{job_id}/retry', status_code=202, response_model=BatchJob, response_model_exclude_unset=True,
             summary='Explicitly retry unfinished items using the saved company list')
def retry_job(job_id: UUID, request: Request):
    job = call_manager(request.app.state.screener_jobs.retry, str(job_id))
    if job is None:
        raise HTTPException(404, 'Batch job not found.')
    return job
