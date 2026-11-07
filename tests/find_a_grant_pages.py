"""Saved GOV.UK Find a grant pages (public, Open Government Licence) and helpers that reshape them for offline tests."""
import copy
import json
import re
from pathlib import Path

from grant_platform.services.sources import find_a_grant

SAVED = Path(__file__).resolve().parent / "fixtures" / "sources" / "find_a_grant"
HOST = find_a_grant.HOST
CEF = "community-and-environment-fund-cef-and-business-and-local-economy-fund-blef-1"
WCPG = "trees-woodlands-and-forestry---woodland-creation-planning-grant-1"
_BLOCK = re.compile(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', re.S)


def saved(name):
    return (SAVED / name).read_text(encoding="utf-8")


def reshape(html, change):
    """Return the page with its embedded data passed through change(props) (a deep copy)."""
    match = _BLOCK.search(html)
    data = json.loads(match.group(2))
    data["props"]["pageProps"] = change(copy.deepcopy(data["props"]["pageProps"])) or data["props"]["pageProps"]
    return html[: match.start(2)] + json.dumps(data) + html[match.end(2):]


def listing(cards=None, total=None, page=1):
    def change(props):
        if cards is not None:
            props["searchResult"] = cards
        props["totalGrants"] = len(props["searchResult"]) if total is None else total
        props["currentPage"] = page
        return props
    return reshape(saved("listing.html"), change)


def cards_by_label():
    match = _BLOCK.search(saved("listing.html"))
    return {card["label"]: card for card in json.loads(match.group(2))["props"]["pageProps"]["searchResult"]}


def detail(label, change=None):
    html = saved(f"{label}.html")
    return reshape(html, change) if change else html


def edit_fields(**values):
    def change(props):
        props["grantDetail"]["fields"].update(values)
        return props
    return change


def web_pages(extra=None):
    """URL -> text for a one-page listing of the two saved grants (404 robots.txt, as the real site)."""
    cards = cards_by_label()
    pages = {
        f"{HOST}/robots.txt": 404,
        f"{HOST}/grants": listing([cards[CEF], cards[WCPG]]),
        f"{HOST}/grants/{CEF}": detail(CEF),
        f"{HOST}/grants/{WCPG}": detail(WCPG),
    }
    pages.update(extra or {})
    return pages
