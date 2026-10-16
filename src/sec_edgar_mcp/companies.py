"""Company lookup by name or ticker over SEC's company_tickers.json."""

from __future__ import annotations

import re
from typing import Any

from .client import TICKERS_URL, TTL_TICKERS, EdgarClient, EdgarError, normalize_cik
from .models import Company, CompanySearchResult

MAX_LIMIT = 100
_SUFFIXES = frozenset(
    {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "plc", "llc", "the"}
)
_NON_WORD = re.compile(r"[^a-z0-9]+")

TIER_TICKER, TIER_NAME, TIER_PREFIX, TIER_SUBSTRING, TIER_TOKENS = range(5)


def _tokens(text: str) -> list[str]:
    return [t for t in _NON_WORD.split(text.lower()) if t and t not in _SUFFIXES]


def _normalize_name(text: str) -> str:
    return " ".join(_tokens(text))


def _normalize_ticker(text: str) -> str:
    return text.strip().upper().replace(".", "-")


def _rank(query: str, name: str, ticker: str) -> tuple[int, int] | None:
    """Lower is better: (tier, -overlap). None means no match."""
    if _normalize_ticker(query) == _normalize_ticker(ticker):
        return TIER_TICKER, 0
    q_name, c_name = _normalize_name(query), _normalize_name(name)
    if not q_name:
        return None
    if q_name == c_name:
        return TIER_NAME, 0
    if c_name.startswith(q_name):
        return TIER_PREFIX, 0
    if q_name in c_name:
        return TIER_SUBSTRING, 0
    overlap = len(set(_tokens(query)) & set(_tokens(name)))
    if overlap:
        return TIER_TOKENS, -overlap
    return None


def search_company(client: EdgarClient, name: str, limit: int = 5) -> CompanySearchResult:
    """Case-insensitive search by ticker or name, ranked: exact ticker, exact name, name prefix,
    substring, then token overlap. Companies listed under several tickers appear once."""
    query = (name or "").strip()
    if not query:
        raise EdgarError("search query is empty: pass a company name or ticker")
    if not 1 <= limit <= MAX_LIMIT:
        raise EdgarError(f"limit must be between 1 and {MAX_LIMIT}")
    data: dict[str, Any] = client.get_json(TICKERS_URL, ttl=TTL_TICKERS)
    best: dict[str, tuple[tuple[int, int, int], Company]] = {}
    for position, row in enumerate(data.values()):
        title, ticker = str(row.get("title", "")), str(row.get("ticker", ""))
        rank = _rank(query, title, ticker)
        if rank is None:
            continue
        cik = normalize_cik(row["cik_str"])
        key = (*rank, position)
        if cik not in best or key < best[cik][0]:
            company = Company(cik=cik, name=title, ticker=ticker or None, source_url=TICKERS_URL)
            best[cik] = (key, company)
    ranked = sorted(best.values(), key=lambda item: item[0])
    return CompanySearchResult(
        query=query, matches=[company for _, company in ranked[:limit]], source_url=TICKERS_URL
    )
