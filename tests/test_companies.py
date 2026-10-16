from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from sec_edgar_mcp.client import TICKERS_URL, EdgarClient, EdgarError
from sec_edgar_mcp.companies import search_company
from tests.edgar_helpers import load_fixture, make_client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[EdgarClient]:
    with respx.mock:
        respx.get(TICKERS_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("company_tickers.json"))
        )
        yield make_client(tmp_path)


def test_exact_ticker_ranks_first(client: EdgarClient) -> None:
    result = search_company(client, "amd")
    assert result.matches[0].ticker == "AMD"
    assert result.matches[0].cik == "0000002488"
    assert result.matches[0].name == "ADVANCED MICRO DEVICES INC"
    assert result.source_url == TICKERS_URL
    assert result.matches[0].source_url == TICKERS_URL


def test_exact_name_ignores_corporate_suffix(client: EdgarClient) -> None:
    result = search_company(client, "NVIDIA")
    assert [c.ticker for c in result.matches] == ["NVDA"]


def test_ranking_exact_name_then_prefix_then_substring(client: EdgarClient) -> None:
    result = search_company(client, "apple", limit=10)
    assert [c.ticker for c in result.matches] == ["AAPL", "APLE", "AAPI", "PAPL"]


def test_substring_match(client: EdgarClient) -> None:
    result = search_company(client, "Materials")
    assert [c.ticker for c in result.matches] == ["AMAT"]


def test_token_overlap_match(client: EdgarClient) -> None:
    result = search_company(client, "devices micro")
    assert [c.ticker for c in result.matches] == ["AMD"]


def test_company_with_two_tickers_appears_once(client: EdgarClient) -> None:
    result = search_company(client, "alphabet")
    assert [(c.cik, c.ticker) for c in result.matches] == [("0001652044", "GOOGL")]
    assert search_company(client, "goog").matches[0].ticker == "GOOG"


def test_limit_and_no_matches(client: EdgarClient) -> None:
    assert len(search_company(client, "apple", limit=1).matches) == 1
    assert search_company(client, "zzzz nothing").matches == []


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_is_an_error(client: EdgarClient, query: str) -> None:
    with pytest.raises(EdgarError, match="empty"):
        search_company(client, query)


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_bounds(client: EdgarClient, limit: int) -> None:
    with pytest.raises(EdgarError, match="limit"):
        search_company(client, "apple", limit=limit)
