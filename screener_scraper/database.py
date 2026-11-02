"""Transactional replacement of the latest complete Screener dataset."""

import os
from pathlib import Path
from decimal import Decimal
import psycopg
from psycopg.types.json import Jsonb
from .service import SECTION_NAMES

ROOT = Path(__file__).resolve().parent.parent


def connection_options():
    url = os.environ.get('SCREENER_DATABASE_URL')
    if url:
        return {'conninfo': url, 'connect_timeout': 5}
    password_file = ROOT / '.secrets' / 'postgres_password'
    if password_file.exists():
        return dict(host='127.0.0.1', port=55432, dbname='screener', user='screener',
                    password=password_file.read_text(encoding='utf-8').strip(), connect_timeout=5)
    return None


def initialize(options):
    with psycopg.connect(**options) as conn:
        conn.execute((ROOT / 'database' / 'schema.sql').read_text(encoding='utf-8'))


def save_latest(result, options=None):
    if result.status != 'complete':
        return 'skipped_partial'
    if set(result.sections) != set(SECTION_NAMES.values()) or any(
        s.status != 'ok' or not s.tables for s in result.sections.values()
    ):
        raise ValueError('A complete result must contain all six successful sections.')
    for section in result.sections.values():
        for table in section.tables:
            if not table.periods or not table.rows or any(len(r.values) != len(table.periods) for r in table.rows):
                raise ValueError('Cannot store a malformed table.')
    options = options if options is not None else connection_options()
    if options is None:
        return 'not_configured'
    company = result.company
    # BSE remains stable when the request uses an NSE alias or a BSE fallback.
    key = 'BSE:' + company.bse_code if company.bse_code else 'NSE:' + (company.nse_symbol or company.identifier)
    basis = result.requested_reporting_basis.value
    with psycopg.connect(**options) as conn:
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute('INSERT INTO screener_companies(company_key) VALUES (%s) ON CONFLICT DO NOTHING', (key,))
        company_id = conn.execute('SELECT id FROM screener_companies WHERE company_key=%s FOR UPDATE', (key,)).fetchone()[0]
        previous = conn.execute('SELECT scraped_at FROM screener_datasets WHERE company_id=%s AND reporting_basis=%s',
                                (company_id, basis)).fetchone()
        if previous and previous[0] >= result.scraped_at:
            return 'skipped_older'
        conn.execute('UPDATE screener_companies SET name=%s,nse_symbol=%s,bse_code=%s WHERE id=%s',
                     (company.name, company.nse_symbol, company.bse_code, company_id))
        conn.execute('''INSERT INTO screener_datasets VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(company_id,reporting_basis) DO UPDATE SET
            scraped_at=EXCLUDED.scraped_at,source_url=EXCLUDED.source_url,
            retrieval_method=EXCLUDED.retrieval_method,schema_version=EXCLUDED.schema_version,warnings=EXCLUDED.warnings''',
            (company_id,basis,result.scraped_at,result.source_url,result.retrieval_method,result.schema_version,
             Jsonb([w.model_dump(mode='json') for w in result.warnings])))
        conn.execute('DELETE FROM screener_tables WHERE company_id=%s AND reporting_basis=%s', (company_id,basis))
        for section_name, section in result.sections.items():
            for ti, table in enumerate(section.tables):
                table_id = conn.execute('''INSERT INTO screener_tables
                    (company_id,reporting_basis,section,table_index,table_name,stated_context)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (company_id,basis,section_name,ti,table.name,table.stated_context)).fetchone()[0]
                for ri, row in enumerate(table.rows):
                    row_id = conn.execute('''INSERT INTO screener_rows(table_id,row_index,metric,parent_metric,source_url)
                        VALUES (%s,%s,%s,%s,%s) RETURNING id''',
                        (table_id,ri,row.label,row.parent,row.source_url)).fetchone()[0]
                    with conn.cursor() as cur:
                        cur.executemany('''INSERT INTO screener_values VALUES
                            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', [
                            (row_id,ci,p.label,p.year,p.month,p.duration_months,c.raw_text,
                             Decimal(str(c.numeric_value)) if c.numeric_value is not None else None,
                             c.unit,c.status,Jsonb(c.links))
                            for ci,(p,c) in enumerate(zip(table.periods,row.values))])
    return 'saved'


if __name__ == '__main__':
    options = connection_options()
    if options is None:
        raise SystemExit('Run setup-postgres.ps1 or set SCREENER_DATABASE_URL first.')
    initialize(options)
    print('Screener database schema initialized.')
