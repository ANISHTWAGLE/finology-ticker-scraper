"""Source parsing only: no browser, network or HTTP-interface dependency."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
import re

from bs4 import BeautifulSoup, Tag

from .models import Basis, Company, Context, Issue, Row, Section, Sections, Table, Value


FINANCIAL_SECTIONS = {
    "quarterly_results": ("Quarterly results", "mainContent_quarterly", r"Quarterly Results?"),
    "profit_and_loss": ("Profit and loss", "profit", r"Profit\s*&\s*Loss"),
    "balance_sheet": ("Balance sheet", "balance", r"Balance Sheet"),
    "cash_flow": ("Cash flow", "mainContent_cashflows", r"Cash Flows?"),
}
SECTION_NAMES = {"shareholding_pattern": "Shareholding pattern",
                 **{key: value[0] for key, value in FINANCIAL_SECTIONS.items()}}


class StructureError(ValueError):
    pass


def text(node: Tag) -> str:
    """Collapse HTML layout whitespace, retaining all source words and punctuation."""
    return " ".join(node.get_text(" ", strip=True).split())


def value(raw: str, period: str | None, column: int) -> Value:
    if raw.casefold() in {"", "-", "–", "—", "n/a", "na"}:
        return Value(column_index=column, period=period, raw_text=raw, numeric_value=None,
                     status="missing", missing_reason="blank" if raw == "" else "source_marker")
    candidate = raw.strip().replace("−", "-")
    candidate = re.sub(r"^(?:₹|Rs\.?|INR)\s*", "", candidate, flags=re.I)
    candidate = re.sub(r"\s*%$", "", candidate)
    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = "-" + candidate[1:-1].strip()
    # Accept ungrouped, western-grouped and Indian-grouped numbers only.
    pattern = r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.\d+)?"
    parsed = None
    if re.fullmatch(pattern, candidate):
        try:
            exact = Decimal(candidate.replace(",", ""))
            parsed = int(exact) if exact == exact.to_integral_value() else float(exact)
            if isinstance(parsed, float) and not math.isfinite(parsed):
                parsed = None
        except (InvalidOperation, OverflowError, ValueError):
            pass
    return Value(column_index=column, period=period, raw_text=raw, numeric_value=parsed,
                 status="present" if parsed is not None else "unparsed")


def stated_context(label: str, default: Context | None = None) -> Context:
    """Never infer a currency from the site's location or another section."""
    if "%" in label:
        return Context(unit="%", stated_text=label)
    rupees = re.search(r"\bRs\.?|₹|\bINR\b", label, flags=re.I)
    if rupees:
        return Context(currency=rupees.group(), unit=rupees.group(), stated_text=label)
    crores = re.search(r"\bCr\.|\bCrores?\.?", label, flags=re.I)
    if crores:
        return Context(unit=crores.group(), stated_text=label)
    return default.model_copy() if default else Context()


def table_context(heading: str) -> Context:
    # A mixed-unit heading may mention EPS in rupees; use its opening table-unit statement.
    primary = heading.split("Adjusted EPS", 1)[0]
    return stated_context(primary)


def cells(tr: Tag) -> list[Tag]:
    result = tr.find_all(["th", "td"], recursive=False)
    if any(c.get("rowspan", "1") != "1" or c.get("colspan", "1") != "1" for c in result):
        raise StructureError("Merged cells require an explicit mapping; no values were guessed.")
    return result


def parse_table(node: Tag, name: str, url: str, at: datetime, basis: Basis | None,
                *, dates_in_rows: bool = False, context: Context | None = None,
                notes: list[str] | None = None) -> Table:
    heads = node.select("thead > tr")
    bodies = node.find_all("tbody", recursive=False)
    if len(heads) != 1 or len(bodies) != 1:
        raise StructureError("Expected one table header row and one table body.")
    headers = [text(c) for c in cells(heads[0])]
    if len(headers) < 2 or any(not h for h in headers):
        raise StructureError("Table metric or period headings are missing.")
    source_rows = [cells(tr) for tr in bodies[0].find_all("tr", recursive=False)]
    for i, row in enumerate(source_rows):
        if len(row) != len(headers):
            raise StructureError(f"Row {i + 1} has {len(row)} cells for {len(headers)} headings; "
                                 "the missing period cannot be located safely.")
        if not text(row[0]):
            raise StructureError(f"Row {i + 1} has a blank metric or date label.")
    context = context or table_context(name)
    if dates_in_rows:
        if headers[0].casefold() != "date":
            raise StructureError("Expected Date as the first column of the pledging table.")
        periods = [text(row[0]) for row in source_rows]
        rows = [Row(row_index=i, metric_label=label, context=stated_context(label, context),
                    values=[value(text(row[i + 1]), periods[j], j)
                            for j, row in enumerate(source_rows)])
                for i, label in enumerate(headers[1:])]
    else:
        periods = headers[1:]
        rows = [Row(row_index=i, metric_label=text(row[0]),
                    context=stated_context(text(row[0]), context),
                    values=[value(text(c), periods[j], j) for j, c in enumerate(row[1:])])
                for i, row in enumerate(source_rows)]
    return Table(name=name, source_url=url, scraped_at=at, reporting_basis=basis,
                 periods=periods, context=context, notes=notes or [], rows=rows)


def table_has_data(table: Table) -> bool:
    return any(v.status != "missing" for row in table.rows for v in row.values)


def add_numeric_warnings(section: Section):
    count = sum(v.status == "unparsed" for t in section.tables for r in t.rows for v in r.values)
    if count:
        section.warnings.append(Issue(code="numeric_unparsed", message=
                                      f"{count} displayed values could not be parsed as numbers; raw text is retained."))
        if section.status == "ok":
            section.status = "warning"


def parse_financial(soup: BeautifulSoup, key: str, url: str, at: datetime, basis: Basis) -> Section:
    name, container_id, label = FINANCIAL_SECTIONS[key]
    container = soup.find(id=container_id)
    headings = [h for h in (container or soup).find_all(["h2", "h3", "h4"])
                if re.fullmatch(label + r"(?:\s*\(.*\))?", text(h), flags=re.I)]
    if not headings and container is None:
        return Section(name=name, status="absent", errors=[Issue(code="section_absent",
                       message="No matching section appears in the retrieved company page.")])
    if len(headings) != 1:
        return Section(name=name, status="failed", errors=[Issue(code="structure_changed",
                       message=f"Expected one financial heading; found {len(headings)}.")])
    heading = headings[0]
    tables = heading.parent.find_all("table")
    if len(tables) != 1:
        return Section(name=name, status="failed", errors=[Issue(code="structure_changed",
                       message=f"Expected one financial table; found {len(tables)}.")])
    remarks = [text(n) for n in (container or heading.parent).select(".noteremarks")]
    try:
        parsed = parse_table(tables[0], text(heading), url, at, basis, notes=remarks)
    except StructureError as exc:
        return Section(name=name, status="failed", errors=[Issue(code="structure_changed", message=str(exc))])
    section = Section(name=name, status="ok" if table_has_data(parsed) else "empty", tables=[parsed])
    if section.status == "empty":
        section.errors.append(Issue(code="table_empty", message="The table has no non-missing data values."))
    add_numeric_warnings(section)
    return section


def parse_shareholding(soup: BeautifulSoup, url: str, at: datetime, chart_text: str | None,
                      chart_url: str | None, chart_at: datetime | None,
                      legends: list[str], chart_error: Issue | None) -> Section:
    import json

    section = Section(name=SECTION_NAMES["shareholding_pattern"], status="ok")
    container = soup.find(id="mainContent_ShareHolding")
    investor_root = soup.find(id="mainContent_investors")
    if container is None and investor_root is None and chart_text is None:
        return Section(name=section.name, status="absent", errors=[Issue(
            code="section_absent", message="No shareholding section appears in the retrieved page.")])
    if container is None:
        section.errors.append(Issue(code="structure_changed", message="Shareholding section container is missing."))
    expects_chart = bool(container and container.find(id="Share-chart-area"))
    if chart_text is not None:
        try:
            data = json.loads(chart_text)
            if not isinstance(data, list):
                raise StructureError("Shareholding endpoint did not return an array.")
            if len(legends) != len(data):
                raise StructureError("Chart legend does not match the number of endpoint categories.")
            rows = []
            for i, item in enumerate(data):
                if not isinstance(item, dict) or not isinstance(item.get("Particulars"), str):
                    raise StructureError("A chart category label is missing.")
                label = item["Particulars"]
                prefix = label + " : "
                if not legends[i].startswith(prefix):
                    raise StructureError("Chart labels and endpoint category order differ.")
                raw = legends[i][len(prefix):]
                parsed = value(raw, None, 0)
                if parsed.status != "present" or isinstance(item.get("Data"), bool) or parsed.numeric_value != item.get("Data"):
                    raise StructureError("Displayed chart value does not match its endpoint value.")
                rows.append(Row(row_index=i, metric_label=label, context=Context(unit="%"), values=[parsed]))
            section.tables.append(Table(name="Shareholding Pattern", source_format="chart",
                source_url=chart_url or url, scraped_at=chart_at or at, reporting_basis=None,
                periods=[None], context=Context(unit="%", stated_text="Chart legend percentages"), rows=rows))
            if data:
                section.warnings.append(Issue(code="period_unstated", message=
                    "The source chart and endpoint do not state a reporting period. period is null, as approved; no date was inferred."))
            else:
                section.errors.append(Issue(code="table_empty", message="Shareholding chart returned no categories."))
        except (ValueError, TypeError) as exc:
            section.errors.append(Issue(code="structure_changed", message=str(exc)))
    elif expects_chart:
        section.errors.append(chart_error or Issue(code="request_failed", message="Shareholding chart data was not received."))
    elif container:
        section.errors.append(Issue(code="structure_changed", message="Expected shareholding chart is missing."))

    if container:
        pledging = container.find_all("table")
        if len(pledging) != 1:
            section.errors.append(Issue(code="structure_changed", message="Expected one promoter-pledging table."))
        else:
            try:
                section.tables.append(parse_table(pledging[0], "Promoter Pledging %", url, at, None,
                    dates_in_rows=True, context=Context(unit="%", stated_text="% of equity capital")))
            except StructureError as exc:
                section.errors.append(Issue(code="structure_changed", message=str(exc)))
    if investor_root:
        for pane_id, table_name in [("pills-Promoter", "Investors Details — Promoter"),
                                    ("pills-Investors", "Investors Details — Investors")]:
            pane = investor_root.find(id=pane_id)
            tables = pane.find_all("table") if pane else []
            if len(tables) != 1:
                section.errors.append(Issue(code="structure_changed", message=f"Expected one {table_name} table."))
                continue
            try:
                parsed = parse_table(tables[0], table_name, url, at, None,
                                     context=Context(unit="%", stated_text="% in period column headings"))
                section.tables.append(parsed)
                if not table_has_data(parsed):
                    section.warnings.append(Issue(code="supplementary_table_empty", message=f"{table_name} has no data."))
            except StructureError as exc:
                section.errors.append(Issue(code="structure_changed", message=f"{table_name}: {exc}"))
    if section.errors:
        section.status = "partial" if any(table_has_data(t) for t in section.tables) else "failed"
    elif section.warnings:
        section.status = "warning"
    elif not any(table_has_data(t) for t in section.tables):
        section.status = "empty"
        section.errors.append(Issue(code="table_empty", message="Shareholding contains no data values."))
    add_numeric_warnings(section)
    return section


def parse_company(soup: BeautifulSoup, identifier: str) -> Company:
    node = soup.find(id="mainContent_ltrlCompName")
    if node is None or not text(node):
        raise StructureError("Company identity is missing; this is not a recognized company page.")
    info = soup.find(id="mainContent_compinfoId")
    label = text(info) if info else ""
    nse = re.search(r"\bNSE:\s*([^\s]+)", label)
    bse = re.search(r"\bBSE:\s*(\d+)", label)
    return Company(identifier=identifier, name=text(node), nse_symbol=nse.group(1) if nse else None,
                   bse_code=bse.group(1) if bse else None)


def parse_sections(html: str | BeautifulSoup, url: str, at: datetime, basis: Basis, *, chart_text=None,
                   chart_url=None, chart_at=None, legends=None, chart_error=None) -> Sections:
    soup = html if isinstance(html, BeautifulSoup) else BeautifulSoup(html, "html.parser")
    sections = {key: parse_financial(soup, key, url, at, basis) for key in FINANCIAL_SECTIONS}
    sections["shareholding_pattern"] = parse_shareholding(soup, url, at, chart_text, chart_url,
                                                          chart_at, legends or [], chart_error)
    return Sections(**sections)
