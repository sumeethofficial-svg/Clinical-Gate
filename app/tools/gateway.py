"""Single entry point for every tool call (agent loop, API, eval harness).

Order of checks: role -> argument tampering -> schema validation (FastMCP) -> DB-enforced execution -> audit.
Every outcome, allowed or denied, is written to the audit log; an audit failure withholds the data.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client

from app.auth.context import identity_scope
from app.auth.tokens import Identity
from app.db.audit import write_audit
from app.policy.rules import PolicyDenied, check_role, looks_like_identity_arg, tools_for
from app.tools.server import mcp


@dataclass
class ToolResult:
    tool: str
    args: dict
    ok: bool
    decision: str               # allow | deny | error
    reason: str | None = None
    payload: dict = field(default_factory=dict)

    def to_model_json(self) -> str:
        """Exactly what the LLM is allowed to see."""
        body = {"ok": self.ok, "decision": self.decision}
        if self.ok:
            body["data"] = self.payload.get("data")
        else:
            body["reason"] = self.reason
            body["message"] = self.payload.get("message", "Access denied.")
        return json.dumps(body, default=str)


_schema_cache: dict[str, dict] | None = None


async def _schemas() -> dict[str, dict]:
    global _schema_cache
    if _schema_cache is None:
        async with Client(mcp) as c:
            _schema_cache = {t.name: t.input_schema for t in await c.list_tools()}
    return _schema_cache


def tool_specs(role: str) -> list[dict]:
    """Tool definitions advertised to the model for this role (identity-free schemas)."""
    async def _go():
        async with Client(mcp) as c:
            return await c.list_tools()
    tools = asyncio.run(_go())
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in tools if t.name in tools_for(role)]


def _deny(identity: Identity, tool: str, args: dict, reason: str, message: str) -> ToolResult:
    write_audit(identity, tool, args, "deny", reason, 0)
    return ToolResult(tool, args, False, "deny", reason, {"message": message})


async def _call(identity: Identity, tool: str, args: dict) -> ToolResult:
    schemas = await _schemas()
    if tool not in schemas:
        return _deny(identity, tool, args, "unknown_tool", "Unknown tool.")
    try:
        check_role(tool, identity.role)
    except PolicyDenied as e:
        return _deny(identity, tool, args, e.reason, e.message)
    if not isinstance(args, dict):
        return _deny(identity, tool, {"_raw": str(args)}, "invalid_arguments", "Arguments must be an object.")
    allowed = set(schemas[tool].get("properties", {}))
    extra = [k for k in args if k not in allowed]
    if extra:
        tamper = any(looks_like_identity_arg(k) for k in extra)
        reason = "argument_tampering" if tamper else "unexpected_argument"
        return _deny(identity, tool, args, reason, "Arguments not accepted by this tool: " + ", ".join(sorted(extra))[:100])
    try:
        with identity_scope(identity):
            async with Client(mcp) as c:
                res = await c.call_tool(tool, args)
    except Exception as e:
        if "audit_unavailable" in str(e):
            return ToolResult(tool, args, False, "error", "audit_unavailable", {"message": "Request could not be recorded; no data returned."})
        return _deny(identity, tool, args, "invalid_arguments", "Arguments failed validation.")
    body = res.structured_content if getattr(res, "structured_content", None) else json.loads(res.content[0].text)
    if isinstance(body, dict) and "result" in body and len(body) == 1:
        body = body["result"]
    return ToolResult(tool, args, bool(body.get("ok")), body.get("decision", "error"), body.get("reason"), body)


def call_tool(identity: Identity, tool: str, args: dict) -> ToolResult:
    """Synchronous facade. Fail closed: any unexpected failure returns an error result with no data."""
    try:
        return asyncio.run(_call(identity, tool, args))
    except Exception as e:  # pragma: no cover - defensive
        if "audit_unavailable" in repr(e):
            return ToolResult(tool, args if isinstance(args, dict) else {}, False, "error", "audit_unavailable",
                              {"message": "Request could not be recorded; no data returned."})
        return ToolResult(tool, args if isinstance(args, dict) else {}, False, "error", "gateway_error",
                          {"message": "The request could not be completed."})
