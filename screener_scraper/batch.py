"""Batch company execution, separate from the HTTP interface."""

import logging

import psycopg

from .database import save_latest
from .models import ScreenerRequest
from .retrieval import Retriever, SourceError
from .service import SECTION_NAMES, scrape

log = logging.getLogger(__name__)


def execute_item(symbol, basis, *, retriever_factory=Retriever, save=save_latest):
    """One company/basis; a partial scrape never replaces a complete snapshot."""
    item = {'nse_symbol': symbol, 'reporting_basis': basis, 'status': 'failed',
            'persistence': None, 'sections': {}, 'error': None}
    try:
        result = scrape(ScreenerRequest(nse_symbol=symbol, reporting_basis=basis),
                        retriever_factory(), SECTION_NAMES)
        item.update(status=result.status, scraped_at=result.scraped_at.isoformat(),
                    source_url=result.source_url,
                    sections={name: {'status': section.status,
                                     'tables': len(section.tables),
                                     'rows': sum(len(table.rows) for table in section.tables),
                                     'issues': [issue.model_dump() for issue in section.issues]}
                              for name, section in result.sections.items()})
        try:
            item['persistence'] = save(result)
            if item['persistence'] == 'not_configured':
                item.update(status='failed', error={'code': 'database_not_configured',
                            'message': 'PostgreSQL storage is required for seeding.'})
        except psycopg.Error:
            log.error('Batch database write failed; transaction rolled back.')
            item.update(status='failed', persistence='failed',
                        error={'code': 'database_write_failed',
                               'message': 'PostgreSQL write failed; previous snapshot was preserved.'})
    except SourceError as exc:
        item['status'] = 'failed'
        item['error'] = {'code': 'source_error', 'http_status': exc.status, 'message': str(exc)}
    except Exception:
        log.exception('Unexpected Screener batch item failure')
        item['status'] = 'failed'
        item['error'] = {'code': 'unexpected_failure', 'message': 'Unexpected extraction failure.'}
    return item
