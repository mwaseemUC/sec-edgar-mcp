import json
from pathlib import Path

import pytest

from evals import scoring

GOLDEN = Path(__file__).resolve().parent.parent / "evals" / "golden.jsonl"

REVENUE = {
    "label": "AAPL FY2023 revenue",
    "value": 383285000000,
    "unit": "USD",
    "tolerance_pct": 0.5,
}
GROWTH = {
    "label": "NVDA revenue growth FY2022-FY2024",
    "value": 126.4,
    "unit": "percent",
    "abs_tolerance": 0.15,
    "derived": {"op": "growth_pct", "from": 26914000000, "to": 60922000000},
}
ACCESSION = {"label": "MSFT 10-K accession", "value": "0000950170-24-087843", "unit": "accession"}
DATE = {"label": "filing date", "value": "2025-04-30", "unit": "date"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (383285000000, 383285000000.0),
        (11.8, 11.8),
        ("383,285,000,000", 383285000000.0),
        ("$383.3B", 383.3e9),
        ("$383.285 billion", 383.285e9),
        ("383,285 million", 383285e6),
        ("USD 3.875 trillion", 3.875e12),
        ("126.4%", 126.4),
        ("-52.7%", -52.7),
        ("−52.7%", -52.7),
        ("(52.7)%", -52.7),
        ("$11.80", 11.8),
        ("228,000 people", 228000.0),
        ("2", 2.0),
        ("n/a", None),
        (None, None),
        (True, None),
        ([1], None),
    ],
)
def test_parse_number(raw, expected):
    got = scoring.parse_number(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_extract_fenced_json_block():
    text = (
        "Revenue was $383.3B [1].\n\n"
        '```json\n{"values": {"AAPL FY2023 revenue": 383285000000}}\n```\n'
    )
    assert scoring.extract_answer_values(text) == {"AAPL FY2023 revenue": 383285000000}


def test_extract_unfenced_json_uses_last_values_object():
    text = (
        'Draft {"values": {"x": 1}} then a stray {"note": "no values"} and final '
        '{"values": {"x": 2, "label with } brace": "a"}}'
    )
    assert scoring.extract_answer_values(text) == {"x": 2, "label with } brace": "a"}


def test_extract_prefers_fenced_block_over_trailing_prose_object():
    text = '```json\n{"values": {"x": 1}}\n```\nSee {"values": "not a dict"}'
    assert scoring.extract_answer_values(text) == {"x": 1}


@pytest.mark.parametrize(
    "text", ["no json here", '```json\n{"values": [1, 2]}\n```', "{broken json"]
)
def test_extract_returns_none_without_values(text):
    assert scoring.extract_answer_values(text) is None


def test_label_lookup_is_case_and_punctuation_insensitive():
    values = {"aapl fy2023 REVENUE": 1}
    assert scoring.lookup_label(values, "AAPL FY2023 revenue") == 1
    assert scoring.lookup_label(values, "AAPL FY2024 revenue") is None


def test_raw_tolerance_is_half_percent():
    assert scoring.value_matches(REVENUE, 383285000000)
    assert scoring.value_matches(REVENUE, "$383.3B")
    assert scoring.value_matches(REVENUE, 383285000000 * 1.0049)
    assert not scoring.value_matches(REVENUE, 383285000000 * 1.0051)
    assert not scoring.value_matches(REVENUE, "$394.3B")
    assert not scoring.value_matches(REVENUE, None)


def test_percent_uses_absolute_tolerance():
    assert scoring.value_matches(GROWTH, 126.4)
    assert scoring.value_matches(GROWTH, "126.35%")
    assert scoring.value_matches(GROWTH, 126.55)
    assert not scoring.value_matches(GROWTH, 126.6)
    assert not scoring.value_matches(GROWTH, 1.264)


def test_zero_tolerance_count():
    item = {"label": "segments", "value": 2, "unit": "count", "tolerance_pct": 0}
    assert scoring.value_matches(item, 2)
    assert scoring.value_matches(item, "2")
    assert not scoring.value_matches(item, 3)


def test_accession_and_date_matching():
    assert scoring.value_matches(ACCESSION, "0000950170-24-087843")
    assert scoring.value_matches(ACCESSION, "000095017024087843")
    assert not scoring.value_matches(ACCESSION, "0000950170-23-035122")
    assert not scoring.value_matches(ACCESSION, 950170)
    assert scoring.value_matches(DATE, "2025-04-30")
    assert scoring.value_matches(DATE, "April 30, 2025")
    assert not scoring.value_matches(DATE, "2025-04-29")
    assert not scoring.value_matches(DATE, "sometime")


def test_citation_requires_url_returned_by_tools():
    tool_urls = {
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/",
    }
    cited = "Source: https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json."
    assert scoring.is_cited(cited, tool_urls)
    assert scoring.is_cited(
        "(https://WWW.SEC.GOV/Archives/edgar/data/320193/000032019323000106)", tool_urls
    )
    assert not scoring.is_cited(
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json", tool_urls
    )
    assert not scoring.is_cited(
        "https://example.com/api/xbrl/companyfacts/CIK0000320193.json", tool_urls
    )
    assert not scoring.is_cited("no links", tool_urls)


def test_collect_source_urls_walks_nested_models():
    data = {
        "source_url": "https://data.sec.gov/a.json",
        "values": [{"filing_url": "https://www.sec.gov/b/", "value": 1}],
        "filings": [
            {"document_url": "https://www.sec.gov/c.htm", "index_url": "https://www.sec.gov/d"}
        ],
    }
    assert scoring.collect_source_urls(data) == {
        "https://data.sec.gov/a.json",
        "https://www.sec.gov/b/",
        "https://www.sec.gov/c.htm",
        "https://www.sec.gov/d",
    }


def test_score_question_partial_and_tools():
    golden = {"id": "q02", "expected": [REVENUE, GROWTH], "expected_tools": ["get_financial_facts"]}
    answer = (
        "Per https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json ...\n"
        '```json\n{"values": {"AAPL FY2023 revenue": "$383.3B",\n'
        '"NVDA revenue growth FY2022-FY2024": 130}}\n```'
    )
    score = scoring.score_question(
        golden,
        answer,
        ["search_company", "get_financial_facts"],
        {"https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"},
    )
    assert not score.correct
    assert score.partial == 0.5
    assert score.cited
    assert score.used_expected_tools
    assert score.tool_calls == 2
    assert [c.correct for c in score.checks] == [True, False]


def test_score_question_missing_json_scores_zero():
    golden = {"id": "q01", "expected": [REVENUE], "expected_tools": ["get_financial_facts"]}
    score = scoring.score_question(golden, "Revenue was $383.3B.", [], set())
    assert not score.answer_parsed
    assert score.partial == 0.0
    assert not score.correct
    assert not score.used_expected_tools


def test_score_question_missing_label_is_wrong():
    golden = {"id": "q01", "expected": [REVENUE, ACCESSION], "expected_tools": []}
    answer = '{"values": {"AAPL FY2023 revenue": 383285000000}}'
    score = scoring.score_question(golden, answer, [], set())
    assert score.partial == 0.5
    assert score.checks[1].got is None


def test_cost_usd():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_creation_input_tokens": 100_000,
        "cache_read_input_tokens": 1_000_000,
    }
    # 2.00 + 1.00 + 0.25 + 0.20
    assert scoring.cost_usd(usage, "claude-sonnet-5-5") == pytest.approx(3.45)
    assert scoring.cost_usd(usage, "unknown-model") is None


def _row(correct, partial, cited, tools, calls, cost, latency):
    return {
        "correct": correct,
        "partial": partial,
        "cited": cited,
        "used_expected_tools": tools,
        "tool_calls": calls,
        "cost_usd": cost,
        "latency_s": latency,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def test_aggregate():
    rows = [
        _row(True, 1.0, True, True, 2, 0.01, 4.0),
        _row(False, 0.5, False, True, 4, 0.03, 6.0),
    ]
    summary = scoring.aggregate(rows)
    assert summary["questions"] == 2
    assert summary["correct_strict"] == 1
    assert summary["accuracy_strict"] == 0.5
    assert summary["accuracy_partial"] == 0.75
    assert summary["citation_rate"] == 0.5
    assert summary["expected_tool_rate"] == 1.0
    assert summary["tool_calls_per_question"] == 3.0
    assert summary["tokens"]["input_tokens"] == 200
    assert summary["cost_usd"] == pytest.approx(0.04)
    assert summary["latency_s_mean"] == 5.0


def test_aggregate_cost_unknown_when_any_row_unpriced():
    rows = [_row(True, 1.0, True, True, 1, 0.01, 1.0), _row(True, 1.0, True, True, 1, None, 1.0)]
    assert scoring.aggregate(rows)["cost_usd"] is None
    assert scoring.aggregate([])["questions"] == 0


def test_oracle_check_raw_derived_string_and_evidence():
    facts = {
        "values": [{"value": 26914000000.0}, {"value": 60922000000.0}, {"value": 383285000000}]
    }
    assert scoring.oracle_check(REVENUE, [facts])[0]
    assert scoring.oracle_check(GROWTH, [facts])[0]
    assert not scoring.oracle_check(GROWTH, [{"values": [{"value": 60922000000.0}]}])[0]
    filings = {"filings": [{"accession": "0000950170-24-087843"}]}
    assert scoring.oracle_check(ACCESSION, [filings])[0]
    assert not scoring.oracle_check(ACCESSION, [facts])[0]
    section = {
        "text": "Our Businesses We report our business\nresults in two segments. The Compute"
    }
    item = {
        "label": "s",
        "value": 2,
        "unit": "count",
        "evidence": "We report our business results in two segments",
    }
    assert scoring.oracle_check(item, [section])[0]
    assert not scoring.oracle_check(item, [{"text": "three segments"}])[0]


def test_numbers_in_reads_numerals_inside_text():
    assert 228000.0 in scoring.numbers_in({"text": "approximately 228,000 people"})


def test_golden_file_is_well_formed():
    questions = [
        json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(questions) == 10
    assert len({q["id"] for q in questions}) == 10
    tools = {"search_company", "list_filings", "get_financial_facts", "get_filing_section"}
    for q in questions:
        assert q["question"] and q["note"] and q["source_urls"] and q["oracle_calls"]
        assert set(q["expected_tools"]) <= tools
        assert all(c["tool"] in tools for c in q["oracle_calls"])
        for item in q["expected"]:
            assert item["unit"] in {
                "USD",
                "USD/shares",
                "percent",
                "accession",
                "date",
                "count",
                "people",
            }
            if item["unit"] == "percent":
                assert "abs_tolerance" in item
                d = item["derived"]
                assert round(scoring.growth_pct(d["from"], d["to"]), 1) == item["value"]
            elif item["unit"] not in scoring.STRING_UNITS:
                assert "tolerance_pct" in item
            assert scoring.value_matches(item, item["value"])


# ---------------------------------------------------------------- QA follow-ups

CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_OUTPUT = {"source_url": CF_URL, "values": [{"value": 383285000000.0}]}
Q01 = {
    "id": "q01",
    "question": "Apple FY2023 revenue?",
    "expected": [REVENUE],
    "expected_tools": ["get_financial_facts"],
}
GOOD_ANSWER = f'Per {CF_URL}\n```json\n{{"values": {{"AAPL FY2023 revenue": 383285000000}}}}\n```'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FY2023: $383.3B", 383.3e9),
        ("$383.3B in FY2023", 383.3e9),
        ("FY2023 revenue 383,285 million", 383285e6),
        ("FY2022-FY2024 growth 126.4%", 126.4),
        ("–52.7%", -52.7),
        ("−52.7%", -52.7),
        ("3.83285e11", 3.83285e11),
        ("3.83285E+11 USD", 3.83285e11),
        ("2024", 2024.0),
    ],
)
def test_parse_number_qa_cases(raw, expected):
    assert scoring.parse_number(raw) == pytest.approx(expected)


def test_grounded_correct_requires_value_in_tool_output():
    grounded = scoring.score_question(
        Q01, GOOD_ANSWER, ["get_financial_facts"], {CF_URL}, [FACTS_OUTPUT]
    )
    assert grounded.correct and grounded.grounded_correct

    from_memory = scoring.score_question(Q01, GOOD_ANSWER, ["get_financial_facts"], {CF_URL}, [])
    assert from_memory.correct and not from_memory.grounded_correct

    other_value = {"source_url": CF_URL, "values": [{"value": 394328000000.0}]}
    wrong_output = scoring.score_question(
        Q01, GOOD_ANSWER, ["get_financial_facts"], {CF_URL}, [other_value]
    )
    assert wrong_output.correct and not wrong_output.grounded_correct

    no_tool = scoring.score_question(Q01, GOOD_ANSWER, ["search_company"], {CF_URL}, [FACTS_OUTPUT])
    assert no_tool.correct and not no_tool.grounded_correct


def test_aggregate_reports_grounded():
    rows = [
        scoring.score_question(
            Q01, GOOD_ANSWER, ["get_financial_facts"], {CF_URL}, [FACTS_OUTPUT]
        ).to_dict(),
        scoring.score_question(Q01, GOOD_ANSWER, [], set(), []).to_dict(),
    ]
    summary = scoring.aggregate(rows)
    assert summary["grounded_correct"] == 1
    assert summary["accuracy_grounded"] == 0.5
    assert summary["correct_strict"] == 2


def test_company_tickers_url_is_not_a_citation():
    answer = f"Found Apple via {TICKERS_URL}."
    assert not scoring.is_cited(answer, {TICKERS_URL, CF_URL})
    assert scoring.is_cited(f"{answer} Revenue: {CF_URL}", {TICKERS_URL, CF_URL})
    assert scoring.is_cited(
        "https://data.sec.gov/submissions/CIK0000789019.json",
        {"https://data.sec.gov/submissions/CIK0000789019.json"},
    )


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeMessages:
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("API down")
        block = _Block(type="tool_use", id="t1", name="get_financial_facts", input={"cik": "1"})
        usage = _Block(input_tokens=10, output_tokens=5)
        return _Block(model="m", stop_reason="tool_use", content=[block], usage=usage)


class _FakeTools:
    definitions: list = []

    async def call(self, name, args):
        from evals.score import ToolRecord

        return ToolRecord(name, args, False, json.dumps(FACTS_OUTPUT), FACTS_OUTPUT, 0.0)


def test_tool_calls_survive_exception_mid_loop(monkeypatch, tmp_path):
    import anyio

    from evals import score

    monkeypatch.setattr(score, "RESULTS", tmp_path)
    client = _Block(messages=_FakeMessages())
    row = anyio.run(score.run_question_claude, client, _FakeTools(), Q01, "claude-sonnet-5-5", None)
    assert row["error"] == "RuntimeError: API down"
    assert row["tool_calls"] == 1
    assert row["used_expected_tools"]
    assert row["usage"]["input_tokens"] == 10
    assert not row["correct"]


def test_errored_tool_calls_do_not_count():
    from evals.score import ToolRecord, Transcript, score_transcript

    out = Transcript(answer=GOOD_ANSWER)
    out.records = [
        ToolRecord("get_financial_facts", {}, True, "boom", None, 0.0),
        ToolRecord("search_company", {}, False, "{}", {"source_url": TICKERS_URL}, 0.0),
    ]
    result = score_transcript(Q01, out)
    assert result.tool_calls == 1
    assert not result.used_expected_tools
    assert not result.grounded_correct
    assert not result.cited
