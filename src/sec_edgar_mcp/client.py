"""Polite SEC EDGAR HTTP client: User-Agent, process-wide rate limit, retries, disk cache."""

from __future__ import annotations

import email.utils
import json
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .cache import DiskCache, default_cache_dir
from .ratelimit import DEFAULT_RATE, TokenBucket, process_limiter

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

TTL_TICKERS = 24 * 3600.0
TTL_SUBMISSIONS = 3600.0
TTL_COMPANYFACTS = 24 * 3600.0
TTL_ARCHIVES: float | None = None

MAX_ATTEMPTS = 4
MAX_RETRY_AFTER = 60.0
TIMEOUT_SECONDS = 30.0
THROTTLE_MARKER = b"Request Rate Threshold Exceeded"

_USER_AGENT_RE = re.compile(r"^\S.*\s\S+@\S+\.\S+$")
_ACCESSION_RE = re.compile(r"^(\d{10})-?(\d{2})-?(\d{6})$")


class EdgarError(Exception):
    """A user-facing failure. Messages never contain secrets."""


def validate_user_agent(value: str | None) -> str:
    agent = (value or "").strip()
    if not agent:
        raise EdgarError(
            "SEC_USER_AGENT is not set. SEC requires a User-Agent with your name and email, "
            'e.g. SEC_USER_AGENT="Jane Doe jane@example.com" (set it in the environment or .env).'
        )
    if not _USER_AGENT_RE.match(agent):
        raise EdgarError(
            'SEC_USER_AGENT is malformed: expected "Name email@domain", '
            'e.g. "Jane Doe jane@example.com".'
        )
    return agent


def rate_from_env() -> float:
    raw = os.environ.get("SEC_RATE_LIMIT", "").strip()
    if not raw:
        return DEFAULT_RATE
    try:
        rate = float(raw)
    except ValueError as exc:
        raise EdgarError(f"SEC_RATE_LIMIT must be a number (requests/second), got {raw!r}") from exc
    if not 0 < rate < 10:
        raise EdgarError("SEC_RATE_LIMIT must be greater than 0 and below SEC's limit of 10")
    return rate


def normalize_cik(cik: str | int) -> str:
    """'320193', '0000320193', 'CIK320193' or 320193 -> '0000320193'."""
    text = str(cik).strip()
    if text.upper().startswith("CIK"):
        text = text[3:].strip()
    if not text.isdigit() or len(text) > 10 or int(text) == 0:
        raise EdgarError(
            f"invalid CIK {str(cik)!r}: expected up to 10 digits such as '320193'. "
            "Use search_company to look up a CIK from a company name or ticker."
        )
    return text.zfill(10)


def normalize_accession(accession: str) -> str:
    """'000032019323000106' or '0000320193-23-000106' -> '0000320193-23-000106'."""
    match = _ACCESSION_RE.match(accession.strip())
    if not match:
        raise EdgarError(
            f"invalid accession number {accession!r}: expected the form 0000320193-23-000106"
        )
    return "-".join(match.groups())


def submissions_url(cik: str | int) -> str:
    return f"https://data.sec.gov/submissions/CIK{normalize_cik(cik)}.json"


def companyfacts_url(cik: str | int) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalize_cik(cik)}.json"


def archives_folder_url(cik: str | int, accession: str) -> str:
    acc = normalize_accession(accession).replace("-", "")
    return f"{ARCHIVES_BASE}/{int(normalize_cik(cik))}/{acc}"


def filing_index_url(cik: str | int, accession: str) -> str:
    acc = normalize_accession(accession)
    return f"{archives_folder_url(cik, acc)}/{acc}-index.htm"


def filing_document_url(cik: str | int, accession: str, document: str) -> str:
    return f"{archives_folder_url(cik, accession)}/{document.lstrip('/')}"


def _retry_after_seconds(header: str | None, now: datetime | None = None) -> float | None:
    if not header:
        return None
    header = header.strip()
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


class EdgarClient:
    """Sync client for SEC endpoints. Every request sends the SEC_USER_AGENT User-Agent."""

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        cache_dir: Path | None = None,
        rate_limit: float | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = validate_user_agent(
            user_agent if user_agent is not None else os.environ.get("SEC_USER_AGENT")
        )
        self.cache = DiskCache(cache_dir if cache_dir is not None else default_cache_dir())
        self._sleep = sleep
        if rate_limit is None and sleep is time.sleep and clock is time.monotonic:
            self.limiter = process_limiter(rate_from_env())
        else:
            rate = rate_limit if rate_limit is not None else rate_from_env()
            self.limiter = TokenBucket(rate, clock=clock, sleep=sleep)
        self._http = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(self, url: str, *, ttl: float | None) -> Any:
        """GET a JSON document; ``ttl`` is the cache lifetime in seconds (None = forever)."""
        cached = self.cache.get(url, ttl)
        if cached is not None:
            try:
                return json.loads(cached)
            except ValueError:
                pass
        body = self._fetch(url)
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise EdgarError(f"SEC returned invalid JSON for {url}") from exc
        self.cache.put(url, body)
        return data

    def get_text(self, url: str, *, ttl: float | None) -> str:
        """GET a text/HTML document; ``ttl`` is the cache lifetime in seconds (None = forever)."""
        cached = self.cache.get(url, ttl)
        if cached is not None:
            return _decode(cached)
        body = self._fetch(url)
        self.cache.put(url, body)
        return _decode(body)

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        delay = retry_after if retry_after is not None else 2.0**attempt
        return min(delay, MAX_RETRY_AFTER)

    def _fetch(self, url: str) -> bytes:
        for attempt in range(MAX_ATTEMPTS):
            last = attempt == MAX_ATTEMPTS - 1
            self.limiter.acquire()
            try:
                response = self._http.get(url)
            except httpx.TransportError as exc:
                if last:
                    raise EdgarError(f"network error fetching {url}: {type(exc).__name__}") from exc
                self._sleep(self._backoff(attempt, None))
                continue
            status = response.status_code
            if status == 200 and THROTTLE_MARKER in response.content:
                status = 429
            if status == 200:
                return response.content
            if status == 404:
                raise EdgarError(f"not found: {url}")
            if status == 429 or status >= 500:
                if last:
                    raise EdgarError(
                        f"SEC returned HTTP {status} for {url} after {MAX_ATTEMPTS} attempts"
                    )
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                self._sleep(self._backoff(attempt, retry_after))
                continue
            hint = " (SEC rejects requests without a valid User-Agent)" if status == 403 else ""
            raise EdgarError(f"SEC returned HTTP {status} for {url}{hint}")
        raise EdgarError(f"could not fetch {url}")


_default: EdgarClient | None = None
_default_lock = threading.Lock()


def default_client() -> EdgarClient:
    """Process-wide client configured from the environment (and .env if present)."""
    global _default
    with _default_lock:
        if _default is None:
            from dotenv import load_dotenv

            load_dotenv(override=False)
            _default = EdgarClient()
        return _default
