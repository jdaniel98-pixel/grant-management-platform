"""Builders for alert tests: two fictional charities, grants and stored match results (no AI, no network)."""
from datetime import date

from grant_platform import db
from grant_platform.models import FundingNeed, Organisation
from grant_platform.repository import Repository
from grant_platform.services import grants, notifications

LONG_AGO = "2000-01-01T00:00:00Z"
T0 = "2026-10-01T09:00:00Z"
MONDAY = date(2026, 10, 26)   # a Monday
TUESDAY = date(2026, 10, 27)
NEXT_MONDAY = date(2026, 11, 2)


def result(band="Strong fit", eligibility="eligible", recommendation="Pursue", total=22, summary="A good match", missing=()):
    return {
        "fingerprint": "x", "prompt_version": "test", "model": "test", "eligibility_status": eligibility, "eligibility_items": [],
        "suitability": {}, "suitability_total": total, "band": band, "recommendation": recommendation, "exclusion_conflict": {},
        "summary": summary, "uncertainties": [], "missing_information": list(missing), "grant_verified": False,
    }


def make_organisation(repository, slug, name, need_title):
    organisation_id = repository.create_organisation(Organisation(slug=slug, name=name, mission=f"{name} mission"))
    repository.add_funding_need(organisation_id, FundingNeed(title=need_title, kind="programme", description=f"{need_title} description"))
    need_id = repository.list_funding_needs(organisation_id)[0]["id"]
    return organisation_id, need_id


def make_grant(repository, title, funder="Example Trust (fictional)", **extra):
    grant = grants.create_grant(repository, {"title": title, "funder": funder, "deadline_date": "2027-06-30", "status": "open",
                                             "funding": "Up to £20,000", "source_url": f"https://example.org/{title.lower().replace(' ', '-')}", **extra})
    # Found after any contact's consent date used in these tests
    repository.connection.execute("UPDATE grant_record SET first_seen_at = ? WHERE id = ?", ("2026-10-10T00:00:00Z", grant["id"]))
    repository.connection.commit()
    return repository.get_grant(grant["id"])


class World:
    """Two fictional charities, each with one funding need and one consented contact who wants everything."""

    def __init__(self):
        self.connection = db.connect(":memory:")
        self.repo = Repository(self.connection)
        self.a, self.a_need = make_organisation(self.repo, "harbour-youth", "Harbour Youth Trust (fictional)", "Harbour Youth Studio")
        self.b, self.b_need = make_organisation(self.repo, "riverside-arts", "Riverside Arts Collective (fictional)", "Riverside Mural Project")
        self.contact_a = self.contact(self.a, "Alex Harbour", "alex@harbour.example")
        self.contact_b = self.contact(self.b, "Robin Riverside", "robin@riverside.example")

    def contact(self, organisation_id, name, email, **preferences):
        contact = notifications.add_contact(self.repo, organisation_id, name, email, consent_given=True, now=T0)
        if preferences:
            contact = notifications.update_preferences(self.repo, organisation_id, contact["id"], now=T0, **preferences)
        return contact

    def match(self, organisation_id, need_id, grant, **fields):
        return self.repo.save_match(organisation_id, need_id, grant["id"], result(**fields))

    def fresh(self, contact):
        return self.repo.get_contact(contact["organisation_id"], contact["id"])

    def close(self):
        self.connection.close()
