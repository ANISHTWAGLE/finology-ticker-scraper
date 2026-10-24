# Output schema, version 1.0

Machine-readable definitions are in `schema/scrape-request.schema.json` and `schema/scrape-response.schema.json`. Models in `finology_scraper/models.py` are the contract source. Live OpenAPI is at `/openapi.json`.

## Request

Required: `company` (Finology company-page slug or HTTPS URL) and `reporting_basis` (`standalone` or `consolidated`). Extra fields are rejected. URL mode S/C, if present, must match the explicit basis.

## Response hierarchy

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0` |
| `status` | `complete`, `complete_with_warnings`, `partial`, or `failed` |
| `company` | Page identifier, source company name, and available NSE/BSE identifiers |
| `requested_reporting_basis` | Caller-selected basis |
| `source_url` | Fetched company URL including mode |
| `scraped_at` | UTC company-document retrieval time, not a reporting period |
| `sections` | Always the five required section keys |
| `warnings`, `errors` | Overall issues with stable codes and plain explanations |

Section keys: `shareholding_pattern`, `quarterly_results`, `profit_and_loss`, `balance_sheet`, `cash_flow`.

Each section has `name`, `status`, `tables`, `warnings`, `errors`. Status meanings:

- `ok`: extracted without current warnings.
- `warning`: extracted with a qualification such as an unstated chart period.
- `partial`: some tables succeeded but another component failed.
- `absent`: no matching section appears in the received company page; not a claim about other sources.
- `empty`: table/section present without non-missing values.
- `failed`: safe extraction could not be completed.

Only ok/warning sections count toward complete results. Absent/empty/failed sections cause a partial result if any other data was captured. Overall source failures mark all five sections failed. The approved chart-period exception permits complete_with_warnings.

## Tables, rows and values

A table has its original `name`, `source_format` (`html_table` or `chart`), `source_url`, UTC `scraped_at`, `reporting_basis`, ordered `periods`, `context`, source `notes`, and ordered `rows`.

Financial tables carry the source-verified reporting basis. Shareholding tables have basis null because the source does not label ownership figures standalone/consolidated. Chart tables carry their actual endpoint URL and retrieval time; HTML tables carry the company URL/document time.

A row contains `row_index`, original `metric_label`, effective `context`, and ordered `values`. Positions distinguish duplicate labels. Blank group rows such as Assets are retained. Explicit row units override table defaults, including EPS in rupees.

| Value field | Meaning |
| --- | --- |
| `column_index` | Zero-based period-column position; duplicate headings remain distinct |
| `period` | Original source heading, including special labels such as TTM; null for undated chart values |
| `raw_text` | Source text with layout whitespace collapsed; number formatting and missing markers retained |
| `numeric_value` | Parsed JSON integer/finite float or null, in source units |
| `status` | `present`, `missing`, or `unparsed` |
| `missing_reason` | `blank`, `source_marker`, or null |

A displayed zero is present with numeric zero. A blank/known missing marker has missing status and null numeric value. Unsupported text is retained as unparsed, with a section warning, rather than replaced by zero.

The pledging table has dates in rows and is transposed without changing date order. Investor tables retain heading text including its percent suffix. Calendar dates are never converted to fiscal years/quarters. No chart period is inferred from pledging or investor dates.

`context` includes `currency`, `unit`, and `stated_text`. Null means the source did not explicitly state an understood currency/unit. There is no global INR default. Table names/labels/notes also retain original context.

## Error shapes

Codes include `invalid_company_input`, `company_not_found`, `page_unavailable`, `access_blocked`, `basis_unavailable`, `section_absent`, `table_empty`, `request_failed`, `timeout`, `structure_changed`, `service_busy`, `internal_error`.

Malformed request fields/JSON receive HTTP 422 and FastAPI's standard `detail` list. A valid body with an invalid company target receives the full scrape envelope with failed section statuses. Invalid bodies may not contain a usable company or reporting basis, so these shapes are distinct.

If a decoded invalid input contains infinity or NaN (including an overflowed exponent), its value in the validation error details is represented as a string so the response remains valid JSON. Missing investor containers, duplicate financial containers and empty pledging tables produce explicit section errors and cannot count toward a complete result.

On failures before a document is retrieved, source_url may be empty and scraped_at records the failed attempt time. No table retrieval timestamp is fabricated.
