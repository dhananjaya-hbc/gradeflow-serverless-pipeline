"""scripts/smoke_test.py against a real local HTTP server that can be made to misbehave.

One server plays both the CDN-served dashboard and the API. Every way a bad deploy could look to a
visitor is a switch on the fake: a missing header, a cacheable config, an API that never refuses a
missing key. Each must turn the smoke test red, and a healthy site must keep it green.
"""

import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "scripts" / "smoke_test.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

API_KEY = "k3y-0123456789abcdefghijklmnopqrstuv"
INDEX = (
    "<!doctype html><html><head>"
    '<script type="module" src="./assets/index-abc123.js"></script>'
    '</head><body><div id="root"></div></body></html>'
)

HEALTHY: dict[str, Any] = {
    "index_status": 200,
    "index_type": "text/html; charset=utf-8",
    "index_body": INDEX,
    "hsts": "max-age=31536000",
    "csp": "default-src 'none'",
    "nosniff": "nosniff",
    "frame": "DENY",
    "script_status": 200,
    "script_type": "text/javascript",
    "script_cache": "public,max-age=31536000,immutable",
    "config_status": 200,
    "config_cache": "no-store",
    "config": None,  # filled in with the server's own address
    "api_status": 200,
    "api_body": {"datasets": [], "next_cursor": None},
    "api_cors": "*",
    "keyless_status": 403,
    "preflight_status": 200,
    "preflight_headers": "Content-Type,X-Api-Key",
    "fail_first_api_calls": 0,
}


class Fake(BaseHTTPRequestHandler):
    behaviour: dict[str, Any] = {}
    calls: list[tuple[str, str]] = []
    api_failures_left = 0

    def log_message(self, *args: object) -> None:  # keep the test output quiet
        pass

    def reply(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            if value is not None:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        b = self.behaviour
        Fake.calls.append(("GET", self.path))
        if self.path == "/":
            self.reply(
                b["index_status"],
                b["index_body"].encode(),
                {
                    "Content-Type": b["index_type"],
                    "Strict-Transport-Security": b["hsts"],
                    "Content-Security-Policy": b["csp"],
                    "X-Content-Type-Options": b["nosniff"],
                    "X-Frame-Options": b["frame"],
                },
            )
        elif self.path.startswith("/assets/"):
            self.reply(
                b["script_status"],
                b"console.log(1)",
                {"Content-Type": b["script_type"], "Cache-Control": b["script_cache"]},
            )
        elif self.path == "/config.json":
            config = b["config"]
            body = config if isinstance(config, bytes) else json.dumps(config).encode()
            self.reply(
                b["config_status"],
                body,
                {"Content-Type": "application/json", "Cache-Control": b["config_cache"]},
            )
        elif self.path.startswith("/api/datasets"):
            if self.headers.get("x-api-key") != API_KEY:
                self.reply(b["keyless_status"], b'{"message":"Forbidden"}', {})
            elif Fake.api_failures_left > 0:
                Fake.api_failures_left -= 1
                self.reply(500, b'{"message":"Internal server error"}', {})
            else:
                body = b["api_body"]
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.reply(
                    b["api_status"],
                    raw,
                    {
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": b["api_cors"],
                    },
                )
        else:
            self.reply(404, b"not found", {})

    def do_OPTIONS(self) -> None:  # noqa: N802
        Fake.calls.append(("OPTIONS", self.path))
        self.reply(
            self.behaviour["preflight_status"],
            b"",
            {"Access-Control-Allow-Headers": self.behaviour["preflight_headers"]},
        )


@pytest.fixture
def site() -> Iterator[Any]:
    """Start the fake; yields a function that sets behaviour overrides and returns the address."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    address = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    Fake.calls = []

    def configure(**overrides: Any) -> str:
        behaviour = dict(HEALTHY)
        behaviour["config"] = {"apiUrl": f"{address}/api", "apiKey": API_KEY}
        behaviour.update(overrides)
        Fake.behaviour = behaviour
        Fake.api_failures_left = behaviour["fail_first_api_calls"]
        Fake.calls = []
        return address

    yield configure
    server.shutdown()
    server.server_close()


def run(address: str, capsys: pytest.CaptureFixture) -> tuple[int, str]:
    pauses: list[float] = []
    code = smoke.run(address, allow_http=True, sleep=pauses.append)  # no real waiting in tests
    return code, capsys.readouterr().out


# --- a healthy site -------------------------------------------------------------------------------


def test_a_healthy_site_passes_every_check(site, capsys) -> None:
    code, out = run(site(), capsys)
    assert code == 0, out
    assert out.count("  ok ") == 7
    assert "FAIL" not in out and "smoke test passed" in out


def test_the_api_key_is_never_printed(site, capsys) -> None:
    code, out = run(site(), capsys)
    assert code == 0
    assert API_KEY not in out


def test_the_key_is_not_printed_even_when_a_check_fails(site, capsys) -> None:
    code, out = run(site(api_status=500), capsys)
    assert code == 1
    assert API_KEY not in out


def test_the_api_is_called_with_the_key_and_the_dashboards_origin(site, capsys) -> None:
    address = site()
    run(address, capsys)
    assert ("GET", "/api/datasets?limit=1") in Fake.calls
    assert ("OPTIONS", "/api/datasets") in Fake.calls


# --- every way a bad deploy shows -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"index_status": 403}, "the page answered HTTP 403"),
        ({"index_status": 500}, "the page answered HTTP 500"),
        ({"index_type": "application/xml"}, "not served as text/html"),
        ({"index_body": "<html><body>nothing</body></html>"}, "no app root element"),
        ({"hsts": None}, "missing the strict-transport-security header"),
        ({"csp": None}, "missing the content-security-policy header"),
        ({"nosniff": "sniff"}, "x-content-type-options is not nosniff"),
        ({"frame": "SAMEORIGIN"}, "x-frame-options is not DENY"),
        ({"script_status": 404}, "the script answered HTTP 404"),
        ({"script_type": "text/html"}, "not served as javascript"),
        ({"script_cache": "no-cache"}, "not cached as immutable"),
        ({"config_status": 403}, "config.json answered HTTP 403"),
        ({"config_cache": "public,max-age=3600"}, "config.json can be cached"),
        ({"config": b"<html>"}, "not valid JSON"),
        ({"config": []}, "config.json is not a JSON object"),
        ({"config": {"apiKey": "x"}}, "no apiUrl"),
        ({"config": {"apiUrl": "http://x"}}, "no apiKey"),
        ({"api_status": 429}, "GET /datasets with the key answered HTTP 429"),
        ({"api_status": 500}, "GET /datasets with the key answered HTTP 500"),
        ({"api_body": {"nothing": 1}}, "has no datasets array"),
        ({"api_body": {"datasets": []}}, "has no next_cursor"),
        ({"api_body": b"oops"}, "not valid JSON"),
        ({"api_cors": None}, "no CORS header for the browser"),
        ({"keyless_status": 200}, "without a key answered HTTP 200"),
        ({"preflight_status": 403}, "preflight answered HTTP 403"),
        ({"preflight_headers": "Content-Type"}, "does not allow the x-api-key header"),
    ],
)
def test_each_kind_of_broken_deploy_turns_the_smoke_test_red(
    site, capsys, overrides: dict[str, Any], message: str
) -> None:
    code, out = run(site(**overrides), capsys)
    assert code == 1, out
    assert message in out
    assert "smoke test FAILED" in out


def test_a_page_that_is_down_skips_the_checks_that_depend_on_it(site, capsys) -> None:
    code, out = run(site(index_status=500), capsys)
    assert code == 1
    assert out.count("  FAIL  ") == 1  # one clear failure, not a cascade
    assert "skipped" in out


# --- retries --------------------------------------------------------------------------------------


def test_a_brief_api_error_right_after_a_deploy_is_retried(site, capsys) -> None:
    """The first calls fail with a 500 (a cold or throttled API); it waits and tries again."""
    pauses: list[float] = []
    address = site(fail_first_api_calls=2)
    code = smoke.run(address, allow_http=True, sleep=pauses.append)
    assert code == 0, capsys.readouterr().out
    assert len(pauses) == 2  # waited once per failed attempt


def test_a_permanent_failure_gives_up_after_the_retries(site, capsys) -> None:
    pauses: list[float] = []
    address = site(api_status=500)
    code = smoke.run(address, allow_http=True, sleep=pauses.append)
    assert code == 1
    assert len(pauses) == smoke.RETRIES - 1  # it did not retry forever and did not skip the retries


def test_an_unreachable_site_fails_with_a_message_not_a_crash(capsys) -> None:
    code = smoke.run("http://127.0.0.1:9", allow_http=True, sleep=lambda _: None)
    out = capsys.readouterr().out
    assert code == 1
    assert "could not connect" in out


# --- https only -----------------------------------------------------------------------------------


def test_a_plain_http_dashboard_address_is_refused_unless_explicitly_allowed(capsys) -> None:
    assert smoke.run("http://example.com", sleep=lambda _: None) == 2
    assert "must start with https://" in capsys.readouterr().err


def test_an_http_api_address_in_config_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """config.json pointing the browser at plain http would send the API key in the clear."""
    config = {"apiUrl": "http://api.example.com/v1", "apiKey": API_KEY}
    canned = smoke.Response(200, {"cache-control": "no-store"}, json.dumps(config).encode())
    monkeypatch.setattr(smoke, "request", lambda *args, **kwargs: canned)
    with pytest.raises(smoke.CheckFailed, match="not an https:// address") as failure:
        smoke.Site("https://dash.example.com", allow_http=False).config_is_served_fresh()
    assert API_KEY not in str(failure.value)


def test_the_https_rule_for_the_api_address(capsys) -> None:
    site_ = smoke.Site("https://dash.example.com", allow_http=False)
    with pytest.raises(smoke.CheckFailed, match="not an https:// address"):
        site_.https_only("http://api.example.com", "the API address in config.json")
    smoke.Site("https://dash.example.com", allow_http=False).https_only(
        "https://api.example.com", "x"
    )


# --- the command itself (the workflow depends on its exit code) -----------------------------------


def command(*args: str) -> Any:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_test.py"), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_command_exits_zero_for_a_healthy_site(site) -> None:
    result = command("--dashboard-url", site(), "--allow-http")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke test passed" in result.stdout
    assert API_KEY not in result.stdout + result.stderr


def test_the_command_refuses_a_plain_http_address_with_exit_code_two() -> None:
    result = command("--dashboard-url", "http://dash.example.com")
    assert result.returncode == 2
    assert "must start with https://" in result.stderr


def test_the_command_needs_the_dashboard_address() -> None:
    result = command()
    assert result.returncode == 2
    assert "--dashboard-url" in result.stderr
