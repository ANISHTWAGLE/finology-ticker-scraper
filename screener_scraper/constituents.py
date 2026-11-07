"""Fetch and validate the official Nifty 50 constituent snapshot."""

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen

URLS = (
    'https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv',
    'https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv',
)


def parse_constituents(content):
    reader = csv.DictReader(io.StringIO(content))
    required = {'Company Name', 'Symbol', 'ISIN Code', 'Series'}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError('Official constituent CSV columns are missing.')
    rows = list(reader)
    if len(rows) != 50:
        raise ValueError('Expected exactly 50 Nifty 50 constituents.')
    symbols, isins = set(), set()
    companies = []
    for row in rows:
        symbol = (row.get('Symbol') or '').strip()
        isin = (row.get('ISIN Code') or '').strip()
        name = (row.get('Company Name') or '').strip()
        if (not re.fullmatch(r'[A-Z0-9&_.-]{1,40}', symbol)
                or not re.fullmatch(r'IN[A-Z0-9]{10}', isin)
                or not name or (row.get('Series') or '').strip() != 'EQ'):
            raise ValueError('Invalid company identifier in constituent CSV.')
        if symbol in symbols or isin in isins:
            raise ValueError('Duplicate company in constituent CSV.')
        symbols.add(symbol)
        isins.add(isin)
        companies.append({'nse_symbol': symbol, 'name': name, 'isin': isin})
    return companies


def fetch_constituents():
    errors = []
    for url in URLS:
        try:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/csv,*/*'})
            with urlopen(request, timeout=20) as response:
                if response.url not in URLS:
                    raise ValueError('Unexpected constituent source redirect.')
                data = response.read(200001)
            if len(data) > 200000:
                raise ValueError('Constituent CSV exceeds the size limit.')
            companies = parse_constituents(data.decode('utf-8-sig'))
            return {'source_url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                    'sha256': hashlib.sha256(data).hexdigest(), 'companies': companies}
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            errors.append(f'{url}: {type(exc).__name__}')
    raise ValueError('Official Nifty 50 list unavailable or invalid. ' + '; '.join(errors))
