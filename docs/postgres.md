# Local PostgreSQL storage

Screener only. Complete scrapes automatically replace the latest saved dataset
for that company and reporting basis. There is no scrape history. Standalone and
consolidated data are separate. A partial scrape never changes saved data.
An older or repeated scrape timestamp cannot overwrite a newer dataset.

## Setup

From the project root with Docker Desktop running:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\setup-postgres.ps1
.\.venv\Scripts\python.exe -m uvicorn finology_scraper.api:app
```

The setup script generates a local password once, starts PostgreSQL 17 and
initializes the schema. Connection: `127.0.0.1:55432`, database `screener`, user
`screener`. The password is in `.secrets/postgres_password`, which Git ignores.
The Python API automatically reads this local file. For a different database,
set `SCREENER_DATABASE_URL` in the API process environment instead.

The Docker volume retains data across container restarts. Use `docker compose
stop` to stop it and `docker compose up -d --wait` to start it again. Removing
the volume deletes the saved data. Keep the password file when reusing the volume.
The database port is bound to localhost; this is a local development setup.

## API result

`POST /screener/scrape` returns the existing data plus `persistence`:

- `saved`: transaction committed.
- `skipped_partial`: scrape incomplete; saved data left unchanged.
- `skipped_older`: same or newer data is already stored.
- `not_configured`: neither local password file nor database URL is configured.
- `failed`: database write failed; API returns HTTP 503 with the scraped data.

Database writes use one transaction, so insertion errors roll back replacement.
The API does not claim that data was saved when PostgreSQL is unavailable.
Finology's `/scrape` endpoint remains separate and does not save to PostgreSQL.

## SQL queries

Open PostgreSQL's terminal:

```powershell
docker compose exec postgres psql -U screener -d screener
```

MRF quarterly sales:

```sql
SELECT year, month, period_label, numeric_value, unit
FROM screener_financials
WHERE nse_symbol = 'MRF'
  AND reporting_basis = 'consolidated'
  AND section = 'quarterly_results'
  AND metric = 'Sales +'
  AND parent_metric IS NULL
ORDER BY year, month;
```

Check freshness and stored coverage:

```sql
SELECT nse_symbol, reporting_basis, section, table_name,
       max(scraped_at) AS scraped_at, count(*) AS stored_cells
FROM screener_financials
GROUP BY nse_symbol, reporting_basis, section, table_name
ORDER BY nse_symbol, reporting_basis, section, table_name;
```

`screener_companies` identifies companies, preferably by BSE code. A dataset is
unique by company and reporting basis. `screener_tables`, `screener_rows` and
`screener_values` store the structured cells. `screener_financials` joins them
for ordinary queries. Row/column positions preserve duplicate metric names.
Parent labels distinguish expanded rows. The exact period label, duration,
raw text, numeric value, units, missing status, source links and timestamp are
retained. `TTM` is not assigned an invented year/month. Numeric storage uses
PostgreSQL `numeric`; the scraper's parsed values retain their existing precision.

## Verification

```powershell
$env:SCREENER_TEST_POSTGRES = '1'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:SCREENER_TEST_POSTGRES
```

Database tests create and remove uniquely named test schemas. They do not modify
saved company data. Without this flag, real database tests are skipped.
The schema is initialized explicitly, not recreated on every API request.

Verified locally: 130 tests passed with real PostgreSQL tests enabled. A fresh
MRF consolidated scrape through FastAPI TestClient used the live upstream source,
returned HTTP 200 with `persistence: saved`, and stored 4,944 cells. A SQL query
returned quarterly sales of 8,416 INR crore for Jun 2026. This verifies the local
API-to-database path, not a remotely deployed HTTP server.

Implementation references: [official PostgreSQL Docker image](https://hub.docker.com/_/postgres)
and [Psycopg transaction documentation](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).
