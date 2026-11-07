"""A polite web fetcher for monitored grant sources (standard library only).

It reads robots.txt before the first page and stops if robots.txt can't be read or forbids a page, waits at least
min_interval seconds between requests (or the source's Crawl-delay, if longer), caps the pages per run, refuses any
host other than the source's own (redirects included), limits response size and sends a clear user agent.
Fetched text is returned as data for an adapter to parse; nothing here interprets or acts on it.
"""
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

PRODUCT = "GrantManagementPlatform/0.3 (development prototype; grant discovery"
CONTACT_ENV = "GRANT_PLATFORM_CONTACT"
DEFAULT_TIMEOUT = 20
MAX_BYTES = 3_000_000


class FetchError(Exception):
    """A page could not be fetched. .url and .status (None for network errors) say which and why."""

    def __init__(self, message, url=None, status=None):
        self.url, self.status = url, status
        super().__init__(message)


class RobotsError(FetchError):
    """robots.txt could not be read, or it does not allow the page. The run must stop."""


class SourceFormatError(FetchError):
    """A fetched page no longer has the structure the source's adapter expects (the site may have changed)."""


class PageLimitReached(FetchError):
    """The per-run page cap was reached."""


@dataclass(frozen=True)
class Page:
    url: str
    status: int
    text: str
    fetched_at: str


def user_agent(contact=None):
    contact = contact if contact is not None else os.environ.get(CONTACT_ENV, "").strip()
    return f"{PRODUCT}; contact: {contact})" if contact else f"{PRODUCT})"


def _tls_context():
    """Certificate checking is always on. Python from python.org on a Mac may have no trusted certificates installed,
    so use the maintained bundle that ships with certifi (already installed with the OpenAI and httpx libraries) when
    it is available, and Python's own defaults otherwise."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


def _urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_tls_context())


def _reason(error):
    """The underlying cause for people to read: its class, plus the plain-English reason for a certificate problem."""
    cause = getattr(error, "reason", None)
    cause = cause if isinstance(cause, BaseException) else error
    detail = f": {cause.verify_message}" if isinstance(cause, ssl.SSLCertVerificationError) and cause.verify_message else ""
    return f"{type(cause).__name__}{detail}"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PoliteFetcher:
    def __init__(self, base_url, *, min_interval=5.0, max_pages=40, timeout=DEFAULT_TIMEOUT, agent=None,
                 opener=_urlopen, clock=time.monotonic, sleep=time.sleep, now=_utc_now, redirect_hosts=()):
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("A source's base address must start with https://")
        self.base_url, self.host = base_url, parts.hostname.lower()
        # Exact hostnames a request to the source's own host may be redirected to (the source's official alternate address).
        # Pages are still only ever requested from the source's own host.
        self.redirect_hosts = frozenset({self.host, *(str(host).lower() for host in redirect_hosts)})
        self.min_interval, self.max_pages, self.timeout = max(1.0, float(min_interval)), int(max_pages), timeout
        self.agent = agent or user_agent()
        self._open, self._clock, self._sleep, self._now = opener, clock, sleep, now
        self._robots = None
        self._last_request = None
        self.pages_fetched = 0

    def _wait_turn(self):
        if self._last_request is not None:
            remaining = self.min_interval - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def _request(self, url):
        """One GET with the rate limit applied. Returns (status, text). Raises FetchError."""
        self._wait_turn()
        request = urllib.request.Request(url, headers={"User-Agent": self.agent, "Accept": "text/html,application/json,text/plain"})
        try:
            with self._open(request, self.timeout) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() not in self.redirect_hosts:
                    raise FetchError(f"Redirected off the source's own site to {final.hostname}", url)
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise FetchError("Page is larger than the size limit", url)
                charset = response.headers.get_content_charset() or "utf-8"
                return response.status, body.decode(charset, errors="replace")
        except urllib.error.HTTPError as error:
            raise FetchError(f"The site answered {error.code}", url, error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FetchError(f"Could not reach the site ({_reason(error)})", url) from None

    def _load_robots(self):
        url = urljoin(self.base_url, "/robots.txt")
        parser = robotparser.RobotFileParser(url)
        try:
            _, text = self._request(url)
        except FetchError as error:
            # RFC 9309: a robots.txt that doesn't exist (404/410) allows everything. Anything else stops the run.
            if error.status in (404, 410):
                parser.parse([])
                self._robots = parser
                return
            raise RobotsError(f"Could not read robots.txt: {error}", url, error.status) from None
        parser.parse(text.splitlines())
        delay = parser.crawl_delay(self.agent)
        if delay:
            self.min_interval = max(self.min_interval, float(delay))
        self._robots = parser

    def allowed(self, url):
        if self._robots is None:
            self._load_robots()
        return self._robots.can_fetch(self.agent, url)

    def get(self, url):
        """Fetch one page of the source. Raises RobotsError, PageLimitReached or FetchError."""
        url = urljoin(self.base_url, url)
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() != self.host:
            raise FetchError("Only pages on the source's own site are fetched", url)
        if not self.allowed(url):
            raise RobotsError("robots.txt does not allow this page", url)
        if self.pages_fetched >= self.max_pages:
            raise PageLimitReached(f"Stopped at the limit of {self.max_pages} pages for one run", url)
        self.pages_fetched += 1
        status, text = self._request(url)
        return Page(url=url, status=status, text=text, fetched_at=self._now())
