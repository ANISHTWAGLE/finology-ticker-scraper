# Nifty 50 seed jobs

The existing FastAPI service can scrape the official Nifty 50 constituent list,
collect **both standalone and consolidated** data, and save complete snapshots to
PostgreSQL. This creates 100 company/basis items. Every item requests all six
supported sections: quarterly results, profit & loss (including growth summaries),
balance sheet, cash flow, ratios, and quarterly/yearly shareholding, including
public expandable rows. Login and paid content remain outside scope.

## Start the service

Start Docker Desktop and run the existing database setup if needed:

```powershell
./setup-postgres.ps1
.\.venv\Scripts\python.exe -m uvicorn finology_scraper.api:app --workers 1
```

Batch tables are created automatically when a batch endpoint is first used.
Existing financial tables are preserved. Job operations require PostgreSQL;
the original single-company endpoints still work without batch configuration.
Use **one API process**. A PostgreSQL advisory lock prevents two batch
coordinators from operating the same database simultaneously. Single-company
Screener requests share the batch's process-wide source limiter.

## Start a batch

`POST /screener/jobs/nifty50`

```json
{"engine":"http","concurrency":1}
```

The endpoint returns HTTP **202** with the job ID. Fetching the company list and
scraping happen in the background. `engine` is required:

JSON schemas: [request](schema/screener-batch-request.schema.json) and
[job response](schema/screener-batch-response.schema.json). Swagger also exposes
these models at `/docs`.

- `http`: direct HTTP for every source request, without browser fallback.
- `lightpanda`: Lightpanda/CDP for every source request, including detail JSON.

This explicit selection makes HTTP/Lightpanda comparisons possible. It does not
change the original single-company endpoint's HTTP-first/Chrome-fallback behavior.
`concurrency` defaults to 2, accepts integers 1–4, and controls company/basis
workers. Workers own their retrievers and browser sessions. Source requests
remain at least two seconds apart across the process. More workers can overlap
slow responses and parsing, but cannot bypass source pacing. No 429 retries or
proxy rotation are performed. A 429 starts the shared cooldown and stops new
batch dispatch; remaining items are marked interrupted for explicit retry.

Within one batch attempt, standalone and consolidated workers for the same
company share the response when both need the exact same investor-detail URL.
A worker can wait for its partner's request or use the response after it arrives.
Quarterly and yearly URLs are distinct. Each basis fetches its own company page
and financial schedules. A fresh attempt fetches the details again.

Only one batch can be active. A second request returns 409. Invalid payloads
return 422; unavailable PostgreSQL returns 503.

### Lightpanda setup

Run the pinned image tested for this implementation:

```powershell
docker run -d --rm --name scraper-lightpanda -p 127.0.0.1:19222:9222 lightpanda/browser@sha256:5d1d724c476957d8c2f8393a51fd089477523b0e5aa9c783c48688785d934fb9
$env:SCREENER_LIGHTPANDA_CDP_URL='ws://127.0.0.1:19222'
```

Set the variable in the shell that launches the API. The shown URL is the default.
Keep the CDP port bound to localhost. Each browser request uses its own CDP
connection and isolated context, then closes it. The container itself stays up
until stopped with `docker stop scraper-lightpanda`.

### Company-list fallback

The job tries the official `niftyindices.com` constituent CSV, then the official
NSE archive copy. It saves the downloaded list, source URL, fetch time and SHA-256
with the job. Retrying that job uses the same saved list, even if index membership
changes later. A fetch timestamp is not an asserted index effective date.

You may supply optional `constituent_csv` **text in the JSON request** as a fallback:

```powershell
$body = @{
    engine='http'
    concurrency=2
    constituent_csv=(Get-Content -Raw -LiteralPath 'C:\path\ind_nifty50list.csv')
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/screener/jobs/nifty50 -ContentType application/json -Body $body
```

The supplied CSV is used only when both official downloads fail. It must have
exactly 50 unique NSE symbols and ISINs, company names, and EQ series, with the
official column headings. Its provenance is explicitly `caller_supplied_csv`;
validation cannot establish its authenticity or effective date. Without a valid
list, the job fails without scraping any company. No unofficial or hard-coded
constituent list is substituted.

## Read progress

`GET /screener/jobs/{job_id}` returns the saved job document:

```json
{
  "id":"00000000-0000-0000-0000-000000000001",
  "state":"running",
  "engine":"http",
  "concurrency":2,
  "attempt":1,
  "total":100,
  "counts":{"queued":97,"running":2,"complete":1,"partial":0,"failed":0,"interrupted":0},
  "items":[
    {
      "nse_symbol":"ADANIENT",
      "reporting_basis":"standalone",
      "status":"complete",
      "persistence":"saved",
      "sections":{
        "quarterly_results":{"status":"ok","tables":1,"rows":25,"issues":[]}
      },
      "error":null
    }
  ]
}
```

This is an abbreviated example; the real response includes all items, all
collected section summaries, timestamps, and the saved constituent snapshot.
While the company list is still being fetched, `items` is empty and all counts
are zero; `total` remains the expected 100.

Job and item states: `queued`, `running`, `complete`, `partial`, `failed`,
`interrupted`. `complete` requires all 100 items complete. An item reports
section statuses/issues separately from `persistence`. Complete scrapes save
through the existing transactional replacement function; partial scrapes return
`skipped_partial` and preserve previous data. Database write failures make the
item failed and preserve the prior snapshot. `skipped_older` means a newer
snapshot already exists. The status API contains summaries; full numeric data
remains in the existing `screener_financials` PostgreSQL view.

Financial writes and job-progress writes are separate transactions. A crash
between them can leave a successfully saved dataset with an interrupted item.
Explicit retry safely re-scrapes it; source timestamps and the existing
replacement checks prevent an older snapshot overwriting newer data.

Unknown IDs return 404. Invalid UUIDs return 422.

## Restart and retry

Job documents survive API restarts. On first batch access after restart, the
new coordinator marks previously queued/running jobs and unfinished items
`interrupted`. Completed items remain recorded. Nothing automatically resumes.

`POST /screener/jobs/{job_id}/retry` returns 202 and retries only items that are
not complete, using the saved list and original engine/concurrency. Active and
already complete jobs return 409. Wait for source cooldown to end before retrying
a rate-limited job. If no constituent snapshot was ever saved, start a new job
and supply CSV fallback if needed. To compare a different engine, start a new job.

## Reproduce the comparison

With Lightpanda running:

```powershell
.\.venv\Scripts\python.exe -m screener_scraper.benchmark --symbols TCS INFY --basis consolidated --concurrency 1 2
```

This runs full extraction through both engines at both worker counts and prints
one JSON line per run, including per-section issues, row counts and elapsed time.
It does **not** save financial snapshots. Runs are sequential across settings,
so results are affected by network conditions, source changes and caching.
They are a small live comparison, not a Nifty 50 throughput guarantee.

The [4 November 2026 live comparison](benchmarks/2026-11-04-http-lightpanda.md)
favored HTTP with one worker. Both engines returned partial results because a
nested expense detail returned 403. Lightpanda had additional detail retrieval
failures; two workers did not improve total time at the preserved source pacing.
Use `{"engine":"http","concurrency":1}` for that configuration.

On 6 November 2026, live TCS extraction for both bases took 106.28 seconds
and 54 outbound HTTP requests with two workers fetching separately. Sharing
identical investor requests reduced this to 44 requests and 86.33 seconds with
two workers, or 86.53 seconds with one. Each configuration ran once with the
same two-second pacing. The section JSON matched across all three HTTP runs for
each basis (804 standalone rows and 805 consolidated rows). Both returned
partial results because the nested Other Expenses detail returned HTTP 403.
The paired Lightpanda attempt encountered an SSL connection error loading the
standalone page. These timings cover extraction for one company and exclude
database writes and batch coordination.

In a subsequent controlled TCS trial, two workers at one-second spacing
finished both bases in 43.16 seconds with urllib, 43.16 seconds with curl_cffi,
and 43.50 seconds with HTTPX. All three matched the earlier section JSON hashes
and retained the same two 403 issues. At half-second spacing, curl_cffi received
HTTP 429 on request 31 and stopped with incomplete results. The tests ran
consecutively, so cumulative request volume could have contributed to the 429.
These are single-company extraction tests, not a large-scale reliability test.
The production two-second spacing was not changed.

### Live public data check

On 7 November 2026, a logged-out browser opened TCS's nested Other Expenses %
detail. Screener returned a 403 with an explicit registration redirect. The
worker now records a warning for details with that signal and can save the
complete public snapshot. Other unexplained 403 responses still produce partial
results. The worker continues to check each detail.

The production batch item worker ran directly against Screener with HTTP, one
worker, two-second spacing, and shared investor requests. All four fresh
snapshots were saved and read back from PostgreSQL. Each had six sections and
the registration warning. Elapsed time includes both financial saves per pair.

| Company | Requests for both bases | Seconds | Standalone cells saved | Consolidated cells saved |
| --- | ---: | ---: | ---: | ---: |
| TCS | 44 | 93.4 | 9,369 | 9,384 |
| ITC | 42 | 82.7 | 2,194 | 2,246 |

This live check covered two companies. The 50-company coordinator and its
status API were exercised separately in PostgreSQL integration tests using
simulated source data.

The one-worker, 100-item PostgreSQL integration test uses 105 job-progress
writes. The previous scheduling loop used 205 for the same sequence. A single
write now records the finished item and the next item's start. These counts
exclude financial data writes.

## Tests

```powershell
$env:SCREENER_TEST_POSTGRES='1'
.\.venv\Scripts\python.exe -m pytest -q
```

PostgreSQL tests create/drop isolated temporary schemas. Batch API/progress tests
use simulated company workers; they verify coordination and persistence, not
live source extraction. An additional integration test uses the actual batch
worker and financial-save function with simulated source results, and verifies
50 companies, 100 reporting-basis datasets and 600 fixture cells in PostgreSQL.
Live benchmark observations are documented separately.
