"""Error responses and failure logging. Every error body carries the trace id that is also in the `X-Trace-Id`
response header (FR-12), and every failure leaves one log line with that trace id and the exception CLASS only: never
the message, which can carry SQL, parameters, hosts or credentials (NFR-3)."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("cohortlens.api")

# SQLSTATEs (PostgreSQL appendix A) that mean the database could not be reached or is shutting down. A driver error
# with no SQLSTATE at all (connection refused, timed out, DNS failure) is the same kind of failure.
UNAVAILABLE_STATES = {"57P01", "57P02", "57P03", "53300", "53400"}
TOO_SLOW_STATES = {"57014", "55P03"}  # statement cancelled by statement_timeout, lock not available


def trace_id_of(request: Request) -> str:
    return getattr(request.state, "trace_id", "")


def log_failure(request: Request, exc: BaseException) -> None:
    logger.error("request failed exception=%s trace_id=%s", type(exc).__name__, trace_id_of(request))


def error_response(
    request: Request, status: int, message: str, errors: list[dict[str, str]] | None = None,
    extra: dict | None = None,
):
    """`{"error": message, "trace_id": ..., "errors": [{"field", "message"}]}` plus any `extra` keys (for example the id
    of the failed run); messages never echo submitted data."""
    body: dict = {"error": message, "trace_id": trace_id_of(request)}
    if errors is not None:
        body["errors"] = errors
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status)


def database_error_response(request: Request, exc: Exception, extra: dict | None = None):
    """Map a database exception to a response and log it. Only connection-level failures are "unavailable"; a
    statement timeout says it took too long; anything else (a bad statement, a missing table) is a plain 500."""
    log_failure(request, exc)
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate in TOO_SLOW_STATES:
        return error_response(request, 503, "The database took too long to respond. Please try again.", extra=extra)
    if sqlstate is None or sqlstate.startswith("08") or sqlstate in UNAVAILABLE_STATES:
        return error_response(request, 503, "The database is unavailable. Please try again.", extra=extra)
    return error_response(request, 500, "Internal Server Error", extra=extra)
