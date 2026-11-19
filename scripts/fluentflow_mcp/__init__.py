"""The FluentFlow MCP server's tools and shared machinery.

``scripts/fluentflow_mcp_server.py`` is the entry point agents run; it imports
the tool modules here in a fixed order (that order is the order of
``tools/list``) and answers JSON-RPC. ``core`` holds the connection to the
backend and the hold and heartbeat machinery; each other module holds one
group of tools.
"""
