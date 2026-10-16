"""Response models shared by the domain functions and the MCP tools.

Every model carries ``source_url``: the exact SEC URL the data came from, so answers can cite it.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Company(BaseModel):
    cik: str = Field(description="10-digit zero-padded SEC Central Index Key")
    name: str
    ticker: str | None
    source_url: str


class CompanySearchResult(BaseModel):
    query: str
    matches: list[Company]
    source_url: str


class Filing(BaseModel):
    accession: str = Field(description='Accession number, e.g. "0000320193-23-000106"')
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    description: str | None
    document_url: str = Field(description="Primary document on www.sec.gov/Archives")
    index_url: str = Field(description="Filing index page")


class FilingList(BaseModel):
    cik: str
    company_name: str
    form_type: str | None
    filings: list[Filing]
    source_url: str


class FactValue(BaseModel):
    fiscal_year: int
    fiscal_period: str
    period_start: date | None = Field(description="None for point-in-time (balance sheet) facts")
    period_end: date
    value: float
    unit: str
    form: str
    accession: str
    filed: date
    frame: str | None
    filing_url: str = Field(description="Index URL of the filing this value came from")


class FinancialFacts(BaseModel):
    cik: str
    company_name: str
    requested_concept: str
    taxonomy: str = Field(description='Resolved XBRL taxonomy, e.g. "us-gaap"')
    concept: str = Field(description='Resolved XBRL tag, e.g. "Revenues"')
    label: str
    unit: str
    values: list[FactValue] = Field(description="Annual values, newest fiscal year first")
    source_url: str


class FilingSection(BaseModel):
    accession: str
    cik: str
    form: str
    section: str = Field(description='Normalized item number, e.g. "1A", "7"')
    title: str
    text: str
    char_count: int
    truncated: bool
    source_url: str
