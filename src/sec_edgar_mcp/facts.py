"""Annual XBRL financial facts from the SEC companyfacts API.

Annual-value selection rule
---------------------------
1. Candidate facts: ``fp == "FY"`` and ``form`` in the annual family (10-K, 10-K/A, 20-F, 20-F/A,
   40-F, 40-F/A). Duration facts must also span 330-400 days, which drops the Q4-only and
   multi-year values a 10-K sometimes tags with ``fp == "FY"``. Instant (balance sheet) facts have
   no ``start`` and need no length check.
2. True fiscal year. companyfacts sets ``fy`` to the fiscal year *of the filing*, so the prior-year
   comparatives in a 10-K show up under a later ``fy`` (Apple's FY2024 10-K reports FY2023 and
   FY2022 net income with ``fy == 2024``). Each annual filing's own fiscal year end is derived
   once from the whole companyfacts payload: the latest ``period_end`` among that accession's
   annual duration facts (any tag), counted only if the filing was filed within 365 days of it
   (comparatives are filed 13+ months after their period ends, so a filing whose latest value is
   a comparative, or a late 10-K/A, never votes). That filing's ``fy`` labels that period end.
   A tag's own facts add votes only for period ends the company-wide map lacks and that fall
   after the filing's own year end (cover-page dates such as dei shares outstanding). Every
   fact is then assigned the fiscal year labelling its ``period_end``; a period that is never a
   filing's own year (e.g. older comparatives) gets the year of its ``period_end`` plus the
   company's usual offset between ``fy`` and that year. For both the offset and the fallback, a
   52/53-week year ending in January's first 7 days counts as the previous calendar year (a year
   ending 2023-01-01 is FY2022 for a December-year-end company), so the offset stays stable.
   Examples (from the filers' own ``fy``): NVIDIA year ended 2024-01-28 = FY2024, Apple
   2023-09-30 = FY2023, Microsoft 2024-06-30 = FY2024, AMD 2024-12-28 = FY2024.
3. One value per fiscal year: keep the latest ``period_end``, then the latest ``filed`` (so a later
   10-K or 10-K/A that restates a comparative wins).
4. Newest fiscal year first, truncated to ``years``.

Unit preference: USD, then USD/shares, then shares, then the first unit listed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from .client import (
    TTL_COMPANYFACTS,
    EdgarClient,
    EdgarError,
    companyfacts_url,
    filing_index_url,
    normalize_cik,
)
from .models import FactValue, FinancialFacts

ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:SalesRevenueNet",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "cost_of_revenue": ("us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold"),
    "gross_profit": ("us-gaap:GrossProfit",),
    "rd_expense": (
        "us-gaap:ResearchAndDevelopmentExpense",
        "us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ),
    "operating_income": ("us-gaap:OperatingIncomeLoss",),
    "net_income": ("us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"),
    "eps_basic": ("us-gaap:EarningsPerShareBasic",),
    "eps_diluted": ("us-gaap:EarningsPerShareDiluted",),
    "cash": ("us-gaap:CashAndCashEquivalentsAtCarryingValue", "us-gaap:Cash"),
    "total_assets": ("us-gaap:Assets",),
    "total_liabilities": ("us-gaap:Liabilities",),
    "stockholders_equity": (
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "long_term_debt": ("us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt"),
    "operating_cash_flow": ("us-gaap:NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",),
    "shares_outstanding": (
        "dei:EntityCommonStockSharesOutstanding",
        "us-gaap:CommonStockSharesOutstanding",
    ),
}

SYNONYMS: dict[str, str] = {
    "revenues": "revenue",
    "sales": "revenue",
    "net_sales": "revenue",
    "total_revenue": "revenue",
    "cogs": "cost_of_revenue",
    "cost_of_sales": "cost_of_revenue",
    "r_d": "rd_expense",
    "r_d_expense": "rd_expense",
    "research_and_development": "rd_expense",
    "operating_profit": "operating_income",
    "net_profit": "net_income",
    "earnings": "net_income",
    "eps": "eps_diluted",
    "cash_and_cash_equivalents": "cash",
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    "equity": "stockholders_equity",
    "shareholders_equity": "stockholders_equity",
    "capital_expenditures": "capex",
}

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
MIN_YEAR_DAYS, MAX_YEAR_DAYS = 330, 400
UNIT_PREFERENCE = ("USD", "USD/shares", "shares")
TAXONOMY_ORDER = ("us-gaap", "ifrs-full", "dei", "srt")
MAX_YEARS = 30
MAX_FILING_LAG_DAYS = 365
_ALIAS_KEY = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class _Fact:
    start: date | None
    end: date
    value: float
    accession: str
    fy: int
    form: str
    filed: date
    frame: str | None


@dataclass(frozen=True)
class _Resolved:
    taxonomy: str
    tag: str
    label: str
    unit: str
    values: list[tuple[int, _Fact]]


def alias_key(concept: str) -> str:
    return _ALIAS_KEY.sub("_", concept.strip().lower()).strip("_")


def _alias_for(concept: str) -> str | None:
    key = alias_key(concept)
    key = SYNONYMS.get(key, key)
    return key if key in ALIASES else None


def _alias_help() -> str:
    return ", ".join(sorted(ALIASES))


def _parse_fact(raw: dict[str, Any]) -> _Fact | None:
    try:
        if raw.get("fp") != "FY" or raw.get("form") not in ANNUAL_FORMS:
            return None
        start = date.fromisoformat(raw["start"]) if raw.get("start") else None
        end = date.fromisoformat(raw["end"])
        if start is not None and not MIN_YEAR_DAYS <= (end - start).days <= MAX_YEAR_DAYS:
            return None
        return _Fact(
            start=start,
            end=end,
            value=float(raw["val"]),
            accession=str(raw["accn"]),
            fy=int(raw["fy"]),
            form=str(raw["form"]),
            filed=date.fromisoformat(raw["filed"]),
            frame=raw.get("frame"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _end_year(end: date) -> int:
    """Calendar year a fiscal year end belongs to; 52/53-week years that spill into the first
    week of January (e.g. ending 2023-01-01) count as the previous year."""
    return end.year - 1 if end.month == 1 and end.day <= 7 else end.year


def _filed_promptly(fact: _Fact) -> bool:
    return (fact.filed - fact.end).days <= MAX_FILING_LAG_DAYS


def _own_year_ends(facts: list[_Fact]) -> dict[str, tuple[date, int]]:
    """accession -> (its own fiscal year end, fy): the latest period_end among its facts,
    counted only when filed within MAX_FILING_LAG_DAYS of that period's end."""
    latest: dict[str, _Fact] = {}
    for fact in facts:
        if fact.accession not in latest or fact.end > latest[fact.accession].end:
            latest[fact.accession] = fact
    return {acc: (f.end, f.fy) for acc, f in latest.items() if _filed_promptly(f)}


def filing_year_ends(payload_facts: dict[str, Any]) -> dict[str, tuple[date, int]]:
    """Each annual filing's own fiscal year end and fy, from the annual duration facts of every
    tag in a companyfacts ``facts`` payload."""
    durations = [
        fact
        for concepts in payload_facts.values()
        for concept in concepts.values()
        for values in concept.get("units", {}).values()
        for fact in (_parse_fact(raw) for raw in values)
        if fact is not None and fact.start is not None
    ]
    return _own_year_ends(durations)


def _fiscal_year_labels(
    facts: list[_Fact], filing_ends: dict[str, tuple[date, int]] | None = None
) -> dict[date, int]:
    votes: dict[date, Counter[int]] = defaultdict(Counter)
    for end, fy in (filing_ends or {}).values():
        votes[end][fy] += 1
    company_ends = set(votes)
    for accession, (end, fy) in _own_year_ends(facts).items():
        known = (filing_ends or {}).get(accession)
        if end not in company_ends and (known is None or end > known[0]):
            votes[end][fy] += 1
    labels = {
        end: max(counter, key=lambda fy: (counter[fy], -fy)) for end, counter in votes.items()
    }
    offsets = Counter(fy - _end_year(end) for end, fy in labels.items())
    offset = offsets.most_common(1)[0][0] if offsets else 0
    for fact in facts:
        labels.setdefault(fact.end, _end_year(fact.end) + offset)
    return labels


def select_annual(
    raw_values: list[dict[str, Any]],
    filing_ends: dict[str, tuple[date, int]] | None = None,
) -> list[tuple[int, _Fact]]:
    """Apply the module's annual-value rule; returns (fiscal_year, fact) newest first.

    ``filing_ends`` (from ``filing_year_ends``) labels years from the whole company's filings;
    without it, only this tag's own facts vote."""
    facts = [f for f in (_parse_fact(raw) for raw in raw_values) if f is not None]
    labels = _fiscal_year_labels(facts, filing_ends)
    best: dict[int, _Fact] = {}
    for fact in facts:
        year = labels[fact.end]
        current = best.get(year)
        if current is None or (fact.end, fact.filed, fact.accession) > (
            current.end,
            current.filed,
            current.accession,
        ):
            best[year] = fact
    frames = {f.end: f.frame for f in facts if f.frame}
    chosen = []
    for year in sorted(best, reverse=True):
        fact = best[year]
        if fact.frame is None and fact.end in frames:
            fact = replace(fact, frame=frames[fact.end])
        chosen.append((year, fact))
    return chosen


def _pick_unit(units: dict[str, list[dict[str, Any]]]) -> str | None:
    for unit in UNIT_PREFERENCE:
        if units.get(unit):
            return unit
    return next((unit for unit, values in units.items() if values), None)


def _resolve_tag(
    facts: dict[str, Any], taxonomy: str, tag: str, filing_ends: dict[str, tuple[date, int]]
) -> _Resolved | None:
    concept = facts.get(taxonomy, {}).get(tag)
    if not concept:
        return None
    units = concept.get("units", {})
    unit = _pick_unit(units)
    if unit is None:
        return None
    return _Resolved(
        taxonomy=taxonomy,
        tag=tag,
        label=concept.get("label") or tag,
        unit=unit,
        values=select_annual(units[unit], filing_ends),
    )


def _find_raw_tag(
    facts: dict[str, Any], concept: str, *, case_sensitive: bool
) -> tuple[str, str] | None:
    if ":" in concept:
        prefix, tag = (part.strip() for part in concept.split(":", 1))
        taxonomies = [t for t in facts if t.lower() == prefix.lower()]
    else:
        tag = concept
        taxonomies = [t for t in TAXONOMY_ORDER if t in facts]
        taxonomies += sorted(t for t in facts if t not in TAXONOMY_ORDER)
    for taxonomy in taxonomies:
        for name in facts[taxonomy]:
            if name == tag or (not case_sensitive and name.lower() == tag.lower()):
                return taxonomy, name
    return None


def _latest_end(resolved: _Resolved) -> date:
    return resolved.values[0][1].end if resolved.values else date.min


def _resolve(facts: dict[str, Any], concept: str, company: str) -> _Resolved:
    """Exact tag first, then alias, then case-insensitive tag ("Revenues" is the tag,
    "revenue" the alias)."""
    filing_ends = filing_year_ends(facts)
    text = concept.strip()
    prefixed = ":" in text
    alias = None if prefixed else _alias_for(text)
    raw = _find_raw_tag(facts, text, case_sensitive=True)
    if raw is None and (prefixed or alias is None):
        raw = _find_raw_tag(facts, text, case_sensitive=False)
    if raw is not None:
        resolved = _resolve_tag(facts, *raw, filing_ends)
        if resolved is None:
            raise EdgarError(f"{company} has no values for {raw[0]}:{raw[1]}")
        return resolved
    if alias is None:
        raise EdgarError(
            f"unknown concept {concept!r} for {company}. Pass an XBRL tag such as "
            f"'us-gaap:Revenues' or one of the aliases: {_alias_help()}"
        )
    candidates = []
    for ref in ALIASES[alias]:
        taxonomy, tag = ref.split(":", 1)
        resolved = _resolve_tag(facts, taxonomy, tag, filing_ends)
        if resolved is not None and resolved.values:
            candidates.append(resolved)
    if not candidates:
        raise EdgarError(
            f"{company} reports no annual values for alias {alias!r} "
            f"(tried {', '.join(ALIASES[alias])}). Try a specific XBRL tag."
        )
    return max(candidates, key=_latest_end)


def get_financial_facts(
    client: EdgarClient, cik: str, concept: str, years: int = 3
) -> FinancialFacts:
    """Annual values (fiscal_period FY, 10-K family) for the last ``years`` fiscal years,
    one per fiscal year, newest first. ``concept`` is an alias (see ``ALIASES``) or an XBRL tag
    with or without a taxonomy prefix ("Revenues", "us-gaap:NetIncomeLoss", "dei:...")."""
    if not concept or not concept.strip():
        raise EdgarError(f"concept is empty: pass an XBRL tag or one of: {_alias_help()}")
    if not 1 <= years <= MAX_YEARS:
        raise EdgarError(f"years must be between 1 and {MAX_YEARS}")
    padded = normalize_cik(cik)
    url = companyfacts_url(padded)
    try:
        data = client.get_json(url, ttl=TTL_COMPANYFACTS)
    except EdgarError as exc:
        if str(exc).startswith("not found"):
            raise EdgarError(f"no XBRL financial data for CIK {padded} ({url})") from exc
        raise
    company = data.get("entityName") or f"CIK {padded}"
    resolved = _resolve(data.get("facts", {}), concept, company)
    if not resolved.values:
        raise EdgarError(
            f"{company} has no annual (10-K/20-F/40-F, fiscal period FY) values for "
            f"{resolved.taxonomy}:{resolved.tag}"
        )
    values = [
        FactValue(
            fiscal_year=year,
            fiscal_period="FY",
            period_start=fact.start,
            period_end=fact.end,
            value=fact.value,
            unit=resolved.unit,
            form=fact.form,
            accession=fact.accession,
            filed=fact.filed,
            frame=fact.frame,
            filing_url=filing_index_url(padded, fact.accession),
        )
        for year, fact in resolved.values[:years]
    ]
    return FinancialFacts(
        cik=padded,
        company_name=company,
        requested_concept=concept,
        taxonomy=resolved.taxonomy,
        concept=resolved.tag,
        label=resolved.label,
        unit=resolved.unit,
        values=values,
        source_url=url,
    )
