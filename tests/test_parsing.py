from datetime import datetime, timezone

from bs4 import BeautifulSoup
import pytest

from finology_scraper.models import Basis
from finology_scraper.parsing import (StructureError, parse_sections, parse_table, stated_context,
                                      table_context, value)


AT = datetime(2026, 10, 24, tzinfo=timezone.utc)
URL = "https://ticker.finology.in/company/TCS?mode=S"


def table(body, headers=("Particulars", "Mar 2025", "Jun 2025", "TTM")):
    html = "<table><thead><tr>" + "".join(f"<th>{x}</th>" for x in headers)
    return BeautifulSoup(html + "</tr></thead><tbody>" + body + "</tbody></table>", "html.parser").table


def parse(node):
    return parse_table(node, "Profit & Loss (All Figures in Cr.)", URL, AT, Basis.standalone)


def test_blank_value_keeps_its_period_and_zero_is_present():
    r = parse(table("<tr><th>Net Profit</th><td>10</td><td></td><td>0.00</td></tr>"))
    vals = r.rows[0].values
    assert [(v.period, v.raw_text) for v in vals] == [("Mar 2025", "10"), ("Jun 2025", ""), ("TTM", "0.00")]
    assert vals[1].numeric_value is None and vals[1].missing_reason == "blank"
    assert vals[2].numeric_value == 0 and vals[2].status == "present"


@pytest.mark.parametrize("raw,number", [("1,23,456.78", 123456.78), ("(1,234.50)", -1234.5),
    ("−12.5", -12.5), ("12.5%", 12.5), ("Rs. 32.67", 32.67), ("₹ 0", 0),
    ("9007199254740993", 9007199254740993)])
def test_numeric_parsing_preserves_source_text_and_scale(raw, number):
    parsed = value(raw, "Sep 2026", 0)
    assert parsed.numeric_value == number and parsed.raw_text == raw


@pytest.mark.parametrize("raw", ["", "-", "—", "–", "N/A", "NA"])
def test_missing_markers_are_not_zero(raw):
    assert value(raw, "TTM", 0).status == "missing"
    assert value(raw, "TTM", 0).numeric_value is None


@pytest.mark.parametrize("raw", ["NM", "1,2,34", "1.2.3", "NaN", "Infinity", "1e309", "10*"])
def test_unsupported_numeric_text_is_retained_without_guessing(raw):
    parsed = value(raw, "TTM", 0)
    assert parsed.raw_text == raw and parsed.numeric_value is None and parsed.status == "unparsed"


def test_missing_cell_cannot_shift_data():
    with pytest.raises(StructureError, match="missing period"):
        parse(table("<tr><th>Profit</th><td>10</td><td>20</td></tr>"))


def test_merged_cells_are_explicit_failure():
    with pytest.raises(StructureError, match="Merged"):
        parse(table("<tr><th>Profit</th><td colspan='3'>10</td></tr>"))


def test_duplicate_period_and_metric_labels_preserve_positions():
    r = parse(table("<tr><th>Total</th><td>1</td><td>2</td></tr>" * 2,
                    headers=("Metric", "Mar 2025", "Mar 2025")))
    assert len(r.rows) == 2 and r.rows[1].row_index == 1
    assert [v.column_index for v in r.rows[0].values] == [0, 1]
    assert r.periods == ["Mar 2025", "Mar 2025"]


def test_mixed_units_do_not_make_eps_crores():
    context = table_context("Profit & Loss (All Figures in Cr. Adjusted EPS in Rs.)")
    assert context.unit == "Cr." and context.currency is None
    eps = stated_context("Adjusted EPS (Rs.)", context)
    assert eps.unit == "Rs." and eps.currency == "Rs."


def test_date_in_rows_is_transposed_without_reordering():
    r = parse_table(table("<tr><td>Sep 2026</td><td>0.00</td><td>-</td></tr>"
                          "<tr><td>Jun 2026</td><td>1.00</td><td></td></tr>",
                         headers=("Date", "Promoter %", "Pledge %")),
                    "Promoter Pledging %", URL, AT, None, dates_in_rows=True)
    assert r.periods == ["Sep 2026", "Jun 2026"]
    assert r.rows[0].metric_label == "Promoter %"
    assert [v.numeric_value for v in r.rows[0].values] == [0, 1]
    assert r.rows[1].values[0].missing_reason == "source_marker"
    assert r.rows[1].values[1].missing_reason == "blank"


def sections(snapshot):
    return parse_sections(snapshot.html, snapshot.source_url, snapshot.scraped_at, Basis.standalone,
        chart_text=snapshot.chart_text, chart_url=snapshot.chart_url, chart_at=snapshot.chart_at,
        legends=snapshot.legends)


def test_real_source_fixture_includes_all_financial_and_investor_rows(captured_tcs):
    result = sections(captured_tcs)
    assert result.quarterly_results.status == "ok"
    assert len(result.balance_sheet.tables[0].rows) == 16
    assert len(result.shareholding_pattern.tables) == 4
    assert [len(t.rows) for t in result.shareholding_pattern.tables] == [5, 2, 3, 7]
    sales = result.quarterly_results.tables[0].rows[0]
    assert sales.metric_label == "Net Sales"
    assert [(v.period, v.numeric_value) for v in sales.values] == [
        ("Sep 2025", 54531), ("Dec 2025", 55567), ("Mar 2026", 58052),
        ("Jun 2026", 59553), ("Sep 2026", 60441)]


def test_approved_undated_chart_is_warning_not_inferred_period(captured_tcs):
    section = sections(captured_tcs).shareholding_pattern
    assert section.status == "warning" and section.warnings[0].code == "period_unstated"
    chart = section.tables[0]
    assert chart.periods == [None] and chart.reporting_basis is None
    assert chart.rows[0].values[0].raw_text == "71.77%"
    assert chart.rows[0].values[0].period is None


def test_chart_category_order_mismatch_preserves_other_tables_as_partial(captured_tcs):
    captured_tcs.legends.reverse()
    s = sections(captured_tcs).shareholding_pattern
    assert s.status == "partial" and s.errors[0].code == "structure_changed"


def test_chart_failure_is_not_success(captured_tcs):
    captured_tcs.chart_text = None
    s = sections(captured_tcs).shareholding_pattern
    assert s.status == "partial" and s.errors[0].code == "request_failed"


def test_absent_section_and_changed_structure_are_distinct(captured_tcs):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    soup.find(id="mainContent_cashflows").decompose()
    soup.find(id="balance").find("table").decompose()
    captured_tcs.html = str(soup)
    s = sections(captured_tcs)
    assert s.cash_flow.status == "absent"
    assert s.balance_sheet.status == "failed" and s.balance_sheet.errors[0].code == "structure_changed"


def test_empty_table_is_distinct_from_absent(captured_tcs):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    soup.find(id="mainContent_cashflows").find("tbody").clear()
    captured_tcs.html = str(soup)
    s = sections(captured_tcs).cash_flow
    assert s.status == "empty" and s.errors[0].code == "table_empty"


def test_empty_supplementary_investor_table_is_not_a_structure_failure(captured_tcs):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    table = soup.find(id="pills-Promoter").find("table")
    table.find("thead").find("tr").clear()
    table.find("tbody").clear()
    captured_tcs.html = str(soup)

    s = sections(captured_tcs).shareholding_pattern

    assert s.status == "warning"
    promoter = next(t for t in s.tables if t.name.endswith("Promoter"))
    assert promoter.periods == [] and promoter.rows == []
    assert any(w.code == "supplementary_table_empty" for w in s.warnings)


def test_empty_header_stays_a_failure_for_required_financial_table(captured_tcs):
    soup = BeautifulSoup(captured_tcs.html, "html.parser")
    table = soup.find(id="profit").find("table")
    table.find("thead").clear()
    table.find("tbody").clear()
    captured_tcs.html = str(soup)

    s = sections(captured_tcs).profit_and_loss

    assert s.status == "failed" and s.errors[0].code == "structure_changed"


def test_raw_displayed_text_overrides_hidden_unrounded_number():
    r = parse(table("<tr><th>Revenue</th><td><span value='12.34567'>12.35</span></td><td>0</td><td>0</td></tr>"))
    assert r.rows[0].values[0].numeric_value == 12.35


def test_blank_group_heading_row_is_preserved():
    r = parse(table("<tr><th>Assets</th><td></td><td></td><td></td></tr>"))
    assert r.rows[0].metric_label == "Assets"
    assert all(v.status == "missing" for v in r.rows[0].values)


def test_blank_period_header_is_rejected():
    with pytest.raises(StructureError, match="headings are missing"):
        parse(table("", headers=("Metric", "Mar 2025", "", "TTM")))


def test_commentary_does_not_override_the_actual_financial_section(captured_tcs):
    captured_tcs.html += "<h3>Balance Sheet and Cash Flow Metrics</h3><p>Commentary</p>"
    assert sections(captured_tcs).balance_sheet.status == "ok"
