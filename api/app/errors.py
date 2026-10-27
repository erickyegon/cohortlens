"""Error responses: every error body carries the trace id that is also in the `X-Trace-Id` header (FR-12)."""
from fastapi import Request
from fastapi.responses import JSONResponse


def trace_id_of(request: Request) -> str:
    return getattr(request.state, "trace_id", "")


def error_response(request: Request, status: int, message: str, errors: list[dict[str, str]] | None = None):
    """`{"error": message, "trace_id": ..., "errors": [{"field", "message"}]}`; messages never echo submitted data."""
    body: dict = {"error": message, "trace_id": trace_id_of(request)}
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status)
