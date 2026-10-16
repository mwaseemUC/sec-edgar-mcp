---
name: filing-sections
description: Builds get_filing_section for sec-edgar-mcp - locates a filing's primary document from its accession, converts 10-K/10-Q HTML (including inline XBRL) to text, and extracts a named Item section. Use for src/sec_edgar_mcp/sections.py.
tools: Read, Write, Edit, Glob, Grep, Bash
---

You own section extraction for sec-edgar-mcp. Read `CLAUDE.md` first; its contracts are binding.

## Files you own
`src/sec_edgar_mcp/sections.py`, `tests/test_sections.py`, `tests/fixtures/sections/`.

## Interfaces you depend on
`client.py` and `models.py` are being written in parallel by `edgar-core`. Code against the CLAUDE.md contract:
- `EdgarClient.get_json` and `get_text`
- `FilingSection`
- `EdgarError`

If they don't exist yet, create nothing in their files. Use the contract, and in tests use a small fake client
object with `get_json`/`get_text`.

## Requirements
- **Accession:** accept `0000320193-23-000106` or the 18 digits without dashes.
- **CIK:** if `cik` is given, use it. Otherwise try the accession's filer prefix (the first 10 digits).
  - That prefix is often a filing agent, not the company. If the filing index 404s under that CIK, raise a
    clear error asking for `cik`.
  - Document this.
- **Filing index:** `https://www.sec.gov/Archives/edgar/data/<cik int>/<acc no dashes>/index.json`
  (directory listing). Also read the `-index.htm` or `<acc>-index-headers.html` if needed to identify the
  primary document and form.
  - Prefer the submissions API's primaryDocument when the accession is in `filings.recent`.
  - Pick the most robust approach and explain it.
- **Section names:** normalize `"1A"`, `"Item 1A"`, `"item 1a."`, `"risk factors"`, `"MD&A"`/`"management's
  discussion"` → `"7"`, `"7A"`/`"market risk"`, `"8"`/`"financial statements"`, `"1"`/`"business"`,
  `"2"`/`"properties"`, `"3"`/`"legal proceedings"`. 10-Q Part I Item 2 = MD&A.
  - Unknown section → error listing the accepted names.
- **HTML to text:** BeautifulSoup with `html.parser`.
  - Drop script/style and the hidden inline-XBRL header (`ix:header`, `display:none` blocks).
  - Turn block elements into newlines, collapse whitespace, and keep table rows readable (cells joined with " | ").
- **Finding the section:**
  - Item headings appear twice: once in the table of contents, once in the body.
  - Pick the occurrence followed by the most text before the next Item heading, or the last match. Skip TOC
    entries, which are short lines followed immediately by another Item heading or a page number.
  - The section ends at the next Item heading (e.g. Item 1A ends at Item 1B or Item 2).
  - Handle "Item 1A." / "ITEM 1A:" / "Item 1A —" variants and non-breaking spaces.
- **Output:**
  - `max_chars` truncation sets `truncated=True`, with `char_count` being the full length.
  - `title` is the heading text found.
  - `source_url` is the primary document URL.
- **Tests:** hand-made small HTML fixtures that mimic real 10-K structure: a TOC plus a body, inline-XBRL
  hidden header, nbsp, an all-caps heading variant, and a 10-Q. Cover normalization, TOC-skip, end boundary,
  truncation, a missing section, and the accession formats. Also test with one trimmed real 10-K excerpt if you
  can fetch one politely. If you do, use the User-Agent from env `SEC_USER_AGENT` and
  `SSL_CERT_FILE=.venv/ca-bundle.pem`, fetch once, and save a small trimmed fixture; tests must never fetch.

## Done when
`pytest -q tests/test_sections.py` and ruff pass. Report the section-detection algorithm, known failure modes, and
any real filing you verified it on.
