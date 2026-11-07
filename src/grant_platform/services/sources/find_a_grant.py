"""GOV.UK Find a grant (Cabinet Office): https://www.find-government-grants.service.gov.uk

Access method, checked against the site's published terms on 29 October 2026: ordinary public pages only, no login,
no search form, no API. The terms do not mention automated access; they ban attacks and unauthorised access. No
robots.txt is published (HTTP 404), so there are no crawl rules, but the fetcher still waits 5 seconds between requests
and identifies itself. Content is under the Open Government Licence v3.0 (Crown copyright).

Each page carries its data as JSON in the page's own `__NEXT_DATA__` block, so nothing is scraped from the visible
HTML and no AI is needed. The listing (`/grants?skip=&limit=&page=`) gives one card per grant; each grant page gives
the full details. Only grants open to non-profit organisations are kept. Everything found is saved as unverified.
"""
import json
import math
import re
from datetime import date, datetime

from grant_platform.services.fetcher import FetchError, PageLimitReached, RobotsError, SourceFormatError

HOST = "https://www.find-government-grants.service.gov.uk"
PAGE_SIZE = 10
MAX_LISTING_PAGES = 50
LICENCE = "Open Government Licence v3.0 (Crown copyright)"
SOURCE = {
    "slug": "find-a-grant",
    "name": "GOV.UK Find a grant",
    "publisher": "Cabinet Office (Government Grants Management Function)",
    "base_url": HOST,
    "adapter": "find_a_grant",
    "coverage": ["UK", "England", "Scotland", "Wales", "Northern Ireland"],
    "licence": LICENCE,
    "terms_url": HOST + "/info/terms-and-conditions",
    "terms_checked_on": "2026-10-29",
    "min_interval_seconds": 5,
    "max_pages_per_run": 150,
}

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
# Wording the site uses when the closing date it shows is only the end of confirmed funding, not a real closure.
_NO_REAL_DEADLINE = re.compile(
    r"no closing date|rather than a known closure date|reflects the period .{0,80}funding is (currently )?confirmed|"
    r"end of the (confirmed )?funding period|open (for applications )?year[- ]round|applications can be made at any time",
    re.IGNORECASE | re.S,
)
_BLOCKS = {"paragraph", "list-item", "heading-1", "heading-2", "heading-3", "heading-4", "heading-5", "heading-6", "blockquote"}
_TRUNCATED = " [Truncated: read the full text on the source page]"


def page_data(html, what="page"):
    """The JSON the page itself embeds. Raises SourceFormatError if the page isn't shaped as expected."""
    match = _NEXT_DATA.search(html or "")
    try:
        return json.loads(match.group(1))["props"]["pageProps"]
    except (AttributeError, ValueError, KeyError, TypeError):
        raise SourceFormatError(f"The {what} no longer has the expected structure; the site may have changed") from None


def rich_text(node):
    """Plain text from a Contentful rich-text document. Paragraphs and list items become lines; links keep their text."""
    if not isinstance(node, dict):
        return ""
    kind = node.get("nodeType")
    if kind == "text":
        return node.get("value", "")
    children = node.get("content", [])
    if kind == "list-item":
        return "- " + " ".join(rich_text(child).strip() for child in children if rich_text(child).strip())
    if any(child.get("nodeType") in _BLOCKS or child.get("nodeType", "").endswith("-list") for child in children if isinstance(child, dict)):
        return "\n".join(part for part in (rich_text(child).strip("\n") for child in children) if part.strip())
    return "".join(rich_text(child) for child in children)


def _clean(text):
    return "\n".join(" ".join(line.split()) for line in str(text or "").replace("\xa0", " ").splitlines()).strip()


def _limit(text, limit):
    return text if len(text) <= limit else text[: limit - len(_TRUNCATED)].rstrip() + _TRUNCATED


def parse_site_date(value):
    """('2030-04-01T00:00') -> date. The site stores a midnight closing time on the next day for 'end of day' (the page
    shows it as 11:59pm the day before), so 00:00 means the previous date. Returns None if unreadable."""
    try:
        moment = datetime.fromisoformat(str(value)[:16])
    except ValueError:
        return None
    return date.fromordinal(moment.toordinal() - 1) if (moment.hour, moment.minute) == (0, 0) else moment.date()


def _opening_date(value):
    try:
        return datetime.fromisoformat(str(value)[:16]).date()
    except ValueError:
        return None


def is_non_profit(applicant_types):
    return any(re.sub(r"[^a-z]", "", str(kind).lower()) == "nonprofit" for kind in applicant_types or ())


def _money(low, high, total):
    text = f"{low} to {high}" if low and high and low != high else (high or low or "")
    if total:
        text = f"{text} (total scheme {total})" if text else f"Total scheme {total}"
    return text or None


def grant_fields(fields, url, today):
    """Plain grant fields (what a person types on the Grants screen) from one grant page's data."""
    summary = _clean(rich_text(fields.get("grantSummaryTab")))
    short = _clean(fields.get("grantShortDescription"))
    objectives = _clean(rich_text(fields.get("grantObjectivesTab")))
    eligibility = _clean(rich_text(fields.get("grantEligibilityTab")))
    dates = _clean(rich_text(fields.get("grantDatesTab")))
    apply_text = _clean(rich_text(fields.get("grantApplyTab")))
    kinds = ", ".join(fields.get("grantApplicantType") or ())

    opens = _opening_date(fields.get("grantApplicationOpenDate"))
    closes = parse_site_date(fields.get("grantApplicationCloseDate"))
    shown = closes.strftime("%d %B %Y").lstrip("0") if closes else None
    no_real_deadline = bool(_NO_REAL_DEADLINE.search(" ".join((short, dates))))
    if no_real_deadline:
        deadline_date = None
        deadline_text = "No fixed closing date" + (f" (the site shows {shown}, the end of confirmed funding)" if shown else "")
    else:
        deadline_date = closes.isoformat() if closes else None
        deadline_text = f"Closes {shown}" if shown else None
    # Closed only when the site gives an unambiguous closing date that has passed.
    if deadline_date and closes < today:
        status = "closed"
    elif opens and opens <= today:
        status = "open"
    else:
        status = "unknown"

    purpose = "\n\n".join(part for part in (short or summary, objectives) if part)
    return {
        "source_url": url,
        "title": fields.get("grantName"),
        "funder": fields.get("grantFunder"),
        "purpose": _limit(purpose, 4000) or None,
        "eligibility": _limit("\n\n".join(part for part in (f"Who can apply (as listed): {kinds}." if kinds else "", eligibility) if part), 4000) or None,
        "scope": _limit(", ".join(fields.get("grantLocation") or ()), 300) or None,
        "funding": _limit(_money(fields.get("grantMinimumAwardDisplay"), fields.get("grantMaximumAwardDisplay"), fields.get("grantTotalAwardDisplay")) or "", 300) or None,
        "deadline_text": _limit(deadline_text or "", 300) or None,
        "deadline_date": deadline_date,
        "key_requirements": _limit("\n\n".join(part for part in (apply_text, dates) if part), 4000) or None,
        "status": status,
    }


class FindAGrantAdapter:
    # The service's own address without "www": its terms page names it, and both sit under the government-only
    # service.gov.uk domain. Only redirects between these two exact hostnames are accepted.
    redirect_hosts = ("find-government-grants.service.gov.uk",)
    # This many pages failing in a row (not 404 or unreadable) means the site, not one page, is the problem.
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, today=None):
        self.today = today or date.today()
        self.problems = []
        # Read by discovery after the run: every address the list showed, those whose page is gone or couldn't be
        # read, and whether the whole list was read (only then can a missing grant be flagged).
        self.listed_urls, self.unavailable_urls, self.skipped_urls, self.complete = set(), set(), set(), False
        self._failures_in_a_row = 0

    def discover(self, fetcher):
        first = fetcher.get("/grants")
        data = page_data(first.text, "grants list")
        total = data.get("totalGrants")
        if not isinstance(total, int) or not isinstance(data.get("searchResult"), list):
            raise SourceFormatError("The grants list no longer has the expected structure; the site may have changed")
        pages = min(max(math.ceil(total / PAGE_SIZE), 1), MAX_LISTING_PAGES)
        # Each list page's grants are read straight away, so a run stopped by the page limit still keeps what it reached.
        # Only a run that reads the whole list sets `complete`, which is what allows missing grants to be flagged.
        cards, listing = [], data
        for page_number in range(1, pages + 1):
            if page_number > 1:
                listing = page_data(fetcher.get(f"/grants?skip={(page_number - 1) * PAGE_SIZE}&limit={PAGE_SIZE}&page={page_number}").text, "grants list")
                if not isinstance(listing.get("searchResult"), list):
                    raise SourceFormatError("The grants list no longer has the expected structure; the site may have changed")
            page_cards = list(listing["searchResult"])
            cards.extend(page_cards)
            for card in page_cards:
                label = str(card.get("label") or "")
                if re.fullmatch(r"[a-z0-9-]+", label):
                    self.listed_urls.add(f"{HOST}/grants/{label}")
                yield from self._read_card(fetcher, card, label)
        # The whole list was read only if the site's count matches what the pages showed.
        self.complete = len(cards) == total

    def _read_card(self, fetcher, card, label):
        if not re.fullmatch(r"[a-z0-9-]+", label) or not is_non_profit(card.get("grantApplicantType")):
            return
        url = f"{HOST}/grants/{label}"
        try:
            detail = page_data(fetcher.get(f"/grants/{label}").text, f"grant page {label}")["grantDetail"]["fields"]
            self._failures_in_a_row = 0
        except (RobotsError, PageLimitReached):
            raise  # the whole run must stop
        except FetchError as error:
            # One bad grant page (gone, redirected elsewhere, unreadable, timed out) is skipped and reported; the rest carry on.
            # Only several failures in a row point at the source itself.
            self.problems.append(f"Skipped {url}: {error}")
            (self.unavailable_urls if error.status == 404 else self.skipped_urls).add(url)
            if error.status != 404 and not isinstance(error, SourceFormatError):
                self._failures_in_a_row += 1
                if self._failures_in_a_row >= self.MAX_CONSECUTIVE_FAILURES:
                    raise
            return
        except (KeyError, TypeError):
            self.problems.append(f"Skipped {url}: no grant details on the page")
            self.skipped_urls.add(url)
            return
        yield grant_fields(detail, url, self.today)
