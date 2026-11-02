CREATE TABLE IF NOT EXISTS screener_companies (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_key text NOT NULL UNIQUE,
    name text,
    nse_symbol text,
    bse_code text
);
CREATE TABLE IF NOT EXISTS screener_datasets (
    company_id bigint REFERENCES screener_companies(id),
    reporting_basis text CHECK (reporting_basis IN ('standalone','consolidated')),
    scraped_at timestamptz NOT NULL,
    source_url text NOT NULL,
    retrieval_method text NOT NULL,
    schema_version text NOT NULL,
    warnings jsonb NOT NULL,
    PRIMARY KEY (company_id, reporting_basis)
);
CREATE TABLE IF NOT EXISTS screener_tables (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL,
    reporting_basis text NOT NULL,
    section text NOT NULL,
    table_index integer NOT NULL,
    table_name text NOT NULL,
    stated_context text,
    FOREIGN KEY (company_id, reporting_basis) REFERENCES screener_datasets ON DELETE CASCADE,
    UNIQUE(company_id, reporting_basis, section, table_index)
);
CREATE TABLE IF NOT EXISTS screener_rows (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id bigint NOT NULL REFERENCES screener_tables ON DELETE CASCADE,
    row_index integer NOT NULL,
    metric text NOT NULL,
    parent_metric text,
    source_url text NOT NULL,
    UNIQUE(table_id, row_index)
);
CREATE TABLE IF NOT EXISTS screener_values (
    row_id bigint NOT NULL REFERENCES screener_rows ON DELETE CASCADE,
    column_index integer NOT NULL,
    period_label text NOT NULL,
    year integer,
    month integer CHECK (month BETWEEN 1 AND 12),
    duration_months integer,
    raw_text text NOT NULL,
    numeric_value numeric,
    unit text,
    value_status text NOT NULL CHECK (value_status IN ('present','missing','unparsed')),
    links jsonb NOT NULL,
    PRIMARY KEY(row_id, column_index)
);
CREATE OR REPLACE VIEW screener_financials AS
SELECT c.name AS company_name, c.nse_symbol, c.bse_code,
       d.reporting_basis, d.scraped_at, t.section, t.table_name,
       r.metric, r.parent_metric, v.period_label, v.year, v.month,
       v.duration_months, v.raw_text, v.numeric_value, v.unit, v.value_status,
       r.source_url, v.links
FROM screener_companies c
JOIN screener_datasets d ON d.company_id=c.id
JOIN screener_tables t ON t.company_id=d.company_id AND t.reporting_basis=d.reporting_basis
JOIN screener_rows r ON r.table_id=t.id
JOIN screener_values v ON v.row_id=r.id;
