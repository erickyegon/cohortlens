"""Review finding F4 of the telemetry PR: uvicorn's access log prints the request path and query string (for example
`/concepts?q=<text>`) to stdout, outside the redacting filter, and stdout reaches Cloud Logging. It is switched off; the
trace id and the JSON lines are the request record."""
import pathlib

DOCKERFILE = (pathlib.Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")


def test_the_api_container_does_not_print_an_access_log():
    cmd = next(line for line in DOCKERFILE.splitlines() if line.startswith("CMD"))
    assert "--no-access-log" in cmd and "uvicorn" in cmd
