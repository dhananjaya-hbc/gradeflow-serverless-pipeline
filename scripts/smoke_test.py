"""Check that a deployed GradeFlow dashboard and its API really work. Standard library only.

    python scripts/smoke_test.py --dashboard-url https://dxxxxxxxx.cloudfront.net

It needs no AWS access: it does what a visitor's browser does. It loads the page, reads the
dashboard's config.json (the API address and key), and calls the API. The CI deploy runs it last, so
a deploy that produced a broken site turns the run red instead of passing silently.

Each check is retried a few times: right after a deploy, the CDN and a freshly updated API can
answer with an error for a moment. The API key is never printed.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

TIMEOUT_SECONDS = 15
RETRIES = 5
RETRY_PAUSE_SECONDS = 4.0


class CheckFailed(Exception):
    """A check did not pass; the message says why, and never contains the API key."""


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise CheckFailed("the response is not valid JSON") from exc


def request(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> Response:
    """One HTTP call. An HTTP error status is a normal Response (a check decides if it is fine)."""
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as reply:
            return Response(
                reply.status, {k.lower(): v for k, v in reply.headers.items()}, reply.read()
            )
    except urllib.error.HTTPError as error:
        return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, error.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CheckFailed(
            f"{method} {urllib.parse.urlsplit(url).path or '/'} could not connect: {error}"
        ) from error


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


class Site:
    """What is learned along the way: the page, and the API address and key from config.json."""

    def __init__(self, dashboard_url: str, allow_http: bool) -> None:
        self.url = dashboard_url.rstrip("/")
        self.allow_http = allow_http
        self.api_url = ""
        self.api_key = ""
        self.index_html = ""

    def https_only(self, url: str, what: str) -> None:
        expect(url.startswith("https://") or self.allow_http, f"{what} is not an https:// address")

    # --- the checks, in the order they build on each other ---------------------------------------

    def page_loads(self) -> str:
        reply = request(self.url + "/")
        expect(reply.status == 200, f"the page answered HTTP {reply.status}, expected 200")
        expect("text/html" in reply.header("content-type"), "the page is not served as text/html")
        expect('id="root"' in reply.text(), "the page has no app root element")
        self.index_html = reply.text()
        return "200, html with the app root"

    def page_has_security_headers(self) -> str:
        reply = request(self.url + "/")
        for header in ("strict-transport-security", "content-security-policy"):
            expect(bool(reply.header(header)), f"the page is missing the {header} header")
        expect(
            reply.header("x-content-type-options").lower() == "nosniff",
            "x-content-type-options is not nosniff",
        )
        expect(reply.header("x-frame-options").upper() == "DENY", "x-frame-options is not DENY")
        return "HSTS, CSP, nosniff, frame deny"

    def script_is_served_and_cacheable(self) -> str:
        match = re.search(r'src="\./(assets/[^"]+\.js)"', self.index_html)
        expect(match is not None, "the page does not reference a script under assets/")
        reply = request(f"{self.url}/{match.group(1)}")
        expect(reply.status == 200, f"the script answered HTTP {reply.status}, expected 200")
        expect(
            "javascript" in reply.header("content-type"), "the script is not served as javascript"
        )
        expect(
            "immutable" in reply.header("cache-control"),
            "the hashed script is not cached as immutable",
        )
        return "200, javascript, immutable cache"

    def config_is_served_fresh(self) -> str:
        reply = request(self.url + "/config.json")
        expect(reply.status == 200, f"config.json answered HTTP {reply.status}, expected 200")
        expect(
            "no-store" in reply.header("cache-control"),
            "config.json can be cached (it holds the API key)",
        )
        config = reply.json()
        expect(isinstance(config, dict), "config.json is not a JSON object")
        api_url, api_key = config.get("apiUrl"), config.get("apiKey")
        expect(isinstance(api_url, str) and api_url != "", "config.json has no apiUrl")
        expect(isinstance(api_key, str) and api_key != "", "config.json has no apiKey")
        self.https_only(api_url, "the API address in config.json")
        self.api_url, self.api_key = api_url.rstrip("/"), api_key
        return "200, no-store, has the API address and a key"

    def api_answers_with_the_key(self) -> str:
        reply = request(
            self.api_url + "/datasets?limit=1",
            headers={"x-api-key": self.api_key, "Origin": self.url},
        )
        expect(
            reply.status == 200,
            f"GET /datasets with the key answered HTTP {reply.status}, expected 200",
        )
        body = reply.json()
        expect(
            isinstance(body, dict) and isinstance(body.get("datasets"), list),
            "the list has no datasets array",
        )
        expect("next_cursor" in body, "the list has no next_cursor")
        expect(
            bool(reply.header("access-control-allow-origin")),
            "the answer has no CORS header for the browser",
        )
        return f"200, {len(body['datasets'])} dataset(s) returned"

    def api_refuses_a_missing_key(self) -> str:
        reply = request(self.api_url + "/datasets?limit=1")
        expect(
            reply.status == 403,
            f"GET /datasets without a key answered HTTP {reply.status}, expected 403",
        )
        return "403 without a key"

    def api_answers_the_browser_preflight(self) -> str:
        reply = request(
            self.api_url + "/datasets",
            method="OPTIONS",
            headers={
                "Origin": self.url,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key",
            },
        )
        expect(
            200 <= reply.status < 300,
            f"the CORS preflight answered HTTP {reply.status}, expected 2xx",
        )
        expect(
            "x-api-key" in reply.header("access-control-allow-headers").lower(),
            "the preflight does not allow the x-api-key header",
        )
        return "2xx, x-api-key allowed"


def with_retries(check: Callable[[], str], sleep: Callable[[float], None]) -> str:
    """Run a check up to RETRIES times; return its summary, or raise the last failure."""
    failure = CheckFailed("not run")
    for attempt in range(RETRIES):
        try:
            return check()
        except CheckFailed as error:
            failure = error
            if attempt < RETRIES - 1:
                sleep(RETRY_PAUSE_SECONDS)
    raise failure


def run(
    dashboard_url: str, allow_http: bool = False, sleep: Callable[[float], None] = time.sleep
) -> int:
    if not (dashboard_url.startswith("https://") or allow_http):
        print("error: the dashboard address must start with https://", file=sys.stderr)
        return 2
    site = Site(dashboard_url, allow_http)
    checks: list[tuple[str, Callable[[], str]]] = [
        ("the page loads", site.page_loads),
        ("the page sends the security headers", site.page_has_security_headers),
        ("the page's script is served and cacheable", site.script_is_served_and_cacheable),
        ("config.json is served fresh with the API address", site.config_is_served_fresh),
        ("the API answers with the key", site.api_answers_with_the_key),
        ("the API refuses a request with no key", site.api_refuses_a_missing_key),
        ("the API answers the browser's CORS preflight", site.api_answers_the_browser_preflight),
    ]
    failures = 0
    for name, check in checks:
        try:
            print(f"  ok    {name}: {with_retries(check, sleep)}")
        except CheckFailed as error:
            failures += 1
            print(f"  FAIL  {name}: {error}")
            if name.startswith(("the page loads", "config.json")):
                print("        (later checks depend on this one, so they are skipped)")
                break
    print("smoke test passed" if not failures else f"smoke test FAILED ({failures} check)")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dashboard-url", required=True, help="the dashboard's https address")
    parser.add_argument(
        "--allow-http", action="store_true", help="accept http:// (local tests only)"
    )
    args = parser.parse_args()
    return run(args.dashboard_url, args.allow_http)


if __name__ == "__main__":
    sys.exit(main())
