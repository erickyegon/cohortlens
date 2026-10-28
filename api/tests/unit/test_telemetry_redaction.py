"""Issue #21: the redacting log processor. "Given a redacting log processor, then bearer tokens, API keys, connection
strings and emails are masked." The patterns below are built from parts at run time so that no credential-looking
literal lives in the repository (a secret scanner flagged one in an earlier PR)."""
import io
import json
import logging

import pytest

from app.telemetry import MASK, JsonLogFormatter, RedactingFilter, redact

HEX = "0123456789abcdef"
KEY_BODY = "A1b2C3d4E5f6G7h8I9j0"


@pytest.mark.parametrize("secret_text", [
    "Authorization: Bearer " + KEY_BODY + ".payload.signature",
    "bearer " + KEY_BODY,
    "api_key=" + KEY_BODY, "api-key: " + KEY_BODY, "apikey=" + KEY_BODY, "X-Api-Key: " + KEY_BODY,
    "token=" + KEY_BODY, "password=" + KEY_BODY, "secret: " + KEY_BODY,
    "s" + "k-" + KEY_BODY + KEY_BODY, "glsa_" + KEY_BODY + KEY_BODY, "ghp_" + KEY_BODY + KEY_BODY,
    "postgres" + "ql://user:" + "pw" + KEY_BODY + "@db.example.com:5432/cohortlens",
    "postgres" + "ql+psycopg://user:pw@host/db?sslmode=require",
    "postgres://user:pw@10.0.0.5/db",
    "mysql://user:pw@host/db", "https://user:pw@example.com/path",
    "someone@example.com", "first.last+tag@sub.example.co.uk",
])
def test_secret_like_text_is_masked(secret_text):
    masked = redact(f"before {secret_text} after")
    assert MASK in masked and masked.startswith("before ") and masked.endswith(" after")
    for fragment in (KEY_BODY, "user:pw", "someone@", "first.last", "@example.com", "@host", "@db."):
        assert fragment not in masked, (fragment, masked)


@pytest.mark.parametrize("harmless", [
    "run finished outcome=success duration_ms=412", "trace_id=" + HEX * 2,
    "revision a5b89d770e0b9b10b9cd1fbc3ca251755b14aba1",
    "cohortlens-api-dev-00004-req", "persons 5724 final 481", "GET /cohorts/6f1c1f0e-5b1c-4d55-9a0e-0a5f4c2b9a11/runs",
    "request failed exception=OperationalError", "an @ sign alone and a_token word", "https://cohortlens.run.app/version",
])
def test_ordinary_log_text_is_left_alone(harmless):
    assert redact(harmless) == harmless


def test_redaction_is_idempotent():
    once = redact("mail me at a@b.com with Bearer " + KEY_BODY)
    assert redact(once) == once


def test_the_filter_masks_the_message_and_its_arguments_before_any_handler_sees_it():
    record = logging.LogRecord("cohortlens.api", logging.INFO, __file__, 1, "failed for %s using %s",
                               ("a@b.com", "Bearer " + KEY_BODY), None)
    assert RedactingFilter().filter(record) is True
    text = record.getMessage()
    assert "a@b.com" not in text and KEY_BODY not in text and MASK in text


def test_the_filter_survives_odd_records_without_raising():
    for message, args in (("100% sure %d", (5,)), ("no args", None), ("bad %s %s", ("only one",)), (b"bytes", None)):
        record = logging.LogRecord("cohortlens.api", logging.INFO, __file__, 1, message, args, None)
        assert RedactingFilter().filter(record) is True


def test_a_redacted_message_reaches_the_json_line():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("cohortlens.test_redaction")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("user a@b.com sent Bearer %s", KEY_BODY)
    finally:
        logger.removeHandler(handler)
    line = json.loads(stream.getvalue())
    assert "a@b.com" not in line["message"] and KEY_BODY not in line["message"] and MASK in line["message"]
