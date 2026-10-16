from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from sec_edgar_mcp import client as client_module
from sec_edgar_mcp.cache import DiskCache
from sec_edgar_mcp.client import (
    TICKERS_URL,
    EdgarClient,
    EdgarError,
    _retry_after_seconds,
    companyfacts_url,
    default_client,
    filing_document_url,
    filing_index_url,
    normalize_accession,
    normalize_cik,
    rate_from_env,
    submissions_url,
    validate_user_agent,
)
from tests.edgar_helpers import USER_AGENT, FakeTime, make_client

URL = "https://data.sec.gov/submissions/CIK0000320193.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("320193", "0000320193"),
        ("0000320193", "0000320193"),
        (320193, "0000320193"),
        (" CIK320193 ", "0000320193"),
        ("cik0001045810", "0001045810"),
        ("1", "0000000001"),
    ],
)
def test_normalize_cik(raw: str | int, expected: str) -> None:
    assert normalize_cik(raw) == expected


@pytest.mark.parametrize("raw", ["", "AAPL", "12345678901", "0", "32-0193", "-5", "3.2"])
def test_normalize_cik_rejects(raw: str) -> None:
    with pytest.raises(EdgarError, match="invalid CIK"):
        normalize_cik(raw)


def test_normalize_accession() -> None:
    assert normalize_accession("000032019323000106") == "0000320193-23-000106"
    assert normalize_accession(" 0000320193-23-000106 ") == "0000320193-23-000106"
    with pytest.raises(EdgarError, match="invalid accession"):
        normalize_accession("0000320193-23")


def test_url_builders() -> None:
    assert submissions_url("320193") == URL
    assert companyfacts_url(320193) == (
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    )
    assert filing_index_url("0000320193", "000032019323000106") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/"
        "0000320193-23-000106-index.htm"
    )
    assert filing_document_url("320193", "0000320193-23-000106", "aapl-20230930.htm") == DOC_URL


@pytest.mark.parametrize("agent", ["Jane Doe jane@example.com", "Acme Research ops@acme.co.uk"])
def test_validate_user_agent_accepts(agent: str) -> None:
    assert validate_user_agent(agent) == agent


@pytest.mark.parametrize("agent", [None, "", "   ", "Jane Doe", "jane@example.com", "Jane jane@x"])
def test_validate_user_agent_rejects(agent: str | None) -> None:
    with pytest.raises(EdgarError, match="SEC_USER_AGENT"):
        validate_user_agent(agent)


@respx.mock
def test_missing_user_agent_errors_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(EdgarError, match="SEC_USER_AGENT is not set"):
        EdgarClient(cache_dir=tmp_path)
    assert not route.called


def test_user_agent_read_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", USER_AGENT)
    assert EdgarClient(cache_dir=tmp_path, rate_limit=5).user_agent == USER_AGENT


def test_error_message_never_echoes_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "secret-contact-no-email")
    with pytest.raises(EdgarError) as info:
        EdgarClient()
    assert "secret-contact" not in str(info.value)


@respx.mock
def test_every_request_sends_user_agent_and_gzip(tmp_path: Path) -> None:
    route = respx.get(url__startswith="https://").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = make_client(tmp_path)
    client.get_json(URL, ttl=0)
    client.get_json(TICKERS_URL, ttl=0)
    client.get_text(DOC_URL, ttl=None)
    assert route.call_count == 3
    for call in route.calls:
        assert call.request.headers["User-Agent"] == USER_AGENT
        assert call.request.headers["Accept-Encoding"] == "gzip, deflate"


def test_timeout_is_30_seconds(tmp_path: Path) -> None:
    timeout = make_client(tmp_path)._http.timeout
    assert timeout.read == 30.0 and timeout.connect == 30.0


@respx.mock
def test_404_maps_to_not_found(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(EdgarError, match=f"not found: {URL}"):
        make_client(tmp_path).get_json(URL, ttl=0)


@respx.mock
def test_403_hints_at_user_agent(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(403))
    with pytest.raises(EdgarError, match="User-Agent"):
        make_client(tmp_path).get_json(URL, ttl=0)


@respx.mock
def test_retries_429_honoring_retry_after(tmp_path: Path) -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"name": "Apple Inc."}),
        ]
    )
    fake = FakeTime()
    assert make_client(tmp_path, fake, rate=1000).get_json(URL, ttl=0) == {"name": "Apple Inc."}
    assert route.call_count == 2
    assert fake.sleeps == [7.0]


@respx.mock
def test_retries_5xx_with_exponential_backoff_then_gives_up(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    fake = FakeTime()
    with pytest.raises(EdgarError, match="HTTP 503 .* after 4 attempts"):
        make_client(tmp_path, fake, rate=1000).get_json(URL, ttl=0)
    assert route.call_count == 4
    assert fake.sleeps == [1.0, 2.0, 4.0]


@respx.mock
def test_throttle_page_with_200_is_retried_and_never_cached(tmp_path: Path) -> None:
    throttle = "<html><h1>Request Rate Threshold Exceeded</h1></html>"
    route = respx.get(DOC_URL).mock(
        side_effect=[httpx.Response(200, text=throttle), httpx.Response(200, text="<p>10-K</p>")]
    )
    fake = FakeTime()
    client = make_client(tmp_path, fake, rate=1000)
    assert client.get_text(DOC_URL, ttl=None) == "<p>10-K</p>"
    assert route.call_count == 2 and fake.sleeps == [1.0]


@respx.mock
def test_persistent_throttle_page_raises_without_caching(tmp_path: Path) -> None:
    respx.get(DOC_URL).mock(
        return_value=httpx.Response(200, text="Request Rate Threshold Exceeded")
    )
    with pytest.raises(EdgarError, match="HTTP 429"):
        make_client(tmp_path, rate=1000).get_text(DOC_URL, ttl=None)
    assert not list(tmp_path.glob("*.body"))


@respx.mock
def test_retry_after_is_capped_at_60s(tmp_path: Path) -> None:
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "600"}),
            httpx.Response(200, json={}),
        ]
    )
    fake = FakeTime()
    make_client(tmp_path, fake, rate=1000).get_json(URL, ttl=0)
    assert fake.sleeps == [60.0]


@respx.mock
def test_transport_errors_are_retried(tmp_path: Path) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"a": 1})]
    )
    fake = FakeTime()
    assert make_client(tmp_path, fake, rate=1000).get_json(URL, ttl=0) == {"a": 1}
    assert route.call_count == 2 and fake.sleeps == [1.0]


@respx.mock
def test_transport_error_on_last_attempt_raises(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(EdgarError, match="network error"):
        make_client(tmp_path, rate=1000).get_json(URL, ttl=0)


def test_retry_after_http_date() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert _retry_after_seconds("Thu, 01 Jan 2026 12:00:05 GMT", now) == 5.0
    assert _retry_after_seconds("garbage", now) is None
    assert _retry_after_seconds(None, now) is None


@respx.mock
def test_invalid_json_raises(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(EdgarError, match="invalid JSON"):
        make_client(tmp_path).get_json(URL, ttl=0)


@respx.mock
def test_get_text_decodes_cp1252_fallback(tmp_path: Path) -> None:
    respx.get(DOC_URL).mock(return_value=httpx.Response(200, content=b"caf\xe9 \x93q\x94"))
    assert make_client(tmp_path).get_text(DOC_URL, ttl=None) == "café “q”"


@respx.mock
def test_cache_hit_avoids_second_request(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"v": 1}))
    client = make_client(tmp_path)
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    assert make_client(tmp_path).get_json(URL, ttl=3600) == {"v": 1}
    assert route.call_count == 1


@respx.mock
def test_cache_ttl_expiry_refetches(tmp_path: Path) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(200, json={"v": 1}), httpx.Response(200, json={"v": 2})]
    )
    wall = [5000.0]
    client = make_client(tmp_path)
    client.cache = DiskCache(tmp_path, now=lambda: wall[0])
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    wall[0] += 3599
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    wall[0] += 2
    assert client.get_json(URL, ttl=3600) == {"v": 2}
    assert route.call_count == 2


@respx.mock
def test_ttl_none_never_expires(tmp_path: Path) -> None:
    route = respx.get(DOC_URL).mock(return_value=httpx.Response(200, text="<p>hi</p>"))
    wall = [0.0]
    client = make_client(tmp_path)
    client.cache = DiskCache(tmp_path, now=lambda: wall[0])
    client.get_text(DOC_URL, ttl=None)
    wall[0] += 10 * 365 * 86400
    assert client.get_text(DOC_URL, ttl=None) == "<p>hi</p>"
    assert route.call_count == 1


@respx.mock
def test_corrupt_cache_body_is_a_miss(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"v": 1}))
    client = make_client(tmp_path)
    client.get_json(URL, ttl=3600)
    for body in tmp_path.glob("*.body"):
        body.write_bytes(b"{truncated")
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    assert route.call_count == 2


@respx.mock
def test_corrupt_cache_metadata_is_a_miss(tmp_path: Path) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"v": 1}))
    client = make_client(tmp_path)
    client.get_json(URL, ttl=3600)
    for meta in tmp_path.glob("*.json"):
        meta.write_text("not json")
    assert client.get_json(URL, ttl=3600) == {"v": 1}
    assert route.call_count == 2


def test_cache_files_store_body_and_metadata(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path, now=lambda: 123.0)
    cache.put(URL, b'{"x":1}')
    metas = list(tmp_path.glob("*.json"))
    assert len(metas) == 1 and len(list(tmp_path.glob("*.body"))) == 1
    meta = json.loads(metas[0].read_text())
    assert meta["url"] == URL and meta["fetched_at"] == 123.0 and meta["status"] == 200
    assert not list(tmp_path.glob("*.tmp"))
    assert cache.get(URL, ttl=None) == b'{"x":1}'
    assert cache.get("https://other", ttl=None) is None


def test_cache_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path / "c"))
    assert EdgarClient(USER_AGENT, rate_limit=5).cache.directory == tmp_path / "c"


@pytest.mark.parametrize(("raw", "rate"), [("", 8.0), ("5", 5.0), ("9.5", 9.5)])
def test_rate_from_env(monkeypatch: pytest.MonkeyPatch, raw: str, rate: float) -> None:
    monkeypatch.setenv("SEC_RATE_LIMIT", raw)
    assert rate_from_env() == rate


@pytest.mark.parametrize("raw", ["0", "10", "50", "fast"])
def test_rate_from_env_rejects(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SEC_RATE_LIMIT", raw)
    with pytest.raises(EdgarError, match="SEC_RATE_LIMIT"):
        rate_from_env()


def test_default_client_is_a_process_singleton_sharing_one_limiter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(client_module, "_default", None)
    monkeypatch.setattr("dotenv.load_dotenv", lambda **_: False)
    monkeypatch.setenv("SEC_USER_AGENT", USER_AGENT)
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path))
    first = default_client()
    assert default_client() is first
    assert EdgarClient(USER_AGENT, cache_dir=tmp_path).limiter is first.limiter
