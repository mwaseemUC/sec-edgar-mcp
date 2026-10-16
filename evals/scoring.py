"""Pure scoring functions for the sec-edgar-mcp eval (no network, no MCP)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

# USD per million tokens: (input, output). Cache writes bill at 1.25x input (5-minute TTL),
# cache reads at 0.1x input. Source: claude-api skill model table, cached 2026-10-06.
PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

STRING_UNITS = {"accession", "date"}

_SCALE_WORDS = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "tn": 1e12,
    "trillion": 1e12,
}
_NUMBER_RE = re.compile(
    r"(?P<neg>[-−–])?\s*\$?\s*"
    r"(?P<num>(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?P<scale>[A-Za-z]+)?",
)
_YEAR_RE = re.compile(r"(?:19|20)\d\d")
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_ACCESSION_RE = re.compile(r"^\d{10}-?\d{2}-?\d{6}$")
_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y/%m/%d", "%d %B %Y")


def parse_number(raw: Any) -> float | None:
    """Parse 383285000000, "383,285", "$383.3B", "383.3 billion", "-52.7%", "(52.7)%", "3.8e11".

    In mixed text, the part after the last colon wins ("FY2023: $383.3B") and bare four-digit
    years are skipped when another number is present ("383.3B in FY2023").
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if ":" in text and _NUMBER_RE.search(text.rsplit(":", 1)[1]):
        text = text.rsplit(":", 1)[1].strip()
    negative = text.startswith("(") and ")" in text
    match = _pick_number(list(_NUMBER_RE.finditer(text.replace("(", "").replace(")", ""))))
    if not match:
        return None
    value = float(match.group("num").replace(",", ""))
    scale = (match.group("scale") or "").lower()
    if scale in _SCALE_WORDS:
        value *= _SCALE_WORDS[scale]
    if match.group("neg") or negative:
        value = -value
    return value


def _pick_number(matches: list[re.Match[str]]) -> re.Match[str] | None:
    def is_year(m: re.Match[str]) -> bool:
        scale = (m.group("scale") or "").lower()
        return bool(_YEAR_RE.fullmatch(m.group("num"))) and scale not in _SCALE_WORDS

    non_years = [m for m in matches if not is_year(m)]
    return (non_years or matches or [None])[0]


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} substring, in order, respecting JSON strings."""
    found: list[str] = []
    depth, start, in_str, escape = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                found.append(text[start : i + 1])
    return found


def extract_answer_values(text: str) -> dict[str, Any] | None:
    """The `values` dict from the last JSON object with a "values" key (fenced block preferred)."""
    candidates = [blob for block in _FENCE_RE.findall(text) for blob in _balanced_objects(block)]
    candidates += _balanced_objects(text)
    for blob in reversed(candidates):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("values"), dict):
            return data["values"]
    return None


def _norm_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def lookup_label(values: Mapping[str, Any], label: str) -> Any:
    if label in values:
        return values[label]
    wanted = _norm_label(label)
    for key, value in values.items():
        if _norm_label(key) == wanted:
            return value
    return None


def parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_accession(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not _ACCESSION_RE.match(text):
        return None
    return text.replace("-", "")


def value_matches(expected: Mapping[str, Any], got: Any) -> bool:
    """True if `got` is within the expected item's tolerance (or equal, for string units)."""
    unit = expected.get("unit")
    if unit == "accession":
        got_acc = _normalize_accession(got)
        return got_acc is not None and got_acc == _normalize_accession(expected["value"])
    if unit == "date":
        got_date = parse_date(got)
        return got_date is not None and got_date == parse_date(expected["value"])
    number = parse_number(got)
    if number is None:
        return False
    return within_tolerance(expected, number)


def within_tolerance(expected: Mapping[str, Any], number: float) -> bool:
    target = float(expected["value"])
    if "abs_tolerance" in expected:
        return abs(number - target) <= float(expected["abs_tolerance"]) + 1e-9
    tolerance = abs(target) * float(expected.get("tolerance_pct", 0.5)) / 100
    return abs(number - target) <= tolerance + 1e-9


def growth_pct(old: float, new: float) -> float:
    return (new / old - 1) * 100


def extract_urls(text: str) -> list[str]:
    return [url.rstrip(".,;:!?*_") for url in _URL_RE.findall(text)]


def url_key(url: str) -> tuple[str, str] | None:
    """(host, path) with host lowercased and trailing slash dropped; None for non-sec.gov URLs."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host != "sec.gov" and not host.endswith(".sec.gov"):
        return None
    return host, parts.path.rstrip("/")


def collect_source_urls(obj: Any) -> set[str]:
    """Every string under a key ending in `_url` (source_url, document_url, index_url, ...)."""
    urls: set[str] = set()
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if isinstance(key, str) and key.endswith("_url") and isinstance(value, str):
                urls.add(value)
            else:
                urls |= collect_source_urls(value)
    elif isinstance(obj, list | tuple):
        for item in obj:
            urls |= collect_source_urls(item)
    return urls


EVIDENCE_PATH_PREFIXES = ("/archives/edgar/", "/api/xbrl/", "/submissions/")


def is_evidence_url(key: tuple[str, str]) -> bool:
    """Filing documents/indexes, XBRL facts and submissions; not the company_tickers.json lookup."""
    path = key[1].lower() + "/"
    return path.startswith(EVIDENCE_PATH_PREFIXES) and "company_tickers" not in path


def is_cited(answer: str, tool_urls: Iterable[str]) -> bool:
    """At least one sec.gov evidence URL in the answer whose host+path a tool returned."""
    known = {key for url in tool_urls if (key := url_key(url)) and is_evidence_url(key)}
    return any(url_key(url) in known for url in extract_urls(answer) if url_key(url))


def cost_usd(usage: Mapping[str, int], model: str) -> float | None:
    prices = PRICING.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    total = (
        usage.get("input_tokens", 0) * price_in
        + usage.get("cache_creation_input_tokens", 0) * price_in * CACHE_WRITE_MULTIPLIER
        + usage.get("cache_read_input_tokens", 0) * price_in * CACHE_READ_MULTIPLIER
        + usage.get("output_tokens", 0) * price_out
    )
    return total / 1_000_000


@dataclass
class ValueCheck:
    label: str
    unit: str
    expected: Any
    got: Any
    correct: bool


@dataclass
class QuestionScore:
    id: str
    correct: bool
    grounded_correct: bool
    partial: float
    cited: bool
    used_expected_tools: bool
    tool_calls: int
    checks: list[ValueCheck] = field(default_factory=list)
    answer_parsed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_question(
    golden: Mapping[str, Any],
    answer: str,
    tools_called: Sequence[str],
    tool_urls: Iterable[str],
    tool_outputs: Sequence[Any] = (),
) -> QuestionScore:
    """Score one answer. `tools_called`, `tool_urls` and `tool_outputs` must cover only tool
    calls that returned without is_error."""
    values = extract_answer_values(answer)
    checks = []
    for item in golden["expected"]:
        got = lookup_label(values, item["label"]) if values else None
        checks.append(
            ValueCheck(item["label"], item["unit"], item["value"], got, value_matches(item, got))
        )
    n_correct = sum(c.correct for c in checks)
    correct = bool(checks) and n_correct == len(checks)
    used_tools = set(golden.get("expected_tools", [])) <= set(tools_called)
    in_outputs = bool(tool_outputs) and all(
        oracle_check(item, tool_outputs)[0] for item in golden["expected"]
    )
    return QuestionScore(
        id=golden["id"],
        correct=correct,
        grounded_correct=correct and used_tools and in_outputs,
        partial=n_correct / len(checks) if checks else 0.0,
        cited=is_cited(answer, tool_urls),
        used_expected_tools=used_tools,
        tool_calls=len(tools_called),
        checks=checks,
        answer_parsed=values is not None,
    )


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summary over per-question rows (QuestionScore dicts plus usage/cost/latency keys)."""
    n = len(rows)
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    usage_keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    tokens = {k: sum(r.get("usage", {}).get(k, 0) for r in rows) for k in usage_keys}
    return {
        "questions": n,
        "grounded_correct": sum(bool(r.get("grounded_correct")) for r in rows),
        "accuracy_grounded": _mean([float(bool(r.get("grounded_correct"))) for r in rows]),
        "correct_strict": sum(bool(r["correct"]) for r in rows),
        "accuracy_strict": _mean([float(bool(r["correct"])) for r in rows]),
        "accuracy_partial": _mean([float(r["partial"]) for r in rows]),
        "citation_rate": _mean([float(bool(r["cited"])) for r in rows]),
        "expected_tool_rate": _mean([float(bool(r["used_expected_tools"])) for r in rows]),
        "tool_calls_per_question": _mean([float(r["tool_calls"]) for r in rows]),
        "tokens": tokens,
        "cost_usd": sum(costs) if len(costs) == n and n else None,
        "latency_s_mean": _mean([float(r.get("latency_s", 0.0)) for r in rows]),
        "latency_s_total": sum(float(r.get("latency_s", 0.0)) for r in rows),
    }


def numbers_in(obj: Any) -> list[float]:
    """Every number in a tool output: JSON numbers plus numerals inside strings (commas allowed)."""
    found: list[float] = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, int | float):
        found.append(float(obj))
    elif isinstance(obj, str):
        found += [float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", obj)]
    elif isinstance(obj, Mapping):
        for value in obj.values():
            found += numbers_in(value)
    elif isinstance(obj, list | tuple):
        for item in obj:
            found += numbers_in(item)
    return found


def strings_in(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, Mapping):
        return [s for value in obj.values() for s in strings_in(value)]
    if isinstance(obj, list | tuple):
        return [s for item in obj for s in strings_in(item)]
    return []


def oracle_check(expected: Mapping[str, Any], outputs: Sequence[Any]) -> tuple[bool, str]:
    """Dry-run check: can the expected value be read (or derived) from the raw tool outputs?"""
    blob = " ".join(strings_in(list(outputs)))
    if "evidence" in expected:
        ok = re.sub(r"\s+", " ", expected["evidence"]).lower() in re.sub(r"\s+", " ", blob).lower()
        return ok, f"evidence {'found' if ok else 'missing'}: {expected['evidence']!r}"
    if expected["unit"] in STRING_UNITS:
        ok = str(expected["value"]) in blob
        return ok, f"{expected['value']} {'found' if ok else 'missing'}"
    numbers = numbers_in(list(outputs))
    derived = expected.get("derived")
    if derived:
        old, new = float(derived["from"]), float(derived["to"])
        have = old in numbers and new in numbers
        ok = have and within_tolerance(expected, round(growth_pct(old, new), 1))
        return ok, f"inputs {old:g} and {new:g} {'found' if have else 'missing'}"
    ok = any(within_tolerance(expected, n) for n in numbers)
    return ok, f"{expected['value']} {'found' if ok else 'missing'}"
