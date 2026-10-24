# Finology Ticker Scraper

A Python HTTP API that extracts five sections from Ticker by Finology:

- Shareholding pattern
- Quarterly results
- Profit and loss
- Balance sheet
- Cash flow

Each value includes its metric, reporting period, original displayed text and parsed number. Source units are preserved. Missing values remain different from zero.

## 1. Requirements

Use **Windows PowerShell**, **Python 3.12**, **Git**, an installed **Google Chrome**, and an internet connection. Windows with a desktop session is the tested environment. No API key, login or paid subscription is required.

Check Python and Git:

```powershell
python --version
git --version
```

Python should report version 3.12. If either command is unavailable, install that program and reopen PowerShell. Install Google Chrome before starting the service. Playwright uses this Chrome installation; you do not need to run `playwright install`.

## 2. Download and install

Run these commands in the folder where you want the project:

```powershell
git clone https://github.com/ANISHTWAGLE/finology-ticker-scraper.git
Set-Location .\finology-ticker-scraper
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

There is no need to activate the virtual environment. The commands below call its Python directly.

Main dependencies are FastAPI, Uvicorn, Playwright, BeautifulSoup and Pydantic. Their versions are listed in `requirements.txt`. Test dependencies are in `requirements-dev.txt`.

## 3. Start the API

From the project folder, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn finology_scraper.api:app --host 127.0.0.1 --port 8000
```

Keep this terminal running. Use this command as shown, with one worker and without `--reload`. Chrome starts minimized on the first scrape and is reused. Each request gets a fresh isolated browser context.

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser:

1. Expand **POST /scrape** and choose an example from the **Examples** menu.
2. Select **Try it out** and replace `TCS` with the company's Finology page identifier or full company URL.
3. Choose `standalone` for the company's own accounts, or `consolidated` for the company and its subsidiaries.
4. Select **Execute**.

The simplest request is:

```json
{"company": "TCS", "reporting_basis": "standalone"}
```

In the response, `status` tells you whether extraction is complete or partial. If it is partial, check each item under `sections` for its `errors`. Warnings explain qualifications such as an unstated shareholding date. A 422 response means the input needs correction; a 503 response means another scrape is running, so wait and retry.

Press **Ctrl+C** in the server terminal when you want to stop the API and its browser.

## 4. Send a request

Alternatively, open a second PowerShell terminal and run:

```powershell
$body = '{"company":"TCS","reporting_basis":"standalone"}'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/scrape' -Method Post -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 100
```

Both fields are required. `company` accepts a Finology company-page identifier such as `TCS`, or its full HTTPS company URL. `reporting_basis` must be `standalone` or `consolidated`.

Example for consolidated data:

```json
{
  "company": "https://ticker.finology.in/company/TCS?mode=C",
  "reporting_basis": "consolidated"
}
```

If a URL includes `mode=S` or `mode=C`, it must match `reporting_basis`. Company names, ISINs and bare BSE codes are not resolved. Identifiers without a normal stock symbol use the site's `SCRIP-...` page identifier.

Check that the server is running:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

Expected response: `{"status":"ok"}`. This checks the API process, not Finology availability.

## 5. JSON schema and sample responses

Read the [output schema guide](docs/output-schema.md). Machine-readable definitions are available for the [request](docs/schema/scrape-request.schema.json) and [response](docs/schema/scrape-response.schema.json). The running API exposes OpenAPI at `/openapi.json`.

Full sample outputs captured through the live HTTP API:

| Company and basis | Sample |
| --- | --- |
| TCS standalone | [JSON](examples/tcs-standalone.json) |
| Infosys standalone | [JSON](examples/infosys-standalone.json) |
| HCL Technologies standalone | [JSON](examples/hcltech-standalone.json) |
| HDFC Bank standalone, partial result | [JSON](examples/hdfcbank-standalone.json) |
| TCS consolidated | [JSON](examples/tcs-consolidated.json) |

One value object from the TCS quarterly-results sample is:

```json
{
  "column_index": 0,
  "period": "Sep 2025",
  "raw_text": "54,531",
  "numeric_value": 54531,
  "status": "present",
  "missing_reason": null
}
```

Its parent row identifies the metric as Net Sales. The table supplies its source URL, timestamp, reporting basis and unit. The complete samples show all five sections and their statuses.

## 6. Run the tests

Open PowerShell in the project folder, then run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

These tests run offline. They cover table parsing, period attribution, blank cells, missing versus zero, source units, invalid input, HTTP errors and browser lifecycle. Tests use synthetic cases and a saved public-source fixture; they do not call Finology during the test run.

## 7. Implementation approach

Python Playwright opens normal Google Chrome. BeautifulSoup reads the four financial tables from the company-page HTML. Shareholding percentages come from the page's actual `GetShares.ashx` JSON response and are checked against its rendered chart legend. Dated promoter-pledging and investor tables are also extracted.

The browser is reused on one worker thread, while each request has a fresh context that closes after scraping. Financial results are not cached. Labels and periods remain in source order. If table cells cannot be mapped safely, the affected section fails explicitly instead of guessing.

Code is separated into input validation (`input.py`), retrieval (`retrieval.py`), parsing (`parsing.py`), data models (`models.py`), orchestration (`service.py`) and the HTTP interface (`api.py`) inside `finology_scraper/`.

## 8. Limitations and error responses

Read the short [limitations and assumptions note](docs/limitations.md), including the undated shareholding chart and the absent cash-flow section in the HDFC Bank sample.

| HTTP status | Meaning |
| --- | --- |
| 200 | Complete result, possibly with explicit warnings |
| 207 | Partial extraction; inspect each section's status and errors |
| 404 | Company page not found |
| 422 | Invalid request, company input or conflicting reporting basis |
| 502 | Source blocked/unavailable, changed layout, or extraction failure |
| 503 | Another scrape is running; retry after it finishes |
| 504 | Source/browser operation timed out |

Malformed request bodies use FastAPI's `detail` error response. Valid requests that fail scraping include all five section keys with explicit statuses and explanations.
