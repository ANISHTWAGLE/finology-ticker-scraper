"""Screener endpoint mounted on the existing FastAPI application."""

import logging
import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from threading import Lock
from .models import ScreenerRequest, ScreenerResponse
from .retrieval import Retriever, SourceError
from .service import scrape, SECTION_NAMES
from .database import save_latest
from finology_scraper.models import Issue

router = APIRouter(prefix='/screener', tags=['Screener'])
retriever = Retriever()
lock = Lock()
log = logging.getLogger(__name__)


@router.post('/scrape', response_model=ScreenerResponse,
             summary='Fetch Screener financial tables and exchange identifiers',
             description='Supply an NSE symbol and/or a six-digit BSE code. NSE is tried first. '
             'Reporting basis is required. Quarterly results, profit & loss, balance sheet, cash flow, '
             'ratios, quarterly/yearly shareholding and their public expandable details '
             'are fetched with direct HTTP first. Complete scrapes replace the saved PostgreSQL data '
             'for this company and reporting basis. Partial scrapes do not update saved data. '
             'A 403 may use fresh Chrome; 429 stops retrieval.',
             responses={207: {'model': ScreenerResponse}, 429: {'description': 'Source rate limit'},
                        503: {'description': 'Another scrape is running or PostgreSQL storage failed'}})
def scrape_screener(body: ScreenerRequest):
    if not lock.acquire(blocking=False):
        raise HTTPException(503, 'Another Screener scrape is running.', headers={'Retry-After': '5'})
    try:
        result = scrape(body, retriever, SECTION_NAMES)
        try:
            result.persistence = save_latest(result)
        except psycopg.Error:
            # Do not log connection strings or credentials from driver exceptions.
            log.error('Screener database write failed; transaction rolled back.')
            result.persistence = 'failed'
            result.warnings.append(Issue(code='database_write_failed', message='Scrape finished but PostgreSQL storage failed. Previously saved data was not replaced.'))
            return JSONResponse(status_code=503, content=result.model_dump(mode='json'))
        return JSONResponse(status_code=200 if result.status == 'complete' else 207,
                            content=result.model_dump(mode='json'))
    except SourceError as exc:
        status = exc.status if exc.status in (404, 422, 429, 504) else 502
        raise HTTPException(status, str(exc), headers={'Retry-After': '60'} if status == 429 else None) from exc
    except Exception as exc:
        log.exception('Unexpected Screener extraction failure')
        raise HTTPException(502, 'Unexpected Screener extraction failure; no complete result reported.') from exc
    finally:
        lock.release()
