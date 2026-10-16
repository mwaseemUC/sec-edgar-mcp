"""Recent filings for a company from the SEC submissions API."""

from __future__ import annotations

from datetime import date
from typing import Any

from .client import (
    TTL_SUBMISSIONS,
    EdgarClient,
    EdgarError,
    filing_document_url,
    filing_index_url,
    normalize_cik,
    submissions_url,
)
from .models import Filing, FilingList

MAX_LIMIT = 100


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _column(recent: dict[str, list[Any]], key: str, index: int) -> Any:
    values = recent.get(key) or []
    return values[index] if index < len(values) else None


def _filing(cik: str, recent: dict[str, list[Any]], index: int) -> Filing:
    accession = recent["accessionNumber"][index]
    document = _column(recent, "primaryDocument", index) or ""
    index_url = filing_index_url(cik, accession)
    return Filing(
        accession=accession,
        form=recent["form"][index],
        filing_date=date.fromisoformat(recent["filingDate"][index]),
        report_date=_parse_date(_column(recent, "reportDate", index)),
        primary_document=document,
        description=_column(recent, "primaryDocDescription", index) or None,
        document_url=filing_document_url(cik, accession, document) if document else index_url,
        index_url=index_url,
    )


def list_filings(
    client: EdgarClient, cik: str, form_type: str | None = None, limit: int = 10
) -> FilingList:
    """Newest-first filings from ``filings.recent`` (roughly the last 1,000 filings).

    ``form_type`` matches exactly, case-insensitively: "10-K" does not include "10-K/A".
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise EdgarError(f"limit must be between 1 and {MAX_LIMIT}")
    padded = normalize_cik(cik)
    wanted = form_type.strip().upper() if form_type and form_type.strip() else None
    url = submissions_url(padded)
    data = client.get_json(url, ttl=TTL_SUBMISSIONS)
    recent: dict[str, list[Any]] = data.get("filings", {}).get("recent", {})
    filings: list[Filing] = []
    for index, form in enumerate(recent.get("form", [])):
        if wanted is not None and str(form).upper() != wanted:
            continue
        filings.append(_filing(padded, recent, index))
        if len(filings) >= limit:
            break
    return FilingList(
        cik=padded,
        company_name=data.get("name", ""),
        form_type=wanted,
        filings=filings,
        source_url=url,
    )
