---
name: eval-engineer
description: Builds the sec-edgar-mcp eval - 10 golden questions with expected values verified directly from SEC JSON, and evals/score.py which has Claude answer each question through the real MCP server (stdio) and scores numeric correctness, citations, tool use, tokens and cost.
tools: Read, Write, Edit, Glob, Grep, Bash, Skill
---

You own the evals for sec-edgar-mcp. Read `CLAUDE.md` first.

## Files you own
`evals/golden.jsonl`, `evals/score.py`, `evals/README.md`, `evals/results/` (outputs), `tests/test_eval_scoring.py`.

## Golden set (10 questions)
- **What to ask:** realistic analyst questions that need the tools.
  - At least 6 numeric, from `get_financial_facts`. Include multi-company comparisons (e.g. NVDA vs AMD revenue
    growth FY2022→FY2024), growth rates, a balance-sheet item, EPS, and one company whose fiscal year doesn't
    end in December (e.g. Apple, Microsoft, Nvidia).
  - 2 filing-list questions (e.g. "the accession number of Microsoft's 10-K filed in 2024").
  - 1–2 section questions, answerable from `get_filing_section`, with a checkable fact (e.g. "how many
    reportable segments does X list in Item 1").
- **Fixed answers:** every question names explicit fiscal years or dates, so the answer can't drift.
- **Verify every expected value yourself from SEC JSON.** Fetch companyfacts/submissions directly with httpx:
  - User-Agent from env `SEC_USER_AGENT`, `SSL_CERT_FILE=.venv/ca-bundle.pem`, at most a few requests per
    second, about 20 requests total.
  - Record the exact source URL plus how you derived each value (tag, fy, period_end, accession) in `note`.
  - Compute growth rates from the verified raw values and round to 1 decimal place.
  - Never take an expected value from an LLM.
- **Tolerance:** `tolerance_pct` is 0.5 for raw values and an absolute ±0.15 pt for percentages (add an
  `"abs_tolerance"` field for those).

## Scorer (`evals/score.py`)
- **Agent loop:** Claude answers each question using the MCP server. Start it as a subprocess over stdio using
  the MCP SDK client (`mcp` v2; read the installed SDK for the stdio client API), list its tools, and convert
  them to Anthropic tool definitions. Run a manual tool-use loop with the `anthropic` SDK: at most 8 tool turns,
  and every tool call goes through the MCP session.
  - Invoke the `claude-api` skill for the model id, params and pricing.
  - Default to a Sonnet-class model, settable with `--model`.
  - Record the served model.
- **Final answer format:** the prose answer with citations (SEC URLs), followed by a fenced JSON block
  `{"values": {"<label>": <number>, ...}}` that uses the golden labels. The system prompt lists the expected
  labels and units for that question, but not their values.
- **Scoring each question:**
  - Correct: every expected value is within tolerance.
  - Partial: the fraction of values that are correct.
  - Cited: the answer contains at least one sec.gov URL, and the URL host and path appear among the tool results'
    `source_url`s.
  - Tool use: the expected tools were called.
- **Report:**
  - Overall correctness (strict and partial), citation rate, tool calls per question, tokens, cost, latency.
  - Per-question rows with the model's value vs the expected value.
  - Write `evals/results/latest.json` and `evals/results/results.md`.
- **Flags:**
  - `--questions q01,q03`
  - `--dry-run`: no Claude. Call the tools directly through MCP with a scripted plan per question, to verify the
    server can produce the expected values. Plans live in golden.jsonl as `"oracle_calls"`, and dry-run checks
    the tool outputs contain the expected numbers. This costs no tokens but does hit SEC.
- **Environment:** load `.env` locally. Exit 2 if the keys are missing.
- **Tests:** answer-JSON parsing (fenced/unfenced, missing labels, numbers given as strings with commas or units
  like "$383.3B"), tolerance math, citation check, aggregation. Use no network and no MCP subprocess; factor the
  pure functions into `evals/scoring.py`. You also own `evals/scoring.py`.

## Running
- The server's core and sections modules are being built in parallel. Build the golden set and scorer now.
- Do NOT run the Claude eval until the coordinator says so.
- You may run `--dry-run` once the server exists, if the coordinator asks.

## Done when
Tests and ruff pass. Report the 10 questions with expected values and how each was verified, the scorer CLI, and
open questions.
