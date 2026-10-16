from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp_types import CallToolResult

from sec_edgar_mcp import server as server_module
from sec_edgar_mcp.client import EdgarClient, EdgarError, validate_user_agent
from sec_edgar_mcp.server import build_server
from tests.edgar_helpers import load_fixture, make_client

AAPL_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
TOOLS = {"search_company", "list_filings", "get_financial_facts", "get_filing_section"}


def call(client_factory: Any, tool: str, arguments: dict[str, Any]) -> CallToolResult:
    async def run() -> CallToolResult:
        async with Client(build_server(client_factory)) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def test_lists_the_four_tools_with_schemas() -> None:
    async def run() -> Any:
        async with Client(build_server(lambda: None)) as client:
            return await client.list_tools()

    listed = asyncio.run(run()).tools
    assert {tool.name for tool in listed} == TOOLS
    for tool in listed:
        assert tool.description and "source_url" in tool.description
        assert tool.output_schema is not None
        assert tool.annotations is not None and tool.annotations.read_only_hint
    section = next(t for t in listed if t.name == "get_filing_section")
    assert "untrusted" in (section.description or "")
    facts_tool = next(t for t in listed if t.name == "get_financial_facts")
    assert "revenue" in str(facts_tool.input_schema["properties"]["concept"])


@respx.mock
def test_get_financial_facts_returns_structured_output(tmp_path: Path) -> None:
    respx.get(AAPL_FACTS).mock(
        return_value=httpx.Response(200, json=load_fixture("companyfacts_CIK0000320193.json"))
    )
    client = make_client(tmp_path)
    result = call(
        lambda: client, "get_financial_facts", {"cik": "320193", "concept": "revenue", "years": 2}
    )
    assert not result.is_error
    data = result.structured_content
    assert data["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert data["source_url"] == AAPL_FACTS
    assert [(v["fiscal_year"], v["value"]) for v in data["values"]] == [
        (2025, 416161000000.0),
        (2024, 391035000000.0),
    ]


def test_domain_errors_reach_the_model(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    result = call(lambda: client, "list_filings", {"cik": "AAPL"})
    assert result.is_error
    assert "invalid CIK 'AAPL'" in result.content[0].text


def test_missing_user_agent_is_a_clear_tool_error() -> None:
    def factory() -> EdgarClient:
        validate_user_agent(None)
        raise AssertionError("unreachable")

    result = call(factory, "search_company", {"name": "apple"})
    assert result.is_error
    assert "SEC_USER_AGENT is not set" in result.content[0].text


def test_argument_validation_happens_before_any_request() -> None:
    def factory() -> EdgarClient:
        raise AssertionError("client must not be built")

    result = call(factory, "search_company", {"name": "apple", "limit": 0})
    assert result.is_error


def test_get_filing_section_delegates_to_sections_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sec_edgar_mcp import sections

    seen: dict[str, Any] = {}

    def fake(client: Any, accession: str, section: str, cik: Any, max_chars: int) -> Any:
        seen.update(accession=accession, section=section, cik=cik, max_chars=max_chars)
        raise EdgarError("section not found: 99")

    monkeypatch.setattr(sections, "get_filing_section", fake)
    client = make_client(tmp_path)
    result = call(
        lambda: client,
        "get_filing_section",
        {"accession": "0000320193-23-000106", "section": "1A", "cik": "320193"},
    )
    assert result.is_error and "section not found" in result.content[0].text
    assert seen == {
        "accession": "0000320193-23-000106",
        "section": "1A",
        "cik": "320193",
        "max_chars": 20000,
    }


def test_main_runs_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda **_: False)
    monkeypatch.setattr(
        server_module.MCPServer, "run", lambda self, transport: ran.append(transport)
    )
    server_module.main()
    assert ran == ["stdio"]
