"""Shared helpers for the core tests: fixture loading and an offline EdgarClient."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sec_edgar_mcp.client import EdgarClient

FIXTURES = Path(__file__).parent / "fixtures"
USER_AGENT = "Test User test@example.com"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTime:
    """A clock whose sleep() advances it, recording every sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(cache_dir: Path, fake: FakeTime | None = None, rate: float = 8.0) -> EdgarClient:
    fake = fake or FakeTime()
    return EdgarClient(
        USER_AGENT, cache_dir=cache_dir, rate_limit=rate, sleep=fake.sleep, clock=fake.clock
    )
