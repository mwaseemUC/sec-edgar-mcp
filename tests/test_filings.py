from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from sec_edgar_mcp.client import EdgarClient, EdgarError
from sec_edgar_mcp.filings import list_filings
from tests.edgar_helpers import load_fixture, make_client

SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000320193.json"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[EdgarClient]:
    with respx.mock:
        respx.get(SUBMISSIONS).mock(
            return_value=httpx.Response(200, json=load_fixture("submissions_CIK0000320193.json"))
        )
        yield make_client(tmp_path)


def test_annual_reports_newest_first_with_urls(client: EdgarClient) -> None:
    result = list_filings(client, "320193", "10-K", limit=3)
    assert result.cik == "0000320193"
    assert result.company_name == "Apple Inc."
    assert result.form_type == "10-K"
    assert result.source_url == SUBMISSIONS
    assert [f.accession for f in result.filings] == [
        "0000320193-25-000079",
        "0000320193-24-000123",
        "0000320193-23-000106",
    ]
    fy23 = result.filings[2]
    assert fy23.filing_date == date(2023, 11, 3)
    assert fy23.report_date == date(2023, 9, 30)
    assert fy23.primary_document == "aapl-20230930.htm"
    assert fy23.description == "10-K"
    assert fy23.document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    )
    assert fy23.index_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/"
        "0000320193-23-000106-index.htm"
    )


def test_form_filter_is_case_insensitive_and_exact(client: EdgarClient) -> None:
    result = list_filings(client, "0000320193", "10-q", limit=100)
    assert result.form_type == "10-Q"
    assert result.filings and {f.form for f in result.filings} == {"10-Q"}
    assert list_filings(client, "320193", "10-K/A").filings == []


def test_no_filter_returns_all_forms_in_order(client: EdgarClient) -> None:
    result = list_filings(client, "320193", limit=4)
    assert result.form_type is None
    assert len(result.filings) == 4
    dates = [f.filing_date for f in result.filings]
    assert dates == sorted(dates, reverse=True)
    form4 = result.filings[0]
    assert form4.form == "4"
    assert form4.document_url.endswith("/xslF345X06/form4.xml")


def test_blank_form_type_means_no_filter(client: EdgarClient) -> None:
    assert list_filings(client, "320193", "  ", limit=2).form_type is None


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_bounds(client: EdgarClient, limit: int) -> None:
    with pytest.raises(EdgarError, match="between 1 and 100"):
        list_filings(client, "320193", limit=limit)


def test_bad_cik(client: EdgarClient) -> None:
    with pytest.raises(EdgarError, match="invalid CIK"):
        list_filings(client, "AAPL")


@respx.mock
def test_unknown_cik_is_not_found(tmp_path: Path) -> None:
    url = "https://data.sec.gov/submissions/CIK0000000042.json"
    respx.get(url).mock(return_value=httpx.Response(404))
    with pytest.raises(EdgarError, match="not found"):
        list_filings(make_client(tmp_path), "42")
