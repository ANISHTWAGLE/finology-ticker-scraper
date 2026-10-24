"""The versioned HTTP request/response contract."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Basis(str, Enum):
    standalone = "standalone"
    consolidated = "consolidated"


class ScrapeRequest(Model):
    company: str = Field(min_length=1, max_length=2048,
                         description="Enter the Finology page identifier, such as TCS, or paste its full HTTPS company URL. Company-name search is not supported.",
                         examples=["TCS"])
    reporting_basis: Basis = Field(description=
        "Choose standalone for the company's own accounts, or consolidated for the company and its subsidiaries. This choice is required.",
        examples=["standalone", "consolidated"])


class Issue(Model):
    code: str
    message: str


class Context(Model):
    currency: str | None = None
    unit: str | None = None
    stated_text: str | None = None


class Value(Model):
    column_index: int
    period: str | None
    raw_text: str
    numeric_value: int | float | None
    status: Literal["present", "missing", "unparsed"]
    missing_reason: Literal["blank", "source_marker"] | None = None


class Row(Model):
    row_index: int
    metric_label: str
    context: Context
    values: list[Value]


class Table(Model):
    name: str
    source_format: Literal["html_table", "chart"] = "html_table"
    source_url: str
    scraped_at: datetime
    reporting_basis: Basis | None
    periods: list[str | None]
    context: Context
    notes: list[str] = Field(default_factory=list)
    rows: list[Row]


class Section(Model):
    name: str
    status: Literal["ok", "warning", "partial", "absent", "empty", "failed"]
    tables: list[Table] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)
    errors: list[Issue] = Field(default_factory=list)


class Sections(Model):
    shareholding_pattern: Section
    quarterly_results: Section
    profit_and_loss: Section
    balance_sheet: Section
    cash_flow: Section


class Company(Model):
    identifier: str
    name: str | None = None
    nse_symbol: str | None = None
    bse_code: str | None = None


class ScrapeResponse(Model):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "complete_with_warnings", "partial", "failed"]
    company: Company
    requested_reporting_basis: Basis
    source_url: str
    scraped_at: datetime
    sections: Sections
    warnings: list[Issue] = Field(default_factory=list)
    errors: list[Issue] = Field(default_factory=list)
