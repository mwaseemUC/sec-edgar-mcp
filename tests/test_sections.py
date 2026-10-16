from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sec_edgar_mcp.client import EdgarError
from sec_edgar_mcp.sections import (
    extract_section,
    find_primary_document,
    get_filing_section,
    html_to_text,
    normalize_section,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sections"
ACC = "0000320193-25-000079"
ACC_DIGITS = "000032019325000079"
FOLDER = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000320193.json"
DOC_URL = f"{FOLDER}/aapl-20250927.htm"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """Serves canned responses by URL; any other URL behaves like a 404."""

    def __init__(self, json_map: dict[str, Any], text_map: dict[str, str]) -> None:
        self.json_map = json_map
        self.text_map = text_map
        self.calls: list[tuple[str, float | None]] = []

    def get_json(self, url: str, *, ttl: float | None) -> Any:
        self.calls.append((url, ttl))
        if url not in self.json_map:
            raise EdgarError(f"not found: {url}")
        return self.json_map[url]

    def get_text(self, url: str, *, ttl: float | None) -> str:
        self.calls.append((url, ttl))
        if url not in self.text_map:
            raise EdgarError(f"not found: {url}")
        return self.text_map[url]


def submissions(accession: str = ACC, form: str = "10-K", doc: str = "aapl-20250927.htm") -> dict:
    return {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000020", accession],
                "form": ["10-Q", form],
                "primaryDocument": ["aapl-20260627.htm", doc],
            }
        },
    }


def tenk_client(html: str | None = None, form: str = "10-K") -> FakeClient:
    return FakeClient(
        {SUBMISSIONS: submissions(form=form)},
        {DOC_URL: html if html is not None else fixture("tenk_synthetic.htm")},
    )


# ---------------------------------------------------------------- section names


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1A", "1A"),
        ("1a", "1A"),
        ("Item 1A", "1A"),
        ("item 1a.", "1A"),
        ("ITEM\xa01A", "1A"),
        ("risk factors", "1A"),
        ("Risk Factors", "1A"),
        ("7", "7"),
        ("MD&A", "7"),
        ("md&a", "7"),
        ("MDA", "7"),
        ("management's discussion", "7"),
        ("Management’s Discussion and Analysis", "7"),
        ("7A", "7A"),
        ("market risk", "7A"),
        ("Quantitative and Qualitative Disclosures About Market Risk", "7A"),
        ("8", "8"),
        ("financial statements", "8"),
        ("1", "1"),
        ("business", "1"),
        ("2", "2"),
        ("properties", "2"),
        ("3", "3"),
        ("legal proceedings", "3"),
        ("Item 1C", "1C"),
        ("cybersecurity", "1C"),
        ("Part II Item 7", "7"),
    ],
)
def test_normalize_10k_sections(raw: str, expected: str) -> None:
    target = normalize_section(raw, "10-K")
    assert target.key == expected
    assert target.part is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MD&A", "I-2"),
        ("7", "I-2"),
        ("Item 2", "I-2"),
        ("Part I, Item 2", "I-2"),
        ("part ii item 2", "II-2"),
        ("risk factors", "II-1A"),
        ("1A", "II-1A"),
        ("market risk", "I-3"),
        ("financial statements", "I-1"),
        ("legal proceedings", "II-1"),
        ("Part II Item 1", "II-1"),
        ("6", "II-6"),
    ],
)
def test_normalize_10q_sections(raw: str, expected: str) -> None:
    assert normalize_section(raw, "10-Q").key == expected


@pytest.mark.parametrize("raw", ["", "Item 99", "1D", "the weather", "Part I Item 1A"])
def test_unknown_section_lists_accepted_names(raw: str) -> None:
    form = "10-Q" if raw.startswith("Part I ") else "10-K"
    with pytest.raises(EdgarError, match="Accepted:") as info:
        normalize_section(raw, form)
    assert "risk factors" in str(info.value)
    assert "MD&A" in str(info.value)


def test_10q_rejects_10k_only_sections() -> None:
    with pytest.raises(EdgarError, match="Unknown section 'properties' for a 10-Q"):
        normalize_section("properties", "10-Q")


# ---------------------------------------------------------------- HTML to text


def test_html_to_text_drops_hidden_script_and_style() -> None:
    text = html_to_text(fixture("tenk_synthetic.htm"))
    assert "must vanish" not in text
    assert "dei:" not in text
    assert "\xa0" not in text
    assert text.startswith("UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K")


def test_html_to_text_collapses_whitespace_and_joins_table_cells() -> None:
    text = html_to_text(fixture("tenk_synthetic.htm"))
    lines = text.split("\n")
    assert "Item 1. Business" in lines
    assert "Competition from low-cost producers could reduce margins." in lines
    assert "Net sales | $ | 1,234 | $ | 1,100" in lines
    assert "Item 1A. | Risk Factors | 5" in lines


def test_html_to_text_handles_br_and_nested_inline_tags() -> None:
    html = "<p>Line <b>one</b><br/>Line&nbsp;<i>two</i></p><div><div>Three</div></div>"
    assert html_to_text(html) == "Line one\nLine two\nThree"


# ---------------------------------------------------------------- section finding


def tenk_text() -> str:
    return html_to_text(fixture("tenk_synthetic.htm"))


def test_skips_toc_entry_and_ends_at_next_item() -> None:
    found = extract_section(tenk_text(), normalize_section("risk factors"))
    assert found is not None
    assert found.title == "ITEM 1A: RISK FACTORS"
    lines = found.text.split("\n")
    assert lines[0] == "ITEM 1A: RISK FACTORS"
    assert lines[-1] == "Competition from low-cost producers could reduce margins."
    assert "Item 7 of this report describes how the Company manages liquidity." in lines
    assert "Unresolved Staff Comments" not in found.text


def test_em_dash_and_nbsp_heading() -> None:
    found = extract_section(tenk_text(), normalize_section("Item 1B"))
    assert found is not None
    assert found.title == "Item 1B — Unresolved Staff Comments"
    assert found.text.split("\n")[1:] == ["None."]


def test_title_continues_on_next_line_when_heading_is_split() -> None:
    found = extract_section(tenk_text(), normalize_section("properties"))
    assert found is not None
    assert found.title == "Item 2. Properties"
    assert found.text.endswith("headquarters is located in Springfield, Illinois.")


def test_section_ends_at_part_heading() -> None:
    found = extract_section(tenk_text(), normalize_section("3"))
    assert found is not None
    assert found.text.endswith("not party to any material pending legal proceedings.")
    assert "PART II" not in found.text


def test_running_page_headers_do_not_end_the_section() -> None:
    # Microsoft's 10-K repeats "PART I" / "Item 1" at the top of every page.
    text = "\n".join(
        [
            "PART I",
            "Item 1. Business",
            "We build software.",
            "7",
            "PART I",
            "Item 1",
            "As of June 30, 2024, we employed approximately 228,000 people.",
            "Item 1A. Risk Factors",
            "Risks follow.",
        ]
    )
    found = extract_section(text, normalize_section("1"))
    assert found is not None
    assert "228,000 people" in found.text
    assert "Risks follow." not in found.text


def test_reserved_item_followed_by_page_number_is_still_found() -> None:
    found = extract_section(tenk_text(), normalize_section("6"))
    assert found is not None
    assert found.title == "Item 6. [Reserved]"


def test_mdna_keeps_table_rows() -> None:
    found = extract_section(tenk_text(), normalize_section("MD&A"))
    assert found is not None
    assert "Gross margin | $ | 456 | $ | 400" in found.text
    assert found.text.endswith("Liquidity remained strong throughout the year.")


def test_last_section_runs_to_end_of_document() -> None:
    found = extract_section(tenk_text(), normalize_section("8"))
    assert found is not None
    assert found.text.endswith("The consolidated financial statements follow.")


def test_section_only_in_toc_is_not_found() -> None:
    text = "\n".join(
        ["Item 1A. | Risk Factors | 5", "Item 2. | Properties | 9", "Item 2. Properties"]
    )
    assert extract_section(text, normalize_section("1A")) is None


def test_10q_part_aware_sections() -> None:
    text = html_to_text(fixture("tenq_synthetic.htm"))
    mdna = extract_section(text, normalize_section("MD&A", "10-Q"))
    sales = extract_section(text, normalize_section("Part II Item 2", "10-Q"))
    risks = extract_section(text, normalize_section("risk factors", "10-Q"))
    controls = extract_section(text, normalize_section("Item 4", "10-Q"))
    assert mdna is not None and sales is not None and risks is not None and controls is not None
    assert mdna.text.split("\n")[1:] == ["Quarterly net sales increased 8% year over year."]
    assert sales.title.startswith("Item 2. Unregistered Sales")
    assert risks.text.endswith("no material changes to the risk factors in the 2023 Form 10-K.")
    assert controls.text.endswith("Disclosure controls were effective.")


def test_real_apple_10k_excerpt() -> None:
    text = html_to_text(fixture("aapl_10k_2025_excerpt.htm"))
    assert "0000320193" not in text
    staff = extract_section(text, normalize_section("1B"))
    cyber = extract_section(text, normalize_section("cybersecurity"))
    props = extract_section(text, normalize_section("properties"))
    assert staff is not None and cyber is not None and props is not None
    assert staff.text == "Item 1B. Unresolved Staff Comments\nNone."
    assert cyber.title == "Item 1C. Cybersecurity"
    assert "Item 2. Properties" not in cyber.text
    assert props.text.split("\n")[1].startswith("The Company’s headquarters is located in")
    assert props.text.endswith("Apple Inc. | 2025 Form 10-K | 17")
    assert extract_section(text, normalize_section("risk factors")) is None


# ---------------------------------------------------------------- end to end with a fake client


def test_get_filing_section_via_submissions() -> None:
    client = tenk_client()
    result = get_filing_section(client, ACC, "Item 1A")
    assert result.accession == ACC
    assert result.cik == "0000320193"
    assert result.form == "10-K"
    assert result.section == "1A"
    assert result.title == "ITEM 1A: RISK FACTORS"
    assert result.source_url == DOC_URL
    assert result.truncated is False
    assert result.char_count == len(result.text)
    assert client.calls == [(SUBMISSIONS, 3600.0), (DOC_URL, None)]


@pytest.mark.parametrize("accession", [ACC, ACC_DIGITS, f" {ACC} "])
def test_accession_formats(accession: str) -> None:
    assert get_filing_section(tenk_client(), accession, "1B").accession == ACC


@pytest.mark.parametrize("accession", ["0000320193-25-00079", "abc", "0000320193_25_000079"])
def test_invalid_accession(accession: str) -> None:
    with pytest.raises(EdgarError, match="accession"):
        get_filing_section(tenk_client(), accession, "1A")


def test_truncation_reports_full_length() -> None:
    result = get_filing_section(tenk_client(), ACC, "risk factors", max_chars=40)
    full = get_filing_section(tenk_client(), ACC, "risk factors")
    assert result.truncated is True
    assert len(result.text) == 40
    assert result.char_count == full.char_count > 40
    assert full.text.startswith(result.text)


def test_invalid_max_chars() -> None:
    with pytest.raises(EdgarError, match="max_chars"):
        get_filing_section(tenk_client(), ACC, "1A", max_chars=0)


def test_missing_section_in_document() -> None:
    with pytest.raises(EdgarError, match=r"Section '1C' \(Item 1C\) was not found") as info:
        get_filing_section(tenk_client(), ACC, "1C")
    assert DOC_URL in str(info.value)


def test_unknown_section_name() -> None:
    with pytest.raises(EdgarError, match="Unknown section 'weather'"):
        get_filing_section(tenk_client(), ACC, "weather")


def test_rejects_non_periodic_forms() -> None:
    with pytest.raises(EdgarError, match="8-K"):
        get_filing_section(tenk_client(form="8-K"), ACC, "1A")


def test_10q_end_to_end() -> None:
    client = tenk_client(fixture("tenq_synthetic.htm"), form="10-Q")
    result = get_filing_section(client, ACC, "MD&A")
    assert result.form == "10-Q"
    assert result.section == "I-2"
    assert "Quarterly net sales increased 8%" in result.text


def test_falls_back_to_filing_index_when_not_in_recent_submissions() -> None:
    client = FakeClient(
        {
            SUBMISSIONS: submissions(accession="0000320193-24-000123"),
            f"{FOLDER}/index.json": {"directory": {"item": []}},
        },
        {
            f"{FOLDER}/{ACC}-index.htm": fixture("aapl_10k_2025_index.htm"),
            DOC_URL: fixture("aapl_10k_2025_excerpt.htm"),
        },
    )
    result = get_filing_section(client, ACC, "1B")
    assert result.form == "10-K"
    assert result.source_url == DOC_URL
    assert result.text == "Item 1B. Unresolved Staff Comments\nNone."
    assert [url for url, _ in client.calls] == [
        SUBMISSIONS,
        f"{FOLDER}/index.json",
        f"{FOLDER}/{ACC}-index.htm",
        DOC_URL,
    ]


def test_falls_back_to_index_when_submissions_unavailable() -> None:
    client = FakeClient(
        {f"{FOLDER}/index.json": {"directory": {"item": []}}},
        {f"{FOLDER}/{ACC}-index.htm": fixture("aapl_10k_2025_index.htm")},
    )
    doc = find_primary_document(client, ACC)
    assert (doc.cik, doc.form, doc.url) == ("0000320193", "10-K", DOC_URL)


def test_filing_agent_prefix_asks_for_cik() -> None:
    agent_acc = "0001628280-26-003952"
    with pytest.raises(EdgarError, match="pass the company's `cik`"):
        get_filing_section(FakeClient({}, {}), agent_acc, "1A")


def test_explicit_cik_is_used_instead_of_accession_prefix() -> None:
    agent_acc = "0001628280-26-003952"
    sub_url = "https://data.sec.gov/submissions/CIK0001318605.json"
    doc_url = "https://www.sec.gov/Archives/edgar/data/1318605/000162828026003952/tsla-20251231.htm"
    client = FakeClient(
        {sub_url: submissions(accession=agent_acc, doc="tsla-20251231.htm")},
        {doc_url: fixture("tenk_synthetic.htm")},
    )
    result = get_filing_section(client, agent_acc, "business", cik="1318605")
    assert result.cik == "0001318605"
    assert result.source_url == doc_url
    assert result.section == "1"


def test_explicit_cik_not_found_reports_filing() -> None:
    with pytest.raises(EdgarError, match="was not found under CIK 0000789019"):
        get_filing_section(FakeClient({}, {}), ACC, "1A", cik="789019")


def test_invalid_cik() -> None:
    with pytest.raises(EdgarError, match="CIK"):
        get_filing_section(FakeClient({}, {}), ACC, "1A", cik="AAPL")
