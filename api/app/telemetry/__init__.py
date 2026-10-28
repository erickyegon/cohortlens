"""Traces, metrics and JSON logs that share one trace id, and the two protections that keep data out of them (NFR-3).

- **Spans**: made by hand (no auto-instrumentation, which would add URLs and statements). Every span passes an
  *allowlist* before export: only the few attribute names below survive, and only short values; every event, link and
  status description is dropped (an exception message is the classic way data leaks).
- **Metrics**: `cohortlens_cohort_runs_total` and `cohortlens_cohort_run_duration_seconds`, labelled with a closed set
  of outcomes plus environment and revision.
- **Logs**: one JSON line per record with `trace_id` and `span_id`, and the same record exported over OTLP. Every record
  passes a *redaction* filter first (bearer tokens, API keys, connection strings, emails); an exception is logged as its
  class name only.

Export happens only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (locally the Compose `lgtm` container, in the cloud
Grafana Cloud, with no code change); the standard `OTEL_EXPORTER_OTLP_HEADERS` carries the credentials. With no
endpoint nothing is exported, but trace ids still exist so `X-Trace-Id` always works. Tests call `configure` with
in-memory exporters.
"""
import copy
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import Status

SERVICE_NAME = "cohortlens-api"
LOGGER_NAME = "cohortlens"
OUTCOMES = frozenset({"success", "failed", "timeout"})
MASK = "[REDACTED]"
MAX_ATTRIBUTE_VALUE_LENGTH = 64
DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "_OTHER"})
_ROUTE = re.compile(r"^/[A-Za-z0-9_{}.\-/]*$")
_REVISION = re.compile(r"^(?:dev|[0-9a-f]{7,40})$")


def safe_method(method: str) -> str:
    """The HTTP method is client-controlled, so only a closed set is ever exported."""
    return method if method in HTTP_METHODS else "_OTHER"


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


# The only span attributes that may leave the process, each with the rule its VALUE must satisfy: being on the list is
# not enough, or a client-controlled value (the method, a path) would pass through an allowed key. No name here may
# suggest data (the tests check that).
_VALUE_RULES = {
    "http.request.method": lambda v: isinstance(v, str) and v in HTTP_METHODS,
    "http.route": lambda v: isinstance(v, str) and len(v) <= MAX_ATTRIBUTE_VALUE_LENGTH and bool(_ROUTE.match(v)),
    "http.response.status_code": lambda v: _is_int(v, 100, 599),
    "cohortlens.outcome": lambda v: isinstance(v, str) and v in OUTCOMES,
    "cohortlens.rule_count": lambda v: _is_int(v, 0, 100),
    "cohortlens.step_count": lambda v: _is_int(v, 0, 100),
    "cohortlens.revision": lambda v: isinstance(v, str) and bool(_REVISION.match(v)),
}
ALLOWED_SPAN_ATTRIBUTES = frozenset(_VALUE_RULES)


def environment() -> str:
    return os.environ.get("DEPLOYMENT_ENVIRONMENT", "local")


def revision() -> str:
    return os.environ.get("SERVICE_VERSION", "dev")


# ---- the allowlist span processor ---------------------------------------------------------------------------------

class AllowlistSpanProcessor(SpanProcessor):
    """Wraps another processor and hands it a copy of each ended span with only allowlisted attributes, no events, no
    links and no status description."""

    def __init__(self, delegate: SpanProcessor):
        self._delegate = delegate

    def on_start(self, span, parent_context=None):
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        attributes = {key: value for key, value in (span.attributes or {}).items()
                      if key in _VALUE_RULES and _VALUE_RULES[key](value)}
        status = span.status
        clean = ReadableSpan(
            name=span.name, context=span.context, parent=span.parent, resource=span.resource, attributes=attributes,
            events=(), links=(), kind=span.kind, instrumentation_info=None,
            status=Status(status.status_code) if status is not None else Status(),
            start_time=span.start_time, end_time=span.end_time, instrumentation_scope=span.instrumentation_scope)
        self._delegate.on_end(clean)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


# ---- redaction ------------------------------------------------------------------------------------------------------

MAX_REDACTION_INPUT = 8192
_KEYWORDS = r"(?:api[_-]?key|apikey|token|password|passwd|secret)"
# Every quantifier is bounded, and every pattern starts at a token boundary, so a hostile message costs a few
# milliseconds, not seconds (the first version was quadratic on long input).
_SECRET_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b[a-z][a-z0-9+.\-]{1,20}://[^\s/:@]{1,100}:[^\s@]{1,200}@[^\s]{1,300}",           # URL with credentials
    r"\bbearer\s+[^\s,;\"']{8,200}",                                                       # bearer tokens
    r"\bbasic\s+[a-z0-9+/=]{12,200}",                                                       # basic auth
    r"\beyJ[a-z0-9_\-]{5,200}\.[a-z0-9_\-]{5,200}\.[a-z0-9_\-]{0,200}",                   # JWTs
    r"(?<![a-z0-9])[a-z0-9_\-]{0,40}" + _KEYWORDS + r"[\"']?\s{0,3}[:=]\s{0,3}"
    r"(?:\"[^\"]{1,200}\"|'[^']{1,200}'|[\"']?[^\s,;&\"'}]{1,200})",
    r"\b(?:sk|pk|rk)-[a-z0-9_\-]{16,200}",                                                  # provider-style keys
    r"\b(?:glsa|glc|ghp|gho|ghs|github_pat|xox[abprs])_?[a-z0-9_\-]{16,200}",              # Grafana, GitHub, Slack
    r"[\w.%+\-]{1,64}@[\w.\-]{1,255}\.[a-z]{2,24}",                                         # email addresses
)]


def redact(text: str) -> str:
    """Mask secret-like text. Input beyond `MAX_REDACTION_INPUT` characters is cut and marked, not scanned."""
    if len(text) > MAX_REDACTION_INPUT:
        text = text[:MAX_REDACTION_INPUT] + " [TRUNCATED]"
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """Masks the message of a record, with its %-arguments filled in, before any handler (stdout or OTLP) sees it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a malformed format string must never break logging
            message = str(record.msg)
        record.msg = redact(message if isinstance(message, str) else str(message))
        record.args = None
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line with the current span's trace and span id. An exception becomes its class name only."""

    def format(self, record: logging.LogRecord) -> str:
        context = trace.get_current_span().get_span_context()
        line = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            "service": SERVICE_NAME, "environment": environment(), "revision": revision(),
            "trace_id": format(context.trace_id, "032x") if context.is_valid else "",
            "span_id": format(context.span_id, "016x") if context.is_valid else "",
        }
        if record.exc_info and record.exc_info[0] is not None:
            line["exception"] = record.exc_info[0].__name__
        return json.dumps(line, separators=(",", ":"))


_STANDARD_RECORD_FIELDS = (frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
                           | {"message", "asctime", "taskName"})


class _SafeLoggingHandler(LoggingHandler):
    """The OTLP log handler. It exports a COPY of the record (so the record other handlers see is untouched) without any
    `extra=` fields, which the stock handler would copy out as attributes, and with an exception reduced to its class
    name (the stock handler would attach the message and the traceback)."""

    def emit(self, record: logging.LogRecord) -> None:
        clean = copy.copy(record)
        for name in list(clean.__dict__):
            if name not in _STANDARD_RECORD_FIELDS:
                delattr(clean, name)
        if clean.exc_info and clean.exc_info[0] is not None:
            clean.msg = f"{clean.msg} exception={clean.exc_info[0].__name__}"
            clean.args = None
        clean.exc_info = None
        clean.exc_text = None
        super().emit(clean)


# ---- configuration ------------------------------------------------------------------------------------------------

@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None
    tracer: trace.Tracer
    runs_counter: object
    run_duration: object
    handlers: list = field(default_factory=list)
    exporting: bool = False


_state: Telemetry | None = None


def _resource() -> Resource:
    return Resource.create({"service.name": SERVICE_NAME, "service.version": revision(),
                            "deployment.environment": environment()})


def _remove_handlers() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, "_cohortlens_telemetry", False):
            logger.removeHandler(handler)


def configure(*, span_exporter=None, metric_readers=None, log_exporter=None, log_stream=None) -> Telemetry:
    """(Re)build the providers. Exporters passed in (tests, or the OTLP ones from `configure_from_environment`) are used
    with synchronous processors (tests); real OTLP exporters mark themselves `_synchronous = False` and get batching."""
    global _state
    shutdown()
    resource = _resource()
    exporting = bool(span_exporter or metric_readers or log_exporter)
    synchronous = bool(getattr(span_exporter, "_synchronous", True))

    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        processor = SimpleSpanProcessor(span_exporter) if synchronous else BatchSpanProcessor(
            span_exporter, schedule_delay_millis=2000, max_export_batch_size=256)
        tracer_provider.add_span_processor(AllowlistSpanProcessor(processor))

    views = [View(instrument_name="cohortlens_cohort_run_duration_seconds",
                  aggregation=ExplicitBucketHistogramAggregation(DURATION_BUCKETS))]
    meter_provider = MeterProvider(resource=resource, metric_readers=list(metric_readers or []), views=views)
    meter = meter_provider.get_meter(SERVICE_NAME)
    runs_counter = meter.create_counter("cohortlens_cohort_runs_total", unit="1",
                                        description="Cohort runs by outcome")
    run_duration = meter.create_histogram("cohortlens_cohort_run_duration_seconds", unit="s",
                                          description="Wall-clock duration of a cohort run")

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    handlers = []
    stream_handler = logging.StreamHandler(log_stream if log_stream is not None else sys.stdout)
    stream_handler.setFormatter(JsonLogFormatter())
    stream_handler.addFilter(RedactingFilter())
    handlers.append(stream_handler)

    logger_provider = None
    if log_exporter is not None:
        logger_provider = LoggerProvider(resource=resource)
        log_processor = SimpleLogRecordProcessor(log_exporter) if getattr(log_exporter, "_synchronous", True) else \
            BatchLogRecordProcessor(log_exporter)
        logger_provider.add_log_record_processor(log_processor)
        otlp_handler = _SafeLoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        otlp_handler.addFilter(RedactingFilter())
        handlers.append(otlp_handler)

    _remove_handlers()
    for handler in handlers:
        handler._cohortlens_telemetry = True
        logger.addHandler(handler)

    _state = Telemetry(tracer_provider, meter_provider, logger_provider, tracer_provider.get_tracer(SERVICE_NAME),
                       runs_counter, run_duration, handlers, exporting)
    return _state


def configure_from_environment() -> Telemetry:
    """Export over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (the SDK reads the endpoint and the
    `OTEL_EXPORTER_OTLP_HEADERS` credentials itself); otherwise export nothing."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return configure()
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    span_exporter, log_exporter = OTLPSpanExporter(), OTLPLogExporter()
    span_exporter._synchronous = False
    log_exporter._synchronous = False
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000)
    return configure(span_exporter=span_exporter, metric_readers=[reader], log_exporter=log_exporter)


def shutdown() -> None:
    global _state
    _remove_handlers()
    if _state is not None:
        for provider in (_state.tracer_provider, _state.meter_provider, _state.logger_provider):
            try:
                if provider is not None:
                    provider.shutdown()
            except Exception:  # shutting down a broken exporter must not raise
                logging.getLogger(LOGGER_NAME).debug("telemetry shutdown failed")
        _state = None


def get_tracer() -> trace.Tracer:
    if _state is None:
        configure_from_environment()
    return _state.tracer


def record_run(outcome: str, seconds: float) -> None:
    """Count one run and record its duration. The outcome label is a closed set, so no free text can become a label."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown run outcome: {outcome!r}")
    if _state is None:
        configure_from_environment()
    attributes = {"outcome": outcome, "environment": environment(), "revision": revision()}
    _state.runs_counter.add(1, attributes)
    _state.run_duration.record(seconds, attributes)
