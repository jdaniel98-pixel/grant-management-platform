"""A fictional monitored source for offline discovery tests: saved pages, a fake network and a tiny adapter.

Nothing here touches the network. FakeWeb answers urllib requests from a dict of saved pages."""
import io
import re
from email.message import Message
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError

from grant_platform.services.fetcher import FetchError

PAGES = Path(__file__).resolve().parent / "fixtures" / "sources" / "fictional_funder"
BASE = "https://meadowbank.example.org"
SOURCE = {
    "slug": "fictional-funder", "name": "Meadowbank open grants (fictional)", "publisher": "Meadowbank Community Trust (fictional)",
    "base_url": BASE, "adapter": "fictional", "coverage": ["UK", "England"], "licence": "Fictional test data",
    "terms_url": BASE + "/terms", "terms_checked_on": "2026-10-29", "min_interval_seconds": 5, "max_pages_per_run": 10,
}
ROBOTS_ALLOW = "User-agent: *\nDisallow: /admin/\n"


def saved_pages(robots=ROBOTS_ALLOW):
    pages = {f"{BASE}/grants": (PAGES / "listing.html").read_text(encoding="utf-8")}
    for path in PAGES.glob("*.html"):
        if path.stem != "listing":
            pages[f"{BASE}/grants/{path.stem}"] = path.read_text(encoding="utf-8")
    if robots is not None:
        pages[f"{BASE}/robots.txt"] = robots
    return pages


class _Response(io.BytesIO):
    def __init__(self, url, body, status=200):
        super().__init__(body.encode("utf-8"))
        self.url, self.status = url, status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def geturl(self):
        return self.url


class FakeWeb:
    """opener(request, timeout) for PoliteFetcher. pages maps URL -> text, or -> an int HTTP status to fail with.
    redirects maps URL -> final URL. down=True makes every request fail as if the network were unreachable."""

    def __init__(self, pages=None, redirects=None, down=False):
        self.pages = saved_pages() if pages is None else pages
        self.redirects = redirects or {}
        self.down = down
        self.requests = []

    def __call__(self, request, timeout):
        url = request.full_url
        self.requests.append((url, request.get_header("User-agent")))
        if self.down:
            raise URLError("network unreachable")
        body = self.pages.get(url, 404)
        if isinstance(body, int):
            raise HTTPError(url, body, "error", Message(), None)
        return _Response(self.redirects.get(url, url), body)


class FakeClock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _field(html, name):
    match = re.search(rf'class="{name}">(.*?)</', html, re.S)
    return " ".join(unescape(match.group(1)).split()) if match else None


class FictionalAdapter:
    """Reads the listing, then each grant page. Returns plain grant fields; never sets verification."""

    def __init__(self):
        self.problems, self.listed_urls, self.unavailable_urls, self.complete = [], set(), set(), False

    def discover(self, fetcher):
        listing = fetcher.get("/grants")
        hrefs = re.findall(r'<a class="grant" href="([^"]+)"', listing.text)
        self.listed_urls = {BASE + href for href in hrefs}
        self.complete = True
        for href in hrefs:
            try:
                page = fetcher.get(href)
            except FetchError as error:
                if error.status != 404:
                    raise
                self.unavailable_urls.add(BASE + href)
                continue
            yield {
                "source_url": page.url, "title": _field(page.text, "title"), "funder": _field(page.text, "funder"),
                "eligibility": _field(page.text, "eligibility"), "funding": _field(page.text, "funding"),
                "purpose": _field(page.text, "purpose"), "deadline_text": _field(page.text, "deadline"),
                "deadline_date": _field(page.text, "deadline"), "status": _field(page.text, "status") or "open",
            }


ADAPTERS = {"fictional": FictionalAdapter}
