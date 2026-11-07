"""Validated records stored by the platform."""
from typing import Literal

from pydantic import BaseModel, Field


EXTERNAL_USE_ALLOWED = ("safe_to_publish",)
APPROVED = "approved"


class Organisation(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    name: str = Field(min_length=1)
    organisation_type: str | None = None
    registration_country: str | None = None
    registration_number: str | None = None
    legal_form: str | None = None
    founded_year: int | None = None
    annual_income_band: str | None = None
    regions: list[str] = []
    mission: str | None = None
    activities: str | None = None
    beneficiaries: str | None = None
    # Profile field name -> evidence record key the value was taken from.
    field_sources: dict[str, str] = {}


class EvidenceRecord(BaseModel):
    record_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    text: str = Field(min_length=1)
    knowledge_type: str
    topic: str | None = None
    source: str | None = None
    external_use: str
    approval_status: str
    reporting_period: str | None = None
    origin: Literal["seed", "profile", "upload", "manual"] = "seed"
    # Remaining source metadata (record_type, reported_value, ...), kept verbatim.
    extra: dict = {}

    def usable_externally(self):
        """Approved and cleared for external use: the only records drafting may cite."""
        return self.approval_status == APPROVED and self.external_use in EXTERNAL_USE_ALLOWED


class FundingNeed(BaseModel):
    title: str = Field(min_length=1)
    kind: Literal["programme", "project", "core"]
    description: str = Field(min_length=1)
    amount_sought: int | None = None
    timeframe: str | None = None
    geography: str | None = None
    beneficiaries: str | None = None


class Grant(BaseModel):
    """A funding opportunity. Grants are shared reference data, not owned by one organisation."""
    origin: Literal["manual", "url", "monitored"]
    source_url: str | None = None
    canonical_url: str | None = None
    title: str = Field(min_length=1)
    funder: str = Field(min_length=1)
    purpose: str | None = None
    eligibility: str | None = None
    scope: str | None = None
    funding: str | None = None
    duration: str | None = None
    deadline_text: str | None = None
    deadline_date: str | None = None
    key_requirements: str | None = None
    exclusions: str | None = None
    status: Literal["open", "closed", "unknown"] = "unknown"
    dedupe_key: str = Field(min_length=1)
    # The monitored source the grant was found on; None for grants entered by hand or by web address.
    source_id: int | None = None
