"""Parse dates and cells without assuming fixed history length or fiscal years."""

import math
import re
from urllib.parse import urljoin
from .models import Cell, Period, Row, Table

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def period(label):
    match = re.fullmatch(r"([A-Za-z]{3}) (\d{4})(?: (\d+)m)?", label)
    if match and match[1] in MONTHS:
        return Period(label=label, year=int(match[2]), month=MONTHS.index(match[1]) + 1,
                      duration_months=int(match[3]) if match[3] else None)
    return Period(label=label)


def cell(raw, label, section, context, links=None):
    unit = None
    if '%' in raw or '%' in label or section == 'shareholding' and label != 'No. of Shareholders':
        unit = 'percent'
    elif label == 'No. of Shareholders':
        unit = 'count'
    elif 'EPS' in label:
        unit = 'INR per share'
    elif section == 'ratios':
        unit = 'days' if 'Days' in label or 'Cycle' in label else None
    elif context and 'Rs. Crores' in context:
        unit = 'INR crore'
    value = None
    status = 'missing' if raw in ('', '-', '—', 'N/A') else 'unparsed'
    clean = raw.replace(',', '').replace('%', '').strip()
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", clean):
        value = float(clean)
        if math.isfinite(value):
            status = 'present'
        else:
            value = None
    return Cell(raw_text=raw, numeric_value=value, unit=unit, status=status, links=links or [])


def parse_table(element, name, section, context, source):
    headers = element.select('thead th')
    labels = [h.get_text(' ', strip=True) for h in headers[1:]]
    if not labels or len(labels) != len(set(labels)) or any(not x for x in labels):
        raise ValueError('Missing, empty or duplicate period headers.')
    rows = []
    for tr in element.select('tbody > tr'):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) != len(labels) + 1 or any(c.get('colspan', '1') != '1' for c in cells):
            raise ValueError('Table row does not match its period headers.')
        label = cells[0].get_text(' ', strip=True)
        if not label:
            raise ValueError('Empty row label.')
        rows.append(Row(label=label, source_url=source, values=[
            cell(c.get_text(' ', strip=True), label, section, context,
                 [urljoin(source, a['href']) for a in c.select('a[href]')]) for c in cells[1:]]))
    if not rows:
        raise ValueError('Table has no data rows.')
    return Table(name=name, periods=[period(x) for x in labels], stated_context=context, rows=rows)


def parse_range_table(element, source):
    heading = element.find('th')
    pairs = [tr.find_all('td', recursive=False) for tr in element.select('tr') if tr.find('td')]
    if heading is None or not pairs or any(len(pair) != 2 for pair in pairs):
        raise ValueError('Unsupported growth summary table.')
    name = heading.get_text(' ', strip=True)
    labels = [pair[0].get_text(' ', strip=True) for pair in pairs]
    if len(set(labels)) != len(labels) or not all(labels):
        raise ValueError('Missing or duplicate growth summary periods.')
    return Table(name=name, periods=[period(label) for label in labels], rows=[
        Row(label=name, source_url=source, values=[
            cell(pair[1].get_text(' ', strip=True), name, 'profit-loss', None) for pair in pairs])])
