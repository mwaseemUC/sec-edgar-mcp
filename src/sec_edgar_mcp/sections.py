"""Extract a named Item section (e.g. Item 1A, Item 7) from a 10-K or 10-Q filing.

Locating the primary document:
  1. CIK: the `cik` argument if given, else the accession's first 10 digits. That prefix
     identifies whoever submitted the filing, which is often a filing agent (e.g. Workiva,
     Donnelley) rather than the company. EDGAR only serves the filing under the company's CIK, so
     when the inferred CIK is wrong the index lookup 404s and we raise an error asking for `cik`.
  2. Submissions API (`data.sec.gov/submissions/CIK##########.json`): if the accession is in
     `filings.recent`, its `primaryDocument` and `form` are used directly. This JSON is shared
     with `list_filings` and usually already cached.
  3. Otherwise the filing index: `index.json` (directory listing; confirms the filing exists under
     this CIK) and then `<accession>-index.htm`, whose document table lists the primary document
     as sequence 1 together with its form type. Archives files are immutable and cached forever.

Finding the section in the converted text:
  - A heading is a short line that starts with "Item <n>" (optionally preceded by "Part I"),
    e.g. "Item 1A. Risk Factors", "ITEM 7 - MANAGEMENT'S ...", or a table-of-contents row
    "Item 1A. | Risk Factors | 12". Lines starting with "PART I/II/..." track the current Part,
    which matters for 10-Qs where Part I Item 2 (MD&A) and Part II Item 2 are different sections.
  - Headings appear in the table of contents and again in the body. TOC rows ending in a page
    number are never used; headings followed directly by a page number or another heading are
    used only if nothing else matches (e.g. "Item 6. [Reserved]"). Each candidate's body runs to
    the next heading of a different item (or the next Part line); the one with the most text
    wins, ties going to the last.
  - Section labels: 10-K items are returned as "1A", "7", ...; 10-Q items carry their Part,
    "I-2" (MD&A) or "II-1A" (risk factors), since item numbers repeat across Parts.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment, Declaration, Doctype, ProcessingInstruction

from .client import (
    TTL_SUBMISSIONS,
    EdgarError,
    archives_folder_url,
    filing_document_url,
    filing_index_url,
    normalize_accession,
    normalize_cik,
    submissions_url,
)
from .models import FilingSection

TEN_K_ITEMS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "[Reserved] / Selected Financial Data",
    "7": "Management's Discussion and Analysis (MD&A)",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

TEN_Q_ITEMS: dict[tuple[str, str], str] = {
    ("I", "1"): "Financial Statements",
    ("I", "2"): "Management's Discussion and Analysis (MD&A)",
    ("I", "3"): "Quantitative and Qualitative Disclosures About Market Risk",
    ("I", "4"): "Controls and Procedures",
    ("II", "1"): "Legal Proceedings",
    ("II", "1A"): "Risk Factors",
    ("II", "2"): "Unregistered Sales of Equity Securities and Use of Proceeds",
    ("II", "3"): "Defaults Upon Senior Securities",
    ("II", "4"): "Mine Safety Disclosures",
    ("II", "5"): "Other Information",
    ("II", "6"): "Exhibits",
}

# Name aliases resolve to a 10-K item; 10-K-only items map to their 10-Q equivalent below.
NAME_ALIASES: dict[str, str] = {
    "business": "1",
    "riskfactors": "1A",
    "unresolvedstaffcomments": "1B",
    "cybersecurity": "1C",
    "properties": "2",
    "legalproceedings": "3",
    "minesafety": "4",
    "marketforregistrant": "5",
    "selectedfinancialdata": "6",
    "mda": "7",
    "mdanda": "7",
    "managementsdiscussion": "7",
    "managementdiscussion": "7",
    "quantitativeandqualitativedisclosures": "7A",
    "marketrisk": "7A",
    "financialstatements": "8",
    "changesinanddisagreements": "9",
    "controlsandprocedures": "9A",
    "otherinformation": "9B",
    "directors": "10",
    "executivecompensation": "11",
    "securityownership": "12",
    "certainrelationships": "13",
    "principalaccountant": "14",
    "exhibits": "15",
    "form10ksummary": "16",
}

TEN_K_TO_TEN_Q: dict[str, tuple[str, str]] = {
    "1A": ("II", "1A"),
    "3": ("II", "1"),
    "4": ("II", "4"),
    "7": ("I", "2"),
    "7A": ("I", "3"),
    "8": ("I", "1"),
    "9A": ("I", "4"),
    "9B": ("II", "5"),
    "15": ("II", "6"),
}

ROMAN = {"I", "II", "III", "IV"}

_SECTION_INPUT = re.compile(
    r"^(?:part\s*(iv|i{1,3})\b[\s,.:\-]*)?(?:item\s*)?(\d{1,2}[a-c]?)\.?$", re.IGNORECASE
)
_HEADING = re.compile(
    r"^(?:part\s+(?:iv|i{1,3})\b\s*[,.:\-–—|]*\s*)?"
    r"item\s*(\d{1,2}[a-c]?)(?![0-9a-z])\s*[.:\-–—|]*\s*(.*)$",
    re.IGNORECASE,
)
_PART = re.compile(r"^part\s+(iv|i{1,3})\b(?!\s*,?\s*item)", re.IGNORECASE)
_PAGE = r"(?:(?:[A-Z]-)?\d{1,3}|[ivxlc]{1,6})"
_PAGE_REF = re.compile(rf"\|\s*{_PAGE}\s*$", re.IGNORECASE)
_PAGE_NUMBER = re.compile(rf"(?:page\s+)?{_PAGE}", re.IGNORECASE)
_HEADING_MAX_LEN = 160
_TITLE_LINE_MAX_LEN = 120

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
    "td", "tfoot", "th", "thead", "title", "tr", "ul",
}  # fmt: skip
DROP_TAGS = {"script", "style", "head", "ix:header", "noscript", "template"}
_HIDDEN = re.compile(r"display\s*:\s*none", re.IGNORECASE)


class SupportsGet(Protocol):
    def get_json(self, url: str, *, ttl: float | None) -> Any: ...
    def get_text(self, url: str, *, ttl: float | None) -> str: ...


@dataclass(frozen=True)
class SectionTarget:
    key: str  # normalized label returned to callers, e.g. "1A" (10-K) or "II-1A" (10-Q)
    part: str | None  # required Part for 10-Q items, None for 10-K
    item: str


@dataclass(frozen=True)
class ExtractedSection:
    title: str
    text: str


@dataclass(frozen=True)
class PrimaryDocument:
    cik: str
    form: str
    url: str


# ---------------------------------------------------------------- section names


def accepted_section_names(form: str = "10-K") -> str:
    if _is_10q(form):
        items = ", ".join(f"Part {p} Item {i} ({t})" for (p, i), t in TEN_Q_ITEMS.items())
        return f"{items}; also names like 'MD&A', 'risk factors', 'market risk'"
    items = ", ".join(f"{i} ({t})" for i, t in TEN_K_ITEMS.items())
    return (
        f"{items}; also 'Item 1A', 'risk factors', 'MD&A', \"management's discussion\", "
        "'market risk', 'financial statements', 'business', 'properties', 'legal proceedings'"
    )


def _alias_key(text: str) -> str:
    text = text.lower().replace("&", "and").replace("’", "'")
    return re.sub(r"[^a-z0-9]", "", text)


def _resolve_alias(text: str) -> str | None:
    key = _alias_key(text)
    if key.startswith("item"):
        key = key[4:]
    for alias, item in NAME_ALIASES.items():
        if key == alias or key.startswith(alias):
            return item
    return None


def normalize_section(section: str, form: str = "10-K") -> SectionTarget:
    raw = re.sub(r"\s+", " ", (section or "").replace("\xa0", " ")).strip()
    match = _SECTION_INPUT.match(raw)
    part = match.group(1).upper() if match and match.group(1) else None
    item = match.group(2).upper() if match else _resolve_alias(raw)
    if item is None:
        raise _unknown_section(section, form)
    if _is_10q(form):
        return _ten_q_target(item, part, named=match is None, section=section)
    if item not in TEN_K_ITEMS:
        raise _unknown_section(section, form)
    return SectionTarget(key=item, part=None, item=item)


def _ten_q_target(item: str, part: str | None, *, named: bool, section: str) -> SectionTarget:
    if named or (part is None and not _ten_q_has(item)):
        mapped = TEN_K_TO_TEN_Q.get(item)
        if mapped is None:
            raise _unknown_section(section, "10-Q")
        part, item = mapped
    elif part is None:
        part = "I" if ("I", item) in TEN_Q_ITEMS else "II"
    if (part, item) not in TEN_Q_ITEMS:
        raise _unknown_section(section, "10-Q")
    return SectionTarget(key=f"{part}-{item}", part=part, item=item)


def _ten_q_has(item: str) -> bool:
    return any(i == item for _, i in TEN_Q_ITEMS)


def _unknown_section(section: str, form: str) -> Exception:
    return EdgarError(
        f"Unknown section {section!r} for a {form}. Accepted: {accepted_section_names(form)}."
    )


def _is_10q(form: str) -> bool:
    return form.upper().startswith("10-Q")


# ---------------------------------------------------------------- HTML to text


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        if tag.name in DROP_TAGS or _HIDDEN.search(str(tag.get("style", ""))):
            tag.decompose()
    chunks: list[str] = []
    _render(soup, chunks)
    return _clean_lines("".join(chunks))


def _render(node: Tag, out: list[str]) -> None:
    for child in node.children:
        if isinstance(child, (Comment, Declaration, Doctype, ProcessingInstruction)):
            continue
        if isinstance(child, NavigableString):
            out.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "table":
                out.append("\n" + _render_table(child) + "\n")
            elif child.name in BLOCK_TAGS:
                out.append("\n")
                _render(child, out)
                out.append("\n")
            else:
                _render(child, out)


def _render_table(table: Tag) -> str:
    rows: list[str] = []
    for tr in _own_rows(table):
        cells = []
        for cell in tr.find_all(["td", "th"], recursive=False):
            parts: list[str] = []
            _render(cell, parts)
            text = _collapse("".join(parts))
            if text:
                cells.append(text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _own_rows(table: Tag) -> Iterator[Tag]:
    for child in table.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "tr":
            yield child
        elif child.name in {"thead", "tbody", "tfoot"}:
            yield from (tr for tr in child.children if isinstance(tr, Tag) and tr.name == "tr")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _clean_lines(text: str) -> str:
    lines = (_collapse(line) for line in text.replace("\xa0", " ").splitlines())
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------- section finding


@dataclass(frozen=True)
class _Heading:
    line: int
    part: str | None
    item: str
    rest: str


def _scan(lines: list[str]) -> tuple[list[_Heading], list[int]]:
    headings: list[_Heading] = []
    part_lines: list[int] = []
    part: str | None = None
    for i, line in enumerate(lines):
        if len(line) > _HEADING_MAX_LEN:
            continue
        part_match = _PART.match(line)
        if part_match and _looks_like_heading(line[part_match.end() :].strip(" .:-–—|")):
            part = part_match.group(1).upper()
            part_lines.append(i)
            continue
        match = _HEADING.match(line)
        if match and _looks_like_heading(match.group(2)):
            line_part = re.match(r"^part\s+(iv|i{1,3})\b", line, re.IGNORECASE)
            if line_part:
                part = line_part.group(1).upper()
            headings.append(_Heading(i, part, match.group(1).upper(), match.group(2)))
    return headings, part_lines


def _looks_like_heading(rest: str) -> bool:
    rest = rest.lstrip("| ").strip()
    if not rest:
        return True
    return not re.match(r"^(of|in|and|to|above|below|herein|for|as|is|was)\b", rest) and not (
        rest[0].islower()
    )


def extract_section(text: str, target: SectionTarget) -> ExtractedSection | None:
    lines = text.split("\n")
    headings, part_lines = _scan(lines)
    matches = [
        h
        for h in headings
        if h.item == target.item
        and (not target.part or h.part == target.part)
        and not _PAGE_REF.search(lines[h.line])
    ]
    body_matches = [h for h in matches if not _maybe_toc_entry(lines, h)]
    best: tuple[int, int, int] | None = None
    for h in body_matches or matches:
        end = _section_end(h, headings, part_lines, lines)
        size = sum(len(line) for line in lines[h.line + 1 : end])
        if best is None or size >= best[2]:
            best = (h.line, end, size)
    if best is None:
        return None
    start, end, _ = best
    title = _title(lines, start, end)
    body = "\n".join(lines[start:end])
    return ExtractedSection(title=title, text=body)


def _maybe_toc_entry(lines: list[str], heading: _Heading) -> bool:
    nxt = lines[heading.line + 1] if heading.line + 1 < len(lines) else ""
    return bool(_PAGE_NUMBER.fullmatch(nxt) or _HEADING.match(nxt) or _PART.match(nxt))


def _section_end(
    start: _Heading, headings: list[_Heading], part_lines: list[int], lines: list[str]
) -> int:
    end = len(lines)
    for h in headings:
        if h.line > start.line and (h.item != start.item or h.part != start.part):
            end = h.line
            break
    for p in part_lines:
        if start.line < p < end and not _is_running_header(lines[p], start):
            return p
    return end


def _is_running_header(line: str, start: _Heading) -> bool:
    # Some filers (e.g. Microsoft) repeat "PART I" / "Item 1" at the top of every page;
    # a Part line for the section's own Part is a page header, not a boundary.
    match = _PART.match(line)
    return bool(match and start.part and match.group(1).upper() == start.part)


def _title(lines: list[str], start: int, end: int) -> str:
    title = re.sub(r"\s*\|\s*", " ", lines[start]).strip()
    match = _HEADING.match(lines[start])
    if match and not match.group(2).strip(" |") and start + 1 < end:
        nxt = lines[start + 1]
        if len(nxt) <= _TITLE_LINE_MAX_LEN and not _HEADING.match(nxt):
            title = f"{title} {nxt}"
    return title


# ---------------------------------------------------------------- primary document


def _from_submissions(client: SupportsGet, cik: str, accession: str) -> PrimaryDocument | None:
    try:
        data = client.get_json(submissions_url(cik), ttl=TTL_SUBMISSIONS)
    except EdgarError:
        return None
    recent = (data or {}).get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber") or []
    if accession not in accessions:
        return None
    i = accessions.index(accession)
    document = (recent.get("primaryDocument") or [""] * len(accessions))[i]
    form = (recent.get("form") or [""] * len(accessions))[i]
    if not document or not form:
        return None
    return PrimaryDocument(cik, form, filing_document_url(cik, accession, document))


def _from_index(
    client: SupportsGet, cik: str, accession: str, cik_inferred: bool
) -> PrimaryDocument:
    try:
        client.get_json(f"{archives_folder_url(cik, accession)}/index.json", ttl=None)
    except EdgarError as exc:
        if cik_inferred:
            raise EdgarError(
                f"filing {accession} was not found under CIK {cik}, which was taken from the "
                "accession number's prefix. That prefix is often a filing agent rather than the "
                "company: pass the company's `cik` (use search_company to look it up)."
            ) from exc
        raise EdgarError(f"filing {accession} was not found under CIK {cik}: {exc}") from exc
    index_html = client.get_text(filing_index_url(cik, accession), ttl=None)
    document, form = _parse_index_page(index_html)
    if not document:
        raise EdgarError(f"could not identify the primary document of filing {accession}")
    return PrimaryDocument(cik, form, filing_document_url(cik, accession, document))


def _parse_index_page(html: str) -> tuple[str | None, str]:
    soup = BeautifulSoup(html, "html.parser")
    form = ""
    form_div = soup.find(id="formName")
    if form_div:
        found = re.search(r"Form\s+(\S+)", form_div.get_text(" ", strip=True))
        form = found.group(1) if found else ""
    for table in soup.find_all("table", class_="tableFile"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 4 or cells[0].get_text(strip=True) != "1":
                continue
            link = cells[2].find("a")
            name = (link.get_text(strip=True) if link else cells[2].get_text(strip=True)) or None
            return name, cells[3].get_text(strip=True) or form
    return None, form


def find_primary_document(
    client: SupportsGet, accession: str, cik: str | None = None
) -> PrimaryDocument:
    acc = normalize_accession(accession)
    cik_inferred = cik is None or str(cik).strip() == ""
    cik10 = normalize_cik(acc[:10] if cik_inferred else str(cik))
    found = _from_submissions(client, cik10, acc)
    return found or _from_index(client, cik10, acc, cik_inferred)


# ---------------------------------------------------------------- public entry point


def get_filing_section(
    client: SupportsGet,
    accession: str,
    section: str,
    cik: str | None = None,
    max_chars: int = 20000,
) -> FilingSection:
    if max_chars < 1:
        raise EdgarError("max_chars must be a positive integer")
    acc = normalize_accession(accession)
    doc = find_primary_document(client, acc, cik)
    if not (doc.form.upper().startswith("10-K") or _is_10q(doc.form)):
        raise EdgarError(
            f"filing {acc} is a {doc.form}; section extraction supports 10-K and 10-Q filings only"
        )
    target = normalize_section(section, doc.form)
    raw = client.get_text(doc.url, ttl=None)
    text = raw if doc.url.lower().endswith(".txt") else html_to_text(raw)
    found = extract_section(text, target)
    if found is None:
        raise EdgarError(
            f"Section {section!r} (Item {target.key}) was not found in {doc.form} {acc} "
            f"({doc.url}). Accepted: {accepted_section_names(doc.form)}."
        )
    return FilingSection(
        accession=acc,
        cik=doc.cik,
        form=doc.form,
        section=target.key,
        title=found.title,
        text=found.text[:max_chars],
        char_count=len(found.text),
        truncated=len(found.text) > max_chars,
        source_url=doc.url,
    )
