"""Regression tests added by the QA review (limiter, cache, facts, sections, scoring, MCP)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client

from evals import scoring
from sec_edgar_mcp.client import EdgarError
from sec_edgar_mcp.facts import select_annual
from sec_edgar_mcp.ratelimit import TokenBucket
from sec_edgar_mcp.sections import extract_section, normalize_section
from sec_edgar_mcp.server import build_server
from tests.edgar_helpers import FakeTime, make_client

# ---------------------------------------------------------------- rate limiter


def test_limiter_bounds_rate_across_threads() -> None:
    rate, threads, per_thread = 200.0, 8, 5
    limiter = TokenBucket(rate, capacity=1)
    stamps: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(per_thread):
            limiter.acquire()
            with lock:
                stamps.append(time.monotonic())

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    stamps.sort()
    total = threads * per_thread
    assert len(stamps) == total
    assert stamps[-1] - stamps[0] >= (total - 2) / rate


def _max_in_window(stamps: list[float], window: float) -> int:
    best = 0
    for i, start in enumerate(stamps):
        best = max(best, sum(1 for s in stamps[i:] if s - start < window))
    return best


def test_default_limiter_never_exceeds_ten_requests_in_any_second() -> None:
    fake = FakeTime()
    limiter = TokenBucket(8, clock=fake.clock, sleep=fake.sleep)
    stamps = []
    for _ in range(30):
        limiter.acquire()
        stamps.append(fake.now)
    assert _max_in_window(stamps, 1.0) <= 10


# ---------------------------------------------------------------- cache poisoning


@respx.mock
def test_invalid_json_body_is_not_cached(tmp_path: Path) -> None:
    url = "https://data.sec.gov/submissions/CIK0000000042.json"
    route = respx.get(url).mock(
        side_effect=[httpx.Response(200, text="<html>busy</html>"), httpx.Response(200, json={})]
    )
    client = make_client(tmp_path)
    with pytest.raises(EdgarError, match="invalid JSON"):
        client.get_json(url, ttl=3600)
    assert client.get_json(url, ttl=3600) == {}
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 500, 429])
def test_error_responses_are_never_cached(tmp_path: Path, status: int) -> None:
    url = "https://www.sec.gov/Archives/edgar/data/42/000000004224000001/doc.htm"
    respx.get(url).mock(return_value=httpx.Response(status, text="error page"))
    client = make_client(tmp_path)
    with pytest.raises(EdgarError):
        client.get_text(url, ttl=None)
    assert not list(tmp_path.glob("*.body"))


# ---------------------------------------------------------------- XBRL fiscal years


def _fact(end: str, start: str, val: float, accn: str, fy: int, filed: str, form: str = "10-K"):
    return {
        "start": start,
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": "FY",
        "form": form,
        "filed": filed,
    }


def test_tag_present_only_as_comparatives_keeps_true_fiscal_years() -> None:
    # e.g. a pre-ASC 606 revenue tag kept only for prior years in the FY2018 10-K.
    raw = [
        _fact("2017-12-31", "2017-01-01", 100, "A18", 2018, "2019-02-15"),
        _fact("2016-12-31", "2016-01-01", 90, "A18", 2018, "2019-02-15"),
    ]
    assert [(y, f.value) for y, f in select_annual(raw)] == [(2017, 100), (2016, 90)]


def test_later_filings_cannot_outvote_the_owning_10k() -> None:
    raw = [
        _fact("2017-12-31", "2017-01-01", 100, "A17", 2017, "2018-02-15"),
        _fact("2017-12-31", "2017-01-01", 100, "A18", 2018, "2019-02-15"),
        _fact("2017-12-31", "2017-01-01", 100, "A18A", 2018, "2019-04-30", form="10-K/A"),
    ]
    assert [y for y, _ in select_annual(raw)] == [2017]


def test_comparatives_one_to_one_tie_resolves_to_owning_year() -> None:
    raw = [
        _fact("2017-12-31", "2017-01-01", 100, "A17", 2017, "2018-02-15"),
        _fact("2017-12-31", "2017-01-01", 100, "A18", 2018, "2019-02-15"),
        _fact("2017-12-31", "2017-01-01", 100, "A19", 2019, "2020-02-15"),
    ]
    assert [y for y, _ in select_annual(raw)] == [2017]


# ---------------------------------------------------------------- section end boundaries


def _lines(*lines: str) -> str:
    return "\n".join(lines)


def test_running_part_header_does_not_hide_the_next_part_boundary() -> None:
    text = _lines(
        "PART I",
        "Item 4. Mine Safety Disclosures",
        "Not applicable.",
        "PART I",
        "More Part I text on a new page.",
        "PART II",
        "Information about our executive officers follows.",
        "Item 5. Market for Registrant's Common Equity",
        "Our stock trades on Nasdaq.",
    )
    found = extract_section(text, normalize_section("4"))
    assert found is not None
    assert "More Part I text on a new page." in found.text
    assert "PART II" not in found.text
    assert "executive officers" not in found.text


def test_10q_part_one_item_four_still_ends_at_part_two() -> None:
    text = _lines(
        "PART I — FINANCIAL INFORMATION",
        "Item 4. Controls and Procedures",
        "Disclosure controls were effective.",
        "PART I",
        "Changes in internal control: none.",
        "PART II — OTHER INFORMATION",
        "Legal matters are described in Note 9.",
        "Item 1. Legal Proceedings",
        "See Note 9.",
    )
    found = extract_section(text, normalize_section("Item 4", "10-Q"))
    assert found is not None
    assert found.text.endswith("Changes in internal control: none.")


def test_section_without_known_part_still_ends_at_part_line() -> None:
    text = _lines(
        "Item 1A. Risk Factors",
        "Risks follow.",
        "PART II",
        "Unlabelled Part II preamble.",
        "Item 5. Market",
        "Stock.",
    )
    found = extract_section(text, normalize_section("1A"))
    assert found is not None
    assert found.text.endswith("Risks follow.")


def test_combined_part_item_heading_ends_at_next_part() -> None:
    text = _lines(
        "PART II, ITEM 9B. OTHER INFORMATION",
        "None.",
        "PART II",
        "PART III",
        "Part III information is incorporated by reference.",
        "Item 10. Directors",
        "See proxy.",
    )
    found = extract_section(text, normalize_section("9B"))
    assert found is not None
    assert "incorporated by reference" not in found.text


# ---------------------------------------------------------------- eval citation check


TOOL_URLS = {
    "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/0000320193-23-000106-index.htm",
}


@pytest.mark.parametrize(
    "answer",
    [
        "See https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
        "See https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=320193",
        "See https://data.sec.gov.evil.example/api/xbrl/companyfacts/CIK0000320193.json",
        "See https://data.sec.gov@evil.example/api/xbrl/companyfacts/CIK0000320193.json",
        "See https://evilsec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "See data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    ],
)
def test_citation_rejects_urls_not_returned_by_tools(answer: str) -> None:
    assert not scoring.is_cited(answer, TOOL_URLS)


def test_citation_accepts_tool_url_in_markdown_link() -> None:
    answer = "[10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/0000320193-23-000106-index.htm)."
    assert scoring.is_cited(answer, TOOL_URLS)


def test_citation_ignores_urls_inside_filing_text() -> None:
    output = {"text": "visit https://www.sec.gov/fake-page", "source_url": "https://www.sec.gov/a"}
    assert scoring.collect_source_urls(output) == {"https://www.sec.gov/a"}


# ---------------------------------------------------------------- MCP error surface


def test_unexpected_exception_is_a_tool_error_without_internals() -> None:
    def factory() -> Any:
        raise RuntimeError("secret-internal-detail")

    async def run() -> Any:
        async with Client(build_server(factory)) as client:
            return await client.call_tool("search_company", {"name": "apple"})

    result = asyncio.run(run())
    assert result.is_error
    assert "secret-internal-detail" not in result.content[0].text
