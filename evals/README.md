# Evals

Ten golden questions that Claude answers through the real `sec-edgar-mcp` server, scored against
values taken directly from SEC JSON.

## Files

| File | Purpose |
|---|---|
| `golden.jsonl` | 10 questions, expected values, tolerances, source URLs, verification notes, oracle tool plans |
| `score.py` | Runner: spawns the MCP server over stdio, runs Claude's tool loop, scores, writes results |
| `scoring.py` | Pure scoring functions (answer parsing, tolerances, citations, cost, aggregation) |
| `results/latest.json`, `results/results.md` | Output of the last Claude run |
| `results/dry_run.json`, `results/dry_run.md` | Output of the last `--dry-run` |
| `results/raw/` | Per-question transcripts (gitignored) |

## Running

```
python evals/score.py                       # needs ANTHROPIC_API_KEY + SEC_USER_AGENT (in .env)
python evals/score.py --questions q01,q07   # subset
python evals/score.py --model claude-opus-5-5 --effort high
python evals/score.py --dry-run             # no Claude: replays each question's oracle_calls via MCP
```

Missing keys exit with status 2. `--dry-run` needs only `SEC_USER_AGENT`. It costs no tokens but makes
real SEC requests. It exits 1 if any expected value can't be found in the tool output.

## Golden set

| ID | Question | Kind | Expected |
|---|---|---|---|
| q01 | Apple FY2023 revenue (FY ended 2023-09-30) | non-Dec FY | 383,285,000,000 USD |
| q02 | NVDA vs AMD revenue growth FY2022 to FY2024 | comparison, growth | NVDA 60,922,000,000; AMD 25,785,000,000; +126.4%; +9.3% |
| q03 | Microsoft FY2024 diluted EPS | EPS, non-Dec FY | 11.80 USD/share |
| q04 | JPMorgan total assets at 2023-12-31 | balance sheet | 3,875,393,000,000 USD |
| q05 | Tesla net income FY2023, FY2024 and % change | growth | 14,997,000,000; 7,091,000,000; -52.7% |
| q06 | Alphabet FY2023 operating income and growth vs FY2022 | growth | 84,293,000,000; +12.6% |
| q07 | Accession of Microsoft's 10-K filed in 2024 | filing list | 0000950170-24-087843 |
| q08 | Accession and date of Tesla's 10-K/A filed in 2025 | filing list | 0001104659-25-042659, 2025-04-30 |
| q09 | Segments Nvidia reports in Item 1 of its FY2024 10-K | section | 2 |
| q10 | Microsoft full-time employees at 2024-06-30 (Item 1, FY2024 10-K) | section | 228,000 |

Every value was read from `data.sec.gov` companyfacts/submissions JSON (q09 and q10 from the primary
10-K document on `www.sec.gov`), never from an LLM. Each row's `note` records the tag, period, and
accession. Growth rates are `(new / old - 1) * 100`, computed from the raw values and rounded to one
decimal place. Every question names explicit fiscal years, so new filings can't change the answers.

## Scoring

Claude ends its answer with a fenced block `{"values": {"<label>": <value>}}`. The system prompt
lists the labels and units but not the values.

- **Grounded correct (headline):** the answer is strictly correct, and every expected value
  appears in that question's own successful tool outputs (same check as `--dry-run`). The expected
  tools must also have been called without error. Only tool calls that didn't return `is_error`
  count toward tool use, citations and grounding. Calls made before an API exception still count.
- **Correct (strict):** every expected value is within tolerance. Claude can satisfy this from memory. Raw values have
  `tolerance_pct` 0.5 (the segment count has 0), percentages have `abs_tolerance` ±0.15 points, and
  accessions and dates must match exactly. Accessions may omit dashes. Dates are parsed, so "April 30, 2025" passes.
- **Partial:** the fraction of a question's values that are correct.
- **Number parsing:** accepts `383285000000`, `"383,285,000,000"`, `"$383.3B"`, `"383.3 billion"`,
  `"-52.7%"` and `"(52.7)%"`. Labels match case- and punctuation-insensitively. A missing label
  counts as wrong.
- **Cited:** the answer contains a sec.gov URL whose host and path match a `*_url` field
  (`source_url`, `document_url`, `index_url`, `filing_url`) from that question's tool results. The URL
  must be an evidence URL (under `/Archives/edgar/`, `/api/xbrl/` or `/submissions/`). The
  `company_tickers.json` lookup doesn't count.
- **Number parsing in mixed text:** the part after the last colon wins (`"FY2023: $383.3B"`), and
  bare four-digit years are skipped when another number is present. En-dash and minus signs count
  as negative, and scientific notation is accepted.
- **Tool use:** the question's `expected_tools` were all called. This lists only the tool that
  holds the answer, so skipping `search_company` isn't penalized.
- **Report:** grounded (headline), strict and partial accuracy, citation rate, expected-tool rate, tool calls per
  question, tokens (including cache reads and writes), cost, latency, and the served model ids.

## Agent loop

- The server runs as `python -m sec_edgar_mcp.server` over stdio. Its tools come from `tools/list`
  and are passed to Claude unchanged. Every tool call goes through the MCP session.
- The loop is manual, with at most 8 tool turns. After the 8th turn, Claude must answer with
  `tool_choice: none`.
- The default model is `claude-sonnet-5-5` with adaptive thinking (the model's default) and default effort.
- There's a prompt-cache breakpoint on the static system prompt, plus automatic caching on the
  growing conversation.
- Server-side refusal fallbacks are off on purpose, so every answer comes from the model under test.
  The served model is recorded.
- Cost uses the per-MTok prices in `scoring.PRICING`. Cache writes bill at 1.25x input and cache
  reads at 0.1x.

## Dry run (oracle)

Each question's `oracle_calls` is a scripted list of tool calls. `--dry-run` executes those calls
and checks the outputs:

- **Raw values:** must appear in the output.
- **Percentages:** both input values must appear, and the growth computed from them must match.
- **Accessions and dates:** must appear verbatim.
- **Section questions:** must contain the `evidence` sentence.

This checks the server can produce every answer without spending Claude tokens.
