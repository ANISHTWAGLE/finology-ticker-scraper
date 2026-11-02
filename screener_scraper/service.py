"""Source identity checks, table extraction and public row expansion."""

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from bs4 import BeautifulSoup
from finology_scraper.models import Company, Issue
from .models import Row, Section, ScreenerResponse
from .parsing import cell, parse_table, parse_range_table
from .retrieval import SourceError

SECTION_NAMES = {
    'quarters': 'quarterly_results', 'profit-loss': 'profit_and_loss',
    'balance-sheet': 'balance_sheet', 'cash-flow': 'cash_flow',
    'ratios': 'ratios', 'shareholding': 'shareholding_pattern',
}


def expansion_url(action, company_id, basis):
    match = re.fullmatch(r'''Company\.showSchedule\((["'])(.*?)\1,\s*(["'])(.*?)\3,\s*this\)''', action)
    if match:
        params = {'parent': match[2], 'section': match[4]}
        if basis == 'consolidated':
            params['consolidated'] = ''
        return f'https://www.screener.in/api/company/{company_id}/schedules/?' + urlencode(params)
    match = re.fullmatch(r"Company.showShareholders\('([a-z_]+)', '(quarterly|yearly)', this\)", action)
    if match:
        return f'https://www.screener.in/api/3/{company_id}/investors/{match[1]}/{match[2]}/'
    raise ValueError('Unrecognized public row expansion control.')


def expand(table, element, key, company_id, basis, retriever, section):
    queue = [(b.get('onclick', ''), b.get_text(' ', strip=True))
             for b in element.select('button[onclick]')]
    visited = set()
    while queue:
        action, parent = queue.pop(0)
        try:
            url = expansion_url(action, company_id, basis)
            if url in visited:
                continue
            if len(visited) >= 100:
                raise ValueError('Expansion limit reached; additional rows were not collected.')
            visited.add(url)
            payload, _, _ = retriever.get(url)
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError('Unexpected detail response shape.')
            for label, values in data.items():
                if not isinstance(values, dict):
                    raise ValueError('Unexpected detail row shape.')
                # Screener omits the separate duration badge from detail JSON keys.
                aliases = {p.label: re.sub(r' \d+m$', '', p.label)
                           if p.duration_months is not None else p.label for p in table.periods}
                if len(set(aliases.values())) != len(aliases):
                    raise ValueError('Ambiguous detail period mapping.')
                periods = set(aliases) | set(aliases.values())
                if set(values) - periods - {'setAttributes', 'isExpandable'}:
                    raise ValueError('Detail periods differ from the parent table.')
                raw_values = [values.get(p.label, values.get(aliases[p.label])) for p in table.periods]
                if any(v is not None and not isinstance(v, (str, int, float)) for v in raw_values):
                    raise ValueError('Unexpected detail cell shape.')
                table.rows.append(Row(label=label, parent=parent, source_url=url, values=[
                    cell('' if v is None else str(v), label, key, table.stated_context) for v in raw_values]))
                if values.get('isExpandable'):
                    queue.append((values['isExpandable'], label))
        except (SourceError, ValueError, TypeError) as exc:
            section.status = 'partial'
            section.issues.append(Issue(code='detail_unavailable', message=f'{parent}: {exc}'))
            if isinstance(exc, SourceError) and exc.status == 429:
                return


def scrape(body, retriever, section_keys):
    identifiers = [x for x in (body.nse_symbol, body.bse_code) if x]
    warnings = []
    for index, identifier in enumerate(identifiers):
        suffix = 'consolidated/' if body.reporting_basis.value == 'consolidated' else ''
        url = f'https://www.screener.in/company/{quote(identifier, safe="")}/{suffix}'
        try:
            html, source, method = retriever.get(url, browser_fallback=True)
            soup = BeautifulSoup(html, 'html.parser')
            info = soup.select_one('#company-info')
            heading = soup.select_one('h1')
            if info is None or heading is None:
                raise SourceError(502, 'The response is not a supported Screener company page.')
            nse = bse = None
            for a in soup.select('a[href]'):
                label = a.get_text(' ', strip=True)
                if re.fullmatch(r'NSE:\s*\S+', label):
                    nse = label.split(':', 1)[1].strip()
                if re.fullmatch(r'BSE:\s*\d{6}', label):
                    bse = label.split(':', 1)[1].strip()
            expected = nse if identifier == body.nse_symbol else bse
            if expected != identifier:
                raise SourceError(502, 'Returned exchange identifier does not match the requested identifier.')
            if body.bse_code and bse != body.bse_code:
                raise SourceError(422, 'Supplied NSE symbol and BSE code do not identify the same company.')
            actual = 'consolidated' if info.get('data-consolidated') == 'true' else 'standalone'
            if actual != body.reporting_basis.value:
                raise SourceError(502, 'Requested reporting basis is unavailable; no cross-basis fallback was used.')
            break
        except SourceError as exc:
            if index + 1 == len(identifiers) or exc.status not in (404, 502, 504):
                raise
            warnings.append(Issue(code='bse_fallback', message=f'NSE lookup failed: {exc} Using supplied BSE code.'))
    company_id = info.get('data-company-id', '')
    if not company_id.isdigit():
        raise SourceError(502, 'Missing source company ID.')
    result = ScreenerResponse(status='complete', company=Company(identifier=identifier,
        name=heading.get_text(' ', strip=True), nse_symbol=nse, bse_code=bse),
        requested_reporting_basis=body.reporting_basis, source_url=source,
        scraped_at=datetime.now(timezone.utc), retrieval_method=method, warnings=warnings)
    for key in section_keys:
        section = Section(status='ok')
        result.sections[SECTION_NAMES[key]] = section
        containers = soup.select('#' + key)
        if len(containers) != 1:
            section.status = 'absent' if not containers else 'failed'
            section.issues.append(Issue(code='section_unavailable', message='Expected one source section.'))
            continue
        container = containers[0]
        context_el = container.select_one('p.sub')
        context = context_el.get_text(' ', strip=True) if context_el else None
        tables = container.select('table.data-table')
        if not tables:
            section.status = 'absent'
            section.issues.append(Issue(code='table_absent', message='No public data table found.'))
        for index, element in enumerate(tables):
            try:
                name = SECTION_NAMES[key]
                if key == 'shareholding':
                    tab = element.find_parent(id=re.compile(r'^(quarterly|yearly)-shp$'))
                    if tab is None:
                        raise ValueError('Unknown shareholding frequency.')
                    name = tab['id']
                table = parse_table(element, name, key, context, source)
                section.tables.append(table)
                expand(table, element, key, company_id, body.reporting_basis.value, retriever, section)
                if any(c.status == 'unparsed' and not c.links for r in table.rows for c in r.values):
                    section.status = 'partial'
                    section.issues.append(Issue(code='unparsed_value', message='Some source values are not numeric; raw text is retained.'))
            except ValueError as exc:
                section.status = 'partial' if section.tables else 'failed'
                section.issues.append(Issue(code='unsupported_table', message=str(exc)))
        for element in container.select('table.ranges-table'):
            try:
                section.tables.append(parse_range_table(element, source))
            except ValueError as exc:
                section.status = 'partial'
                section.issues.append(Issue(code='unsupported_table', message=str(exc)))
    if any(s.status != 'ok' for s in result.sections.values()):
        result.status = 'partial'
    return result
