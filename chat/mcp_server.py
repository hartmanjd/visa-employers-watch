"""The chat's lookups as a read-only MCP server, so Claude Desktop and other MCP clients can use them.

Mounted by web/api.py at /mcp. It speaks MCP's Streamable HTTP transport in its simplest form: each
POST carries one JSON-RPC message (or a list of them) and gets a plain JSON reply. There are no sessions
and no server-sent events, because every tool is a single read-only database lookup. The tools, their
descriptions and their input schemas are the ones in chat/tools.py.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import tools
from .engine import SITE_URL

# Newest first; a client asking for a version not listed here gets the newest.
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "visa-employer-watch", "title": "Visa Employer Watch", "version": "1.0.0"}

INSTRUCTIONS = f"""Lookups over U.S. employers that filed H-1B or H-2B visa applications in the fiscal years the data covers (data_overview lists them), the Department of Labor Wage and Hour Division (WHD) enforcement cases linked to them, and the OSHA safety and health inspections linked to them. Answer only from what the tools return.

When you mention a record, link it to the website so readers can check it:
- employer: {SITE_URL}/#/employer/<employer_id>
- application: {SITE_URL}/#/application/<case_number>
- WHD case: the page of the employer it was returned under
- OSHA inspection: its osha_url (OSHA's own page), and the page of the employer it was returned under

What the data means:
{tools.DATA_NOTES}"""

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def tool_list():
    return [{"name": t["name"], "title": t["name"].replace("_", " ").capitalize(), "description": t["description"],
             "inputSchema": t["input_schema"],
             "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                             "openWorldHint": False}}
            for t in tools.TOOLS]


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def call_tool(params):
    """Run one tool. Bad arguments come back as a tool result with isError, so the model can retry."""
    name, args = params.get("name"), params.get("arguments") or {}
    if name not in tools.RUNNERS:
        raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "arguments must be an object")
    con = tools.q.connect()
    try:
        result = tools.run_tool(con, name, args)
    except (LookupError, ValueError, TypeError) as e:
        return {"content": [{"type": "text", "text": str(e) or type(e).__name__}], "isError": True}
    finally:
        con.close()
    return {"content": [{"type": "text", "text": json.dumps(result, default=str)}], "isError": False}


def handle(method, params):
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tool_list()}
    if method == "tools/call":
        return call_tool(params)
    raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def respond(msg):
    """The reply to one JSON-RPC message, or None for a notification or a client's response."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "Invalid request"}}
    if "method" not in msg or "id" not in msg:
        return None
    params = msg.get("params") or {}
    try:
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        return {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg["method"], params)}
    except RpcError as e:
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": e.code, "message": str(e)}}


router = APIRouter()


@router.post("/mcp")
async def mcp_post(request: Request):
    try:
        body = json.loads(await request.body())
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "Parse error"}},
                            status_code=400)
    if isinstance(body, list):
        replies = [r for r in (respond(m) for m in body) if r is not None]
    else:
        replies = respond(body)
    if not replies:
        return Response(status_code=202)  # only notifications or responses: nothing to send back
    return JSONResponse(replies)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other():
    """No server-sent event stream and no sessions to end."""
    return Response(status_code=405, headers={"Allow": "POST"})
