"""Tiny stdio MCP server used only in unit tests (no network)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agentos-fixture-echo")


@mcp.tool()
def echo_info(message: str) -> str:
    """Echo a message back (read-only fixture)."""

    return f"echo:{message.strip()}"


@mcp.tool()
def blocked_tool(message: str) -> str:
    """Should be filtered out by allowlist in tests."""

    return f"blocked:{message}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
