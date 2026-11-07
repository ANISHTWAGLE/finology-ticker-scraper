# Screener endpoint

Complete scrapes now update the latest structured PostgreSQL data automatically
when configured. See [PostgreSQL setup and SQL examples](postgres.md). Partial
scrapes leave the saved data unchanged; inspect the response's `persistence` field.

The existing API now also serves `POST /screener/scrape`. Run the same command:

```powershell
.\.venv\Scripts\python.exe -m uvicorn finology_scraper.api:app
```

Open `http://127.0.0.1:8000/docs` and select the Screener endpoint.

```json
{
  "nse_symbol": "MRF",
  "bse_code": "500290",
  "reporting_basis": "consolidated"
}
```

At least one identifier is required. Codes must be strings. Reporting basis must
explicitly be `standalone` or `consolidated`. URLs and company-name search are not
accepted by this endpoint. NSE is tried first; a supplied BSE code is used on a
missing or failed NSE lookup. Conflicting identifiers are rejected when both are
resolved on the NSE page. Basis is never silently changed.

## Connected scope

Quarterly results, profit & loss, balance sheet, cash flow, ratios, and both
quarterly and yearly shareholding. All available columns, raw PDF cell links,
and public `+` detail rows (including nested expansions) are collected. The four
growth/return summary tables under profit & loss are included too. The response
also returns the company name, NSE symbol and BSE code. Login-only and locked
insight tables are outside the approved public scope.

If a detail request returns HTTP 403 with Screener's explicit registration or
login redirect, the scraper keeps the public parent row and records
`detail_registration_required` in the section issues and dataset warnings.
The public snapshot can still be saved. An unexplained 403 makes the result
partial and prevents replacement of the saved snapshot.

Periods retain their exact labels, with year/month when explicitly present.
Duration badges such as `18m` remain in period labels even when the source's
detail JSON uses only the month/year. TTM and relative growth periods are not
assigned invented dates. Values retain displayed text, parsed numbers and row-appropriate units. Blank
values stay distinct from zero. Detail values are aligned by period label.

## Retrieval and status

Direct unauthenticated HTTP is tried first. HTTP 403 can fall back to a fresh
installed Chrome session. No personal profile or account cookies are used.
HTTP 429 does not trigger browser or BSE fallback. Requests within the process
are spaced at least two seconds apart; a rate-limit response starts a cooldown.
This is not a guarantee against source rate limits. Run one API worker: rate
limits and the request lock are process-local.

- 200: all six sections and their public row expansions collected.
- 207: partial extraction; inspect section issues.
- 422: invalid request or conflicting exchange identifiers.
- 404: company not found.
- 429: source rate limit; retry later.
- 502/504: retrieval, unsupported page, or timeout error.
- 503: another Screener scrape is running.

The browser path is implemented but not live-validated: direct HTTP succeeded.
Live verification uses FastAPI TestClient with real upstream HTTP requests;
it is not a separate deployed-server test. Offline tests cover parsing, fallback,
basis, identity, malformed tables, duration badges, rate-limit handling and
endpoint validation. Missing sections or failed expansions produce HTTP 207
with explicit issues instead of claiming complete extraction.

The final live MRF consolidated check returned HTTP 200, all six sections `ok`:
quarterly results 23 rows, profit & loss 27 rows plus four summary tables,
balance sheet 33 rows, cash flow 29 rows, ratios 6 rows, quarterly shareholding
140 rows and yearly shareholding 162 rows. Counts include expanded details and
are observations, not fixed parser limits. The offline suite passed 125 tests.
