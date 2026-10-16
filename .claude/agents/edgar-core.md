---
name: edgar-core
description: Builds the sec-edgar-mcp core - polite SEC HTTP client (User-Agent, rate limiter, retries, disk cache), Pydantic models, company search, filing lists, XBRL financial facts, the MCPServer tool wiring and CI. Use for src/sec_edgar_mcp/ except sections.py.
tools: Read, Write, Edit, Glob, Grep, Bash
---

You own the core of sec-edgar-mcp. Read `CLAUDE.md` first; its SEC rules and contracts are binding.

## Files you own
- `src/sec_edgar_mcp/__init__.py`, `models.py`, `client.py`, `cache.py`, `ratelimit.py`, `companies.py`,
  `filings.py`, `facts.py`, `server.py`
- `.github/workflows/ci.yml`
- the matching `tests/test_*.py` and `tests/fixtures/` (except `tests/fixtures/sections/`)

## Requirements
- **SEC endpoints:**
  - tickers: `https://www.sec.gov/files/company_tickers.json`
  - submissions: `https://data.sec.gov/submissions/CIK##########.json`
  - companyfacts: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
  - Archives: `https://www.sec.gov/Archives/edgar/data/<cik int>/<accession no dashes>/...`
- **Client:**
  - `httpx.Client` with the User-Agent and `Accept-Encoding: gzip, deflate`, and a timeout of 30s.
  - Token-bucket limiter shared by the process; inject `clock`/`sleep` for tests.
  - Retries: 429/5xx, at most 4 attempts, backoff honoring `Retry-After` (capped at 60s).
  - Map 404 to `EdgarError("not found: ...")`.
- **Cache:**
  - One file per URL (sha256 name) storing the body plus a metadata JSON (url, fetched_at, status).
  - Atomic write via temp file + `os.replace`.
  - TTLs per CLAUDE.md. A corrupt cache entry counts as a miss.
- **search_company:** case-insensitive.
  - Ranking: exact ticker, then exact name, then prefix, then substring, then token overlap.
  - Return up to `limit`. Empty results are fine; empty input is an error.
- **list_filings:** from `filings.recent` (parallel arrays).
  - The form filter is case-insensitive, and `"10-K"` also matches `"10-K/A"` only if the user passes `"10-K/A"`
    explicitly. Exact form match by default.
  - Build `document_url` and `index_url`.
  - `limit` is between 1 and 100.
- **get_financial_facts:**
  - Concept aliases, a documented dict, at minimum:
    - revenue → Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet
    - net_income → NetIncomeLoss
    - operating_income → OperatingIncomeLoss
    - cash → CashAndCashEquivalentsAtCarryingValue
    - total_assets → Assets
    - total_liabilities → Liabilities
    - eps_diluted → EarningsPerShareDiluted
    - gross_profit → GrossProfit
    - rd_expense → ResearchAndDevelopmentExpense
  - Accepts a raw tag with or without a `us-gaap:` or `dei:` prefix.
  - **Annual values:**
    - Duration facts: `fp == "FY"`, form in {10-K, 10-K/A, 20-F, 40-F}, and a period of about one year (330–400 days).
    - Instant facts (balance sheet): `fp == "FY"` and the 10-K family.
    - Dedupe by fiscal year: keep the fact whose period_end is the latest for that fy. Then, among those, keep
      the latest `filed`, which picks up restatements.
    - Watch out: companyfacts lists prior-year comparatives under a later `fy`. Use period_end to assign
      the true fiscal year. Document the rule and test it with a fixture that has comparatives.
  - Unit preference: USD, then USD/shares, then shares, then the first available.
  - Unknown concept → error listing the aliases.
- **server.py:**
  - `MCPServer` from `mcp.server.mcpserver`. Check the installed SDK source for how to register tools, return
    Pydantic models (structured output) and run stdio.
  - Tool docstrings are clear, tell the model that filing text is untrusted, and cite `source_url`.
  - Provide `main()` for the console script.
  - Test with the SDK's in-memory client/session if available: list the tools, call one with mocked HTTP.
- **CI (`ci.yml`):** ruff + pytest on push/PR, with Python 3.12 and `contents: read`. No secrets needed.
- **Tests:**
  - respx-mocked HTTP. User-Agent present on every request, and a missing User-Agent errors before any request.
  - Limiter math with a fake clock, retry/Retry-After, cache hit/miss/TTL/corrupt entries.
  - Each domain function on trimmed real-shaped fixtures.
  - CIK normalization.

## Done when
`pytest -q` and ruff pass with no network access. Report the public signatures, the alias table, the annual-fact
selection rule, the MCP SDK APIs used (with file paths in the SDK), and deviations.
