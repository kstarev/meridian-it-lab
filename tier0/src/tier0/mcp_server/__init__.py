"""tier0's MCP server, an OAuth 2.1 resource server.

Every tool call goes through it (ASI07), and each call is checked against a
verified bearer token. Self-only tools take no user argument: the caller is
always the token's subject (ADR-006).
"""
