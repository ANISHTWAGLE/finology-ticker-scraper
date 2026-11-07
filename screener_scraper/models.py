"""Screener request and response contract, independent of Finology tables."""

from typing import Literal
from datetime import datetime
from pydantic import Field, model_validator
from finology_scraper.models import Model, Basis, Company, Issue


class ScreenerRequest(Model):
    nse_symbol: str | None = Field(default=None, pattern=r"^[A-Za-z0-9&_.-]{1,40}$")
    bse_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    reporting_basis: Basis

    @model_validator(mode="after")
    def require_identifier(self):
        if not self.nse_symbol and not self.bse_code:
            raise ValueError("Supply nse_symbol or bse_code.")
        if self.nse_symbol:
            self.nse_symbol = self.nse_symbol.upper()
        return self


class Period(Model):
    label: str
    year: int | None = None
    month: int | None = None
    duration_months: int | None = None


class Cell(Model):
    raw_text: str
    numeric_value: float | None = None
    unit: str | None = None
    status: Literal["present", "missing", "unparsed"]
    links: list[str] = Field(default_factory=list)


class Row(Model):
    label: str
    parent: str | None = None
    source_url: str
    values: list[Cell]


class Table(Model):
    name: str
    periods: list[Period]
    stated_context: str | None = None
    rows: list[Row]


class Section(Model):
    status: Literal["ok", "partial", "absent", "failed"]
    tables: list[Table] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ScreenerResponse(Model):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "partial", "failed"]
    company: Company
    requested_reporting_basis: Basis
    source_url: str
    scraped_at: datetime
    retrieval_method: Literal["http", "browser"]
    sections: dict[str, Section] = Field(default_factory=dict)
    warnings: list[Issue] = Field(default_factory=list)
    persistence: Literal['not_configured', 'saved', 'skipped_partial', 'skipped_older', 'failed'] = 'not_configured'


JobState = Literal['queued', 'running', 'complete', 'partial', 'failed', 'interrupted']


class BatchRequest(Model):
    engine: Literal['http', 'lightpanda'] = Field(description=
        'Required comparison mode. HTTP has no browser fallback; Lightpanda handles every source request.')
    concurrency: int = Field(default=2, ge=1, le=4, strict=True,
                             description='Company/basis workers sharing two-second source request spacing.')
    constituent_csv: str | None = Field(default=None, min_length=1, max_length=200000,
        description='Optional official CSV text, used only if both downloads fail. Exactly 50 unique companies required.')


class SectionSummary(Model):
    status: Literal['ok', 'partial', 'absent', 'failed']
    tables: int
    rows: int
    issues: list[Issue]


class BatchItem(Model):
    nse_symbol: str
    reporting_basis: Basis
    status: JobState
    persistence: Literal['saved', 'skipped_partial', 'skipped_older', 'failed', 'not_configured'] | None = None
    sections: dict[str, SectionSummary] = Field(default_factory=dict)
    error: dict | None = None
    source_url: str | None = None
    scraped_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ConstituentCompany(Model):
    nse_symbol: str
    name: str
    isin: str


class ConstituentSnapshot(Model):
    source_url: str
    fetched_at: datetime
    sha256: str
    companies: list[ConstituentCompany]
    warning: str | None = None


class BatchJob(Model):
    id: str
    state: JobState
    engine: Literal['http', 'lightpanda']
    concurrency: int
    attempt: int
    total: int
    counts: dict[JobState, int]
    snapshot: ConstituentSnapshot | None
    items: list[BatchItem]
    message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
