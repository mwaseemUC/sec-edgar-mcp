from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from sec_edgar_mcp.ratelimit import TokenBucket
from tests.edgar_helpers import FakeTime, make_client


def bucket(rate: float, fake: FakeTime, capacity: float | None = None) -> TokenBucket:
    return TokenBucket(rate, capacity=capacity, clock=fake.clock, sleep=fake.sleep)


def test_default_spacing_is_even() -> None:
    fake = FakeTime()
    limiter = bucket(8, fake)
    assert limiter.acquire() == 0.0
    assert [limiter.acquire() for _ in range(3)] == [pytest.approx(1 / 8)] * 3
    assert fake.sleeps == [pytest.approx(1 / 8)] * 3


def test_burst_up_to_explicit_capacity_then_waits_one_interval() -> None:
    fake = FakeTime()
    limiter = bucket(8, fake, capacity=2)
    assert [limiter.acquire() for _ in range(2)] == [0.0, 0.0]
    assert limiter.acquire() == pytest.approx(1 / 8)


def test_sustained_rate_never_exceeds_limit() -> None:
    fake = FakeTime()
    limiter = bucket(8, fake)
    fake.now += 3600
    stamps = []
    for _ in range(80):
        limiter.acquire()
        stamps.append(fake.now)
    assert stamps[-1] - stamps[0] == pytest.approx(79 / 8)
    assert max(sum(1 for t in stamps if s <= t < s + 1) for s in stamps) <= 8


def test_tokens_refill_with_elapsed_time() -> None:
    fake = FakeTime()
    limiter = bucket(4, fake, capacity=2)
    limiter.acquire()
    limiter.acquire()
    fake.now += 0.25
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == pytest.approx(0.25)


def test_refill_is_capped_at_capacity() -> None:
    fake = FakeTime()
    limiter = bucket(2, fake, capacity=2)
    fake.now += 3600
    assert [limiter.acquire() for _ in range(2)] == [0.0, 0.0]
    assert limiter.acquire() == pytest.approx(0.5)


def test_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0)


@respx.mock
def test_client_requests_go_through_the_limiter(tmp_path: Path) -> None:
    respx.get(url__startswith="https://data.sec.gov/").mock(
        return_value=httpx.Response(200, json={})
    )
    fake = FakeTime()
    client = make_client(tmp_path, fake, rate=2)
    start = fake.now
    for i in range(6):
        client.get_json(f"https://data.sec.gov/x{i}.json", ttl=0)
    assert fake.now - start == pytest.approx(2.5)
