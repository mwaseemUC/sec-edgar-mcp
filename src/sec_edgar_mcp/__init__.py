"""SEC EDGAR as typed MCP tools: company search, filing lists, XBRL facts, 10-K sections."""

from .client import EdgarClient, EdgarError, default_client

__all__ = ["EdgarClient", "EdgarError", "default_client", "__version__"]
__version__ = "0.1.0"
