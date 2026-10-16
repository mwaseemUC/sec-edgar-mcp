from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from sec_edgar_mcp.client import EdgarClient, EdgarError
from sec_edgar_mcp.facts import ALIASES, filing_year_ends, get_financial_facts, select_annual
from tests.edgar_helpers import load_fixture, make_client

AAPL_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
NVDA_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json"
SYNTH_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000042.json"


@pytest.fixture
def mocked() -> Iterator[respx.MockRouter]:
    with respx.mock as router:
        router.get(AAPL_FACTS).mock(
            return_value=httpx.Response(200, json=load_fixture("companyfacts_CIK0000320193.json"))
        )
        router.get(NVDA_FACTS).mock(
            return_value=httpx.Response(200, json=load_fixture("companyfacts_CIK0001045810.json"))
        )
        yield router


@pytest.fixture
def client(tmp_path: Path, mocked: respx.MockRouter) -> EdgarClient:
    return make_client(tmp_path)


def fact(
    end: str,
    val: float,
    accn: str,
    fy: int,
    filed: str,
    *,
    start: str | None = None,
    fp: str = "FY",
    form: str = "10-K",
    frame: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp}
    raw.update(form=form, filed=filed)
    if start:
        raw["start"] = start
    if frame:
        raw["frame"] = frame
    return raw


def synthetic(units: dict[str, list[dict[str, Any]]], tag: str = "Revenues") -> dict[str, Any]:
    return {
        "cik": 42,
        "entityName": "Synthetic Corp",
        "facts": {"us-gaap": {tag: {"label": tag, "units": units}}},
    }


def years_and_values(result: Any) -> list[tuple[int, float]]:
    return [(v.fiscal_year, v.value) for v in result.values]


def test_revenue_alias_apple(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "revenue")
    assert result.cik == "0000320193"
    assert result.company_name == "Apple Inc."
    assert result.requested_concept == "revenue"
    assert (result.taxonomy, result.concept) == (
        "us-gaap",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    )
    assert result.unit == "USD"
    assert result.source_url == AAPL_FACTS
    assert years_and_values(result) == [
        (2025, 416161000000),
        (2024, 391035000000),
        (2023, 383285000000),
    ]
    fy23 = result.values[2]
    assert fy23.fiscal_period == "FY"
    assert (fy23.period_start, fy23.period_end) == (date(2022, 9, 25), date(2023, 9, 30))
    assert fy23.frame == "CY2023"
    assert fy23.form == "10-K"
    assert fy23.filing_url == (
        f"https://www.sec.gov/Archives/edgar/data/320193/"
        f"{fy23.accession.replace('-', '')}/{fy23.accession}-index.htm"
    )


def test_prior_year_comparatives_get_their_true_fiscal_year(client: EdgarClient) -> None:
    # Apple's FY2024 10-K lists FY2022 net income (99,803M) under fy=2024; FY2022's own 10-K is
    # not in the fixture, so 2022 must come from the period_end of the comparatives.
    result = get_financial_facts(client, "320193", "net_income", years=5)
    assert years_and_values(result) == [
        (2025, 112010000000),
        (2024, 93736000000),
        (2023, 96995000000),
        (2022, 99803000000),
        (2021, 94680000000),
    ]
    assert [v.period_end.year for v in result.values] == [2025, 2024, 2023, 2022, 2021]
    assert len({v.fiscal_year for v in result.values}) == 5


def test_instant_balance_sheet_fact(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "total_assets", years=2)
    assert result.concept == "Assets"
    assert years_and_values(result) == [(2025, 359241000000), (2024, 364980000000)]
    assert result.values[0].period_start is None
    assert result.values[1].frame == "CY2024Q3I"


def test_eps_uses_usd_per_share(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "eps_diluted", years=1)
    assert result.unit == "USD/shares"
    assert years_and_values(result) == [(2025, 7.46)]


def test_dei_alias_shares_outstanding(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "shares_outstanding", years=2)
    assert (result.taxonomy, result.concept, result.unit) == (
        "dei",
        "EntityCommonStockSharesOutstanding",
        "shares",
    )
    assert years_and_values(result) == [(2025, 14776353000), (2024, 15115823000)]


def test_alias_picks_tag_with_most_recent_annual_data(client: EdgarClient) -> None:
    result = get_financial_facts(client, "1045810", "Revenue")
    assert result.concept == "Revenues"
    assert years_and_values(result) == [
        (2026, 215938000000),
        (2025, 130497000000),
        (2024, 60922000000),
    ]
    assert result.values[2].period_end == date(2024, 1, 28)


@pytest.mark.parametrize(
    "concept", ["us-gaap:NetIncomeLoss", "NetIncomeLoss", "netincomeloss", "US-GAAP:netIncomeLoss"]
)
def test_raw_tags_with_or_without_prefix(client: EdgarClient, concept: str) -> None:
    result = get_financial_facts(client, "320193", concept, years=1)
    assert (result.taxonomy, result.concept) == ("us-gaap", "NetIncomeLoss")
    assert result.requested_concept == concept


def test_exact_tag_beats_alias(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "Revenues", years=1)
    assert result.concept == "Revenues"
    assert years_and_values(result) == [(2018, 265595000000)]


def test_dei_prefix(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "dei:EntityCommonStockSharesOutstanding")
    assert result.taxonomy == "dei"


@pytest.mark.parametrize("synonym", ["sales", "Net Sales", "revenues "])
def test_alias_synonyms(client: EdgarClient, synonym: str) -> None:
    assert get_financial_facts(client, "320193", synonym).concept.startswith("RevenueFrom")


def test_unknown_concept_lists_aliases(client: EdgarClient) -> None:
    with pytest.raises(EdgarError) as info:
        get_financial_facts(client, "320193", "MadeUpConcept")
    message = str(info.value)
    assert "unknown concept 'MadeUpConcept'" in message
    for alias in ALIASES:
        assert alias in message


def test_alias_the_company_does_not_report(client: EdgarClient) -> None:
    with pytest.raises(EdgarError, match="no annual values for alias 'gross_profit'"):
        get_financial_facts(client, "320193", "gross_profit")


@pytest.mark.parametrize(("years", "concept"), [(0, "revenue"), (31, "revenue"), (3, "  ")])
def test_argument_validation(client: EdgarClient, years: int, concept: str) -> None:
    with pytest.raises(EdgarError):
        get_financial_facts(client, "320193", concept, years=years)


def test_years_beyond_available_returns_what_exists(client: EdgarClient) -> None:
    result = get_financial_facts(client, "320193", "shares_outstanding", years=10)
    assert [v.fiscal_year for v in result.values] == [2025, 2024, 2023]


def test_company_without_xbrl(mocked: respx.MockRouter, tmp_path: Path) -> None:
    mocked.get(SYNTH_FACTS).mock(return_value=httpx.Response(404))
    with pytest.raises(EdgarError, match="no XBRL financial data for CIK 0000000042"):
        get_financial_facts(make_client(tmp_path), "42", "revenue")


def test_restatement_in_later_filing_wins(mocked: respx.MockRouter, tmp_path: Path) -> None:
    usd = [
        fact("2023-12-31", 100, "0000000042-24-000001", 2023, "2024-02-01", start="2023-01-01"),
        fact("2023-12-31", 105, "0000000042-25-000001", 2024, "2025-02-01", start="2023-01-01"),
        fact("2024-12-31", 120, "0000000042-25-000001", 2024, "2025-02-01", start="2024-01-01"),
        fact(
            "2024-12-31",
            118,
            "0000000042-25-000002",
            2024,
            "2025-06-01",
            start="2024-01-01",
            form="10-K/A",
        ),
    ]
    mocked.get(SYNTH_FACTS).mock(return_value=httpx.Response(200, json=synthetic({"USD": usd})))
    result = get_financial_facts(make_client(tmp_path), "42", "revenue")
    assert years_and_values(result) == [(2024, 118), (2023, 105)]
    assert [v.form for v in result.values] == ["10-K/A", "10-K"]


def test_unit_preference(mocked: respx.MockRouter, tmp_path: Path) -> None:
    units = {
        "EUR": [fact("2024-12-31", 1, "0000000042-25-000003", 2024, "2025-02-01")],
        "shares": [fact("2024-12-31", 2, "0000000042-25-000003", 2024, "2025-02-01")],
        "USD": [fact("2024-12-31", 3, "0000000042-25-000003", 2024, "2025-02-01")],
    }
    mocked.get(SYNTH_FACTS).mock(return_value=httpx.Response(200, json=synthetic(units)))
    result = get_financial_facts(make_client(tmp_path), "42", "us-gaap:Revenues")
    assert (result.unit, result.values[0].value) == ("USD", 3)


def test_first_listed_unit_when_no_preferred_unit(mocked: respx.MockRouter, tmp_path: Path) -> None:
    units = {"EUR": [fact("2024-12-31", 1, "0000000042-25-000003", 2024, "2025-02-01")], "JPY": []}
    mocked.get(SYNTH_FACTS).mock(return_value=httpx.Response(200, json=synthetic(units)))
    assert get_financial_facts(make_client(tmp_path), "42", "Revenues").unit == "EUR"


def test_tag_with_only_quarterly_values_errors(mocked: respx.MockRouter, tmp_path: Path) -> None:
    usd = [
        fact(
            "2024-03-31", 1, "0000000042-24-000009", 2024, "2024-05-01", start="2024-01-01", fp="Q1"
        )
    ]
    mocked.get(SYNTH_FACTS).mock(return_value=httpx.Response(200, json=synthetic({"USD": usd})))
    with pytest.raises(EdgarError, match="no annual"):
        get_financial_facts(make_client(tmp_path), "42", "us-gaap:Revenues")


def test_select_annual_filters_non_annual_facts() -> None:
    raw = [
        fact("2024-12-31", 400, "K", 2024, "2025-02-01", start="2024-01-01"),
        fact("2024-12-31", 110, "K", 2024, "2025-02-01", start="2024-10-01"),
        fact("2024-12-31", 999, "K", 2024, "2025-02-01", start="2022-01-01"),
        fact("2024-09-30", 300, "Q", 2024, "2024-11-01", start="2024-01-01", fp="Q3"),
        fact("2025-03-31", 100, "Q", 2025, "2025-05-01", start="2025-01-01", form="10-Q"),
        fact("2024-12-31", 401, "S", 2024, "2025-03-01", start="2024-01-01", form="8-K"),
        {"end": "bad", "val": 1, "accn": "X", "fy": 2024, "fp": "FY", "form": "10-K"},
    ]
    assert [(y, f.value) for y, f in select_annual(raw)] == [(2024, 400)]


def test_select_annual_accepts_foreign_annual_forms_and_53_week_years() -> None:
    raw = [
        fact("2024-12-31", 7, "F", 2024, "2025-04-01", start="2024-01-01", form="20-F"),
        fact("2023-12-31", 6, "F", 2024, "2025-04-01", start="2022-12-25", form="20-F"),
        fact("2022-12-24", 5, "G", 2022, "2023-04-01", start="2021-12-26", form="40-F"),
    ]
    assert [(y, f.value) for y, f in select_annual(raw)] == [(2024, 7), (2023, 6), (2022, 5)]


def test_select_annual_infers_offset_for_fiscal_years_named_after_start_year() -> None:
    # A retailer whose fiscal 2023 ends 2024-02-03 reports fy=2023 for that period.
    raw = [
        fact("2024-02-03", 30, "R24", 2023, "2024-03-10", start="2023-01-29"),
        fact("2023-01-28", 20, "R24", 2023, "2024-03-10", start="2022-01-30"),
        fact("2022-01-29", 10, "R24", 2023, "2024-03-10", start="2021-01-31"),
        fact("2025-02-01", 40, "R25", 2024, "2025-03-10", start="2024-02-04"),
    ]
    assert [(y, f.value) for y, f in select_annual(raw)] == [
        (2024, 40),
        (2023, 30),
        (2022, 20),
        (2021, 10),
    ]


def test_select_annual_early_january_year_end_belongs_to_previous_year() -> None:
    # 52/53-week December-year-end filer: only the latest 10-K is present, so the comparatives
    # ending 2023-01-01 and 2022-01-02 are labelled by the fallback, not by their own filing.
    raw = [
        fact("2023-12-30", 3, "W", 2023, "2024-02-20", start="2023-01-02"),
        fact("2023-01-01", 2, "W", 2023, "2024-02-20", start="2022-01-03"),
        fact("2022-01-02", 1, "W", 2023, "2024-02-20", start="2021-01-04"),
    ]
    assert [(y, f.value) for y, f in select_annual(raw)] == [(2023, 3), (2022, 2), (2021, 1)]


def test_select_annual_trusts_filer_fy_for_its_own_year() -> None:
    raw = [
        fact("2023-01-01", 2, "W22", 2022, "2023-02-20", start="2022-01-03"),
        fact("2024-01-28", 9, "N24", 2024, "2024-02-21", start="2023-01-30"),
    ]
    assert [(y, f.end.isoformat()) for y, f in select_annual(raw)] == [
        (2024, "2024-01-28"),
        (2022, "2023-01-01"),
    ]


def test_filing_year_ends_from_whole_payload() -> None:
    payload = load_fixture("companyfacts_CIK0000320193.json")["facts"]
    ends = filing_year_ends(payload)
    assert ends["0000320193-24-000123"] == (date(2024, 9, 28), 2024)
    assert ends["0000320193-23-000106"] == (date(2023, 9, 30), 2023)


def test_company_wide_year_ends_label_a_comparatives_only_tag() -> None:
    # In the FY2018 10-K this tag only carries FY2017; the filing's own year (2018-12-31) comes
    # from other tags, so 2017-12-31 is not mistaken for FY2018.
    filing_ends = {"0000000042-19-000001": (date(2018, 12, 31), 2018)}
    raw = [fact("2017-12-31", 100, "0000000042-19-000001", 2018, "2018-06-01", start="2017-01-01")]
    assert [y for y, _ in select_annual(raw, filing_ends)] == [2017]
    assert [y for y, _ in select_annual(raw)] == [2018]


def test_select_annual_fills_frame_from_duplicate_period() -> None:
    raw = [
        fact("2023-12-31", 5, "A", 2023, "2024-02-01", frame="CY2023"),
        fact("2023-12-31", 5, "B", 2024, "2025-02-01"),
        fact("2024-12-31", 6, "B", 2024, "2025-02-01"),
    ]
    chosen = dict(select_annual(raw))
    assert (chosen[2023].accession, chosen[2023].frame) == ("B", "CY2023")
    assert chosen[2024].frame is None
