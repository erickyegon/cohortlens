"""Steady traffic for the chaos drill (issue #27): the reference cohort, run `count` times, `interval` seconds apart.

    python loadgen/runs.py --url https://cohortlens-api-dev-<hash>-ul.a.run.app --count 60 --interval 12

It creates the reference cohort once and always deletes it again. Only dev run.app addresses are accepted. Failed runs
are expected during a drill: they are counted, not fatal. Realistic definitions only (docs/observability.md).
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

MAX_COUNT = 500
COHORT = pathlib.Path(__file__).resolve().parent.parent / "api" / "tests" / "fixtures" / "reference_cohort.json"
_URL = re.compile(r"https://cohortlens-(api|web)-dev-[a-z0-9-]+\.a\.run\.app")
_PREFIX = re.compile(r"/[A-Za-z0-9/_-]*")


def _send(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(data or b""))}
    request = urllib.request.Request(url, data=data if method == "POST" else None, method=method, headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=40) as answer:  # noqa: S310 - https, host validated
            text = answer.read().decode("utf-8") or "{}"
            return answer.status, json.loads(text)
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (urllib.error.URLError, TimeoutError):
        return 0, {}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _arguments(argv):
    parser = _Parser(add_help=False)
    parser.add_argument("--url", required=True)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval", type=int, default=12)
    parser.add_argument("--api-prefix", default="")
    args = parser.parse_args(argv)
    if not _URL.fullmatch(args.url):
        raise ValueError("--url must be a dev Cloud Run address like https://cohortlens-api-dev-<hash>-ul.a.run.app")
    if not 1 <= args.count <= MAX_COUNT or not 1 <= args.interval <= 300:
        raise ValueError(f"--count must be 1 to {MAX_COUNT} and --interval 1 to 300 seconds")
    if args.api_prefix and not _PREFIX.fullmatch(args.api_prefix):
        raise ValueError("--api-prefix must start with /")
    return args


def main(argv=None, send=_send, sleep=time.sleep) -> int:
    try:
        args = _arguments(sys.argv[1:] if argv is None else argv)
    except (ValueError, SystemExit) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    base = args.url + args.api_prefix
    status, created = send("POST", f"{base}/cohorts", json.loads(COHORT.read_text(encoding="utf-8")))
    if status != 201:
        print(f"could not create the cohort (HTTP {status})", file=sys.stderr)
        return 1
    cohort = f"{base}/cohorts/{created['id']}"
    answers: dict = {}
    try:
        for number in range(args.count):
            status, _ = send("POST", f"{cohort}/runs")
            answers[status] = answers.get(status, 0) + 1
            print(f"run {number + 1}/{args.count}: HTTP {status}", flush=True)
            if number + 1 < args.count:
                sleep(args.interval)
    finally:
        send("DELETE", cohort)
    print("answers:", json.dumps(answers, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
