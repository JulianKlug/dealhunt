"""The only place in the project that knows how to talk HTTP.

Sources call `get_text` / `post_json` and never touch TLS fingerprints, rate
limiting or retries themselves.

Three transports, because the sites disagree about who is allowed to speak:

    PLAIN       python-requests. Fine for tutti (JSON API) and kleinanzeigen.
    CURL        the system `curl` binary. Its TLS fingerprint clears the
                Cloudflare challenge that 403s python-requests on ricardo.
    CHROME      curl_cffi impersonating Chrome. The only thing eBay accepts;
                optional, and the source disables itself when it is missing.
    BROWSER     a headless Chromium via Playwright. For sites that ship an
                empty app shell and render listings in JavaScript (Vinted).
                Slow and heavy: one page load is ~6 s and a real browser.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
import time
from enum import Enum
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

# One request per host per this many seconds. We are one person checking a
# marketplace, and the traffic should look like it.
MIN_HOST_INTERVAL_S = 4.0

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2.0
TIMEOUT_S = 30

HTTP_OK = 200
HTTP_FORBIDDEN = 403
HTTP_TOO_MANY_REQUESTS = 429

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
}

try:  # optional — only eBay strictly needs it
    from curl_cffi import requests as curl_requests

    HAS_CHROME_IMPERSONATION = True
except ImportError:
    curl_requests = None
    HAS_CHROME_IMPERSONATION = False

HAS_CURL_BINARY = shutil.which("curl") is not None

try:  # optional — only JavaScript-rendered sources need it
    from playwright.sync_api import sync_playwright

    HAS_BROWSER = True
except ImportError:
    sync_playwright = None
    HAS_BROWSER = False

# How long a rendered page gets to fill in its listings after the DOM loads.
BROWSER_RENDER_WAIT_MS = 4000
BROWSER_TIMEOUT_MS = 45000


class Transport(Enum):
    PLAIN = "plain"
    CURL = "curl"
    CHROME = "chrome"
    BROWSER = "browser"


def headers_for(transport: "Transport") -> Dict[str, str]:
    """Default request headers for a transport.

    Impersonation sets a User-Agent that matches its TLS fingerprint; forcing
    ours on top would make the two disagree, which bot detection looks for.
    """
    headers = dict(BROWSER_HEADERS)

    if transport is Transport.CHROME:
        headers.pop("User-Agent")

    return headers


class HttpError(Exception):
    """Any non-recoverable HTTP failure, already retried."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_block(self) -> bool:
        """The site refused *us*, not this one request — e.g. a Cloudflare 403."""
        return self.status_code in (HTTP_FORBIDDEN, HTTP_TOO_MANY_REQUESTS)


class Response(NamedTuple):
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    """Rate-limited, retrying HTTP client shared by every source."""

    def __init__(self, transport: Transport = Transport.PLAIN, curl_binary: str = ""):
        # PATH resolution is not stable across shells and systemd, and the curl
        # builds differ in what Cloudflare accepts — so the caller may pin one.
        self._curl = curl_binary or shutil.which("curl") or "curl"
        self._transport = self._resolve(transport)
        self._last_hit: Dict[str, float] = {}
        self._session = requests.Session()
        self._browser = None  # launched on first use, released by close()

        if self._transport is Transport.CHROME:
            self._session = curl_requests.Session(impersonate="chrome")

    def close(self) -> None:
        """Release the headless browser, if one was started."""
        if self._browser is None:
            return

        playwright, browser, _ = self._browser
        browser.close()
        playwright.stop()
        self._browser = None

    def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        merged = headers_for(self._transport)
        merged.update(headers or {})

        return self._retry("GET", url, merged, payload=None).text

    def post_json(self, url: str, payload: Any, headers: Dict[str, str]) -> Any:
        return self._retry("POST", url, headers, payload=payload).json()

    @staticmethod
    def _resolve(requested: Transport) -> Transport:
        """Fall back down the chain rather than failing to start."""
        if requested is Transport.BROWSER and not HAS_BROWSER:
            raise HttpError("playwright not installed — the browser transport is unavailable")

        if requested is Transport.CHROME and not HAS_CHROME_IMPERSONATION:
            log.warning("curl_cffi not installed — falling back to the curl binary")
            requested = Transport.CURL

        if requested is Transport.CURL and not HAS_CURL_BINARY:
            log.warning("curl not on PATH — falling back to python-requests")
            return Transport.PLAIN

        return requested

    def _retry(self, method: str, url: str, headers: Dict[str, str], payload: Any) -> Response:
        self._wait_for_host(url)

        last_error: Any = None
        last_status: Optional[int] = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._send(method, url, headers, payload)
            except Exception as exc:  # network-level: worth another try
                last_error = exc
                self._backoff(attempt)
                continue

            if response.status_code == HTTP_OK:
                return response

            # 429 and 5xx are transient; 403/404 are a verdict, so stop at once.
            retryable = (
                response.status_code == HTTP_TOO_MANY_REQUESTS or response.status_code >= 500
            )
            last_status = response.status_code
            last_error = HttpError(f"HTTP {response.status_code}", last_status)

            if not retryable:
                break

            self._backoff(attempt)

        raise HttpError(f"{method} {url}: {last_error}", last_status)

    def _send(self, method: str, url: str, headers: Dict[str, str], payload: Any) -> Response:
        if self._transport is Transport.CURL:
            return self._send_via_curl_binary(method, url, headers)

        if self._transport is Transport.BROWSER:
            return self._send_via_browser(method, url)

        response = self._session.request(
            method, url, headers=headers, json=payload, timeout=TIMEOUT_S
        )

        # requests falls back to ISO-8859-1 for HTML that declares no charset,
        # which turns "Größe" into mojibake and hides the "€" the price parser
        # looks for. These sites are UTF-8; say so when the server didn't.
        if "charset" not in response.headers.get("Content-Type", "").lower():
            response.encoding = "utf-8"

        return Response(response.status_code, response.text)

    def _send_via_curl_binary(self, method: str, url: str, headers: Dict[str, str]) -> Response:
        if method != "GET":
            raise HttpError("the curl transport only implements GET")

        command = [self._curl, "-sL", "--compressed", "--max-time", str(TIMEOUT_S)]

        for name, value in headers.items():
            command += ["-H", f"{name}: {value}"]

        # Status code is appended after the body so one capture yields both.
        command += ["-w", "\n%{http_code}", url]

        completed = subprocess.run(command, capture_output=True, timeout=TIMEOUT_S + 10)
        if completed.returncode != 0:
            raise HttpError(f"curl exited {completed.returncode}")

        body, _, status = completed.stdout.decode("utf-8", "replace").rpartition("\n")

        return Response(int(status or 0), body)

    def _send_via_browser(self, method: str, url: str) -> Response:
        """Load the page in headless Chromium and return the rendered DOM."""
        if method != "GET":
            raise HttpError("the browser transport only implements GET")

        page = self._browser_context().new_page()

        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            page.wait_for_timeout(BROWSER_RENDER_WAIT_MS)

            return Response(response.status if response else 0, page.content())
        finally:
            page.close()

    def _browser_context(self):
        if self._browser is None:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(locale="fr-FR", timezone_id="Europe/Paris")
            self._browser = (playwright, browser, context)

        return self._browser[2]

    def _wait_for_host(self, url: str) -> None:
        """Space out requests per host, with jitter so the cadence isn't robotic."""
        host = urlsplit(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        gap = MIN_HOST_INTERVAL_S + random.uniform(0, 1.5)

        if elapsed < gap:
            time.sleep(gap - elapsed)

        self._last_hit[host] = time.monotonic()

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(BACKOFF_BASE_S * attempt)
