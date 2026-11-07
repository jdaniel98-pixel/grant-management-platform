"""Canned model answers for the eight fictional scenario grants, so matching is tested offline.

Quotes are copied from sample_data/scenario_grants and facts from the fictional Bright Path profile, the way a
careful model would. A gateway built here records every payload it was sent."""
from grant_platform.services.drafting import DraftAnalysis
from grant_platform.services.matching import ModelAnalysis

DIGITAL = "Digital Futures expansion"
CORE = "Core operating costs"


def scores(*values):
    names = ("mission_fit", "beneficiary_fit", "activity_fit", "funding_fit", "requirements_fit")
    return {name: {"score": value, "reasoning": f"{name} reasoning"} for name, value in zip(names, values)}


def item(requirement, status, grant_quote, fact=None):
    return {"requirement": requirement, "status": status, "grant_quote": grant_quote, "organisation_fact": fact, "reasoning": "Canned reasoning"}


NO_CONFLICT = {"category": "none", "grant_quote": None, "reasoning": "No conflict"}

# (grant title, funding need title or None) -> the answer. A None need key is the fallback for either mode.
CANNED = {
    "Youth Employment Fund": dict(
        items=[item("Based in the UK", "met", "based in the UK", "United Kingdom"),
               item("Works with 16 to 25 year olds facing barriers", "met", "work directly with 16 to 25 year olds facing barriers to employment", "young people aged 16–25")],
        suitability=scores(5, 5, 4, 4, 4)),
    "Scottish Youth Pathways": dict(
        items=[item("Registered in Scotland", "unknown", "organisations registered in Scotland")],
        suitability=scores(5, 5, 4, 4, 4), missing=["Which part of the UK the organisation is registered in"]),
    "American Youth Initiative": dict(
        items=[item("US 501(c)(3) status", "not_met", "Only organisations holding US 501(c)(3) status may apply.", "United Kingdom")],
        suitability=scores(3, 4, 3, 3, 2)),
    "Small Charities Boost": dict(
        items=[item("Annual income under £250,000", "unknown", "annual income under £250,000")],
        suitability=scores(4, 4, 4, 3, 4), missing=["Annual income"]),
    "Climate Adaptation Research Grant": dict(
        items=[item("Research track record in climate science", "unknown", "research track record in climate science")],
        suitability=scores(1, 1, 1, 2, 2)),
    "Digital Skills for Young People": {
        DIGITAL: dict(
            items=[item("UK registered charity delivering digital skills training", "met", "delivering digital skills training to young people", "practical digital skills")],
            suitability=scores(5, 5, 5, 4, 4)),
        CORE: dict(
            items=[item("UK registered charity delivering digital skills training", "met", "delivering digital skills training to young people", "digital-skills")],
            suitability=scores(3, 3, 3, 1, 3),
            conflict={"category": "activity_exclusion_or_purpose_conflict", "grant_quote": "Core costs, overheads and general running costs are not funded", "reasoning": "The need is for core costs"}),
        None: dict(
            items=[item("UK registered charity delivering digital skills training", "met", "delivering digital skills training to young people", "skills development")],
            suitability=scores(5, 5, 5, 4, 4)),
    },
    "Established Charities Grant": dict(
        items=[item("Three years of independently audited accounts", "unknown", "three years of independently audited accounts")],
        suitability=scores(4, 4, 4, 3, 3), missing=["Audited accounts"]),
}


def analysis_for(payload):
    entry = CANNED[payload["grant"]["title"]]
    if "items" not in entry:
        need = (payload["funding_need"] or {}).get("title")
        entry = entry.get(need, entry[None])
    return ModelAnalysis.model_validate({
        "eligibility_items": entry["items"],
        "suitability": entry["suitability"],
        "exclusion_conflict": entry.get("conflict", NO_CONFLICT),
        "summary": f"Canned summary for {payload['grant']['title']}",
        "uncertainties": entry.get("uncertainties", []),
        "missing_information": entry.get("missing", []),
    })


class CannedGateway:
    def __init__(self, behaviour=analysis_for):
        self.behaviour = behaviour
        self.calls = []

    def __call__(self, instructions, payload, schema):
        self.calls.append({"instructions": instructions, "payload": payload})
        return self.behaviour(payload)


# Drafting: canned answers keyed on a word in the question. Figures are copied from the fictional Bright Path records.
IMPACT_ANSWER = (
    "In 2024, BrightPath supported 312 young people across its employability, mentoring, digital-skills and enterprise "
    "activities. Of the 214 participants who completed a structured progression programme in 2024, 154 moved into "
    "employment, education, accredited training or a sustained work placement within six months."
)


def draft_for(payload):
    question = payload["question"].casefold()
    if "impact" in question:
        return DraftAnalysis.model_validate({
            "answer": IMPACT_ANSWER,
            "citations": [{"record_id": "impact_2024_participants", "claim": "312 young people supported in 2024"},
                          {"record_id": "impact_2024_progression", "claim": "154 of 214 progressed"}],
            "missing_evidence": []})
    if "2025" in question or "salary" in question:
        return DraftAnalysis.model_validate({"answer": "", "citations": [], "missing_evidence": ["The figures asked for are not in the evidence"]})
    if "safeguarding" in question:
        return DraftAnalysis.model_validate({
            "answer": "BrightPath maintains a safeguarding policy covering young people and adults at risk, with induction for staff and relevant volunteers and designated safeguarding leads.",
            "citations": [{"record_id": "policy_safeguarding", "claim": "safeguarding policy and leads"}],
            "missing_evidence": []})
    return DraftAnalysis.model_validate({"answer": "A short answer.", "citations": [{"record_id": "org_mission", "claim": "mission"}], "missing_evidence": []})


class CannedDraftGateway(CannedGateway):
    def __init__(self, behaviour=draft_for):
        super().__init__(behaviour)
