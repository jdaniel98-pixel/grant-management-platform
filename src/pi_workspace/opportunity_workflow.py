"""Record reviewed-match decisions and manually update opportunity statuses."""

import argparse
import json
import sys
import pilot_storage
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MATCH_RESULTS_PATH = ROOT / "match_results.json"
GRANTS_PATH = ROOT / "data" / "grants.json"
OPPORTUNITIES_PATH = ROOT / "opportunities.json"
STATUSES = ("Considering", "Pursuing", "Drafting", "Submitted", "Not pursuing")
DECISIONS = ("Pursue", "Save for review", "Reject")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_opportunities():
    target = pilot_storage.path("opportunities.json") if pilot_storage.enabled() else OPPORTUNITIES_PATH
    return read_json(target) if target.exists() else []


def save_opportunities(opportunities):
    target = pilot_storage.path("opportunities.json") if pilot_storage.enabled() else OPPORTUNITIES_PATH
    pilot_storage.atomic_json(target, opportunities)


def find_one(records, **fields):
    matches = [r for r in records if all(r.get(k) == v for k, v in fields.items())]
    if len(matches) != 1:
        raise ValueError(f"Expected one matching record for {fields}; found {len(matches)}.")
    return matches[0]


def unresolved_points(analysis):
    """Carry forward existing concerns without generating new analysis."""
    if "concerns_gaps" in analysis:
        return list(dict.fromkeys(analysis["concerns_gaps"]))
    criteria = analysis.get("criterion_assessments")
    if criteria is None:
        criteria = [
            {"criterion": name.replace("_", " "), **assessment}
            for name, assessment in analysis.get("assessments", {}).items()
            if isinstance(assessment, dict)
        ]
    return list(dict.fromkeys(
        f"{item['criterion']}: {item['reasoning']}"
        for item in criteria if item["score"] < 5
    ))


@pilot_storage.serialized
def record_decision(idea_title, grant_title, decision, match_analysis=None):
    if decision not in DECISIONS:
        raise ValueError("Decision must be Pursue, Save for review or Reject.")
    if match_analysis is None:
        reviewed = find_one(
            read_json(MATCH_RESULTS_PATH),
            research_idea_title=idea_title,
            grant_title=grant_title,
        )
        match_analysis = reviewed["match_analysis"]
    grant = find_one(read_json(GRANTS_PATH), title=grant_title)
    analysis = match_analysis
    overall = analysis.get("overall_result", analysis)
    # Older saved analyses have no numeric score: retain that absence as null.
    percentage = overall.get("match_percentage")
    checklist_fields = (
        ("eligibility", "Eligibility"),
        ("development_stage", "Development stage"),
        ("funding", "Funding"),
        ("duration", "Duration"),
        ("key_requirements", "Key requirements"),
        ("exclusions", "Exclusions"),
    )
    opportunity = {
        "grant_title": grant_title,
        "matched_research_idea": idea_title,
        "match_percentage": percentage,
        "deadline": grant["deadline"],
        "current_status": {
            "Pursue": "Pursuing", "Save for review": "Considering", "Reject": "Not pursuing"
        }[decision],
        "application_requirements_checklist": [
            f"{label}: {grant[field]}"
            for field, label in checklist_fields
            if grant.get(field)
        ],
        "grant_website_url": grant["source_url"],
        "suggested_next_step": {
            "Pursue": "Start draft", "Save for review": "Resolve outstanding questions",
            "Reject": None,
        }[decision],
    }
    if decision == "Save for review":
        opportunity["key_unresolved_points"] = unresolved_points(analysis)
        opportunity["match_analysis"] = analysis
    opportunities = load_opportunities()
    existing = [
        item for item in opportunities
        if item["grant_title"] == grant_title
        and item["matched_research_idea"] == idea_title
    ]
    if len(existing) > 1:
        raise ValueError("Multiple tracked records exist for this idea and grant.")
    if existing:
        existing[0].pop("key_unresolved_points", None)
        existing[0].update(opportunity)
    else:
        opportunities.append(opportunity)
    save_opportunities(opportunities)
    return opportunity


@pilot_storage.serialized
def save_reminder_preference(idea_title, grant_title, preference):
    """Store a preference only; this does not schedule or deliver reminders."""
    opportunities = load_opportunities()
    opportunity = find_one(
        opportunities, grant_title=grant_title, matched_research_idea=idea_title
    )
    if opportunity["current_status"] not in ("Considering", "Pursuing", "Drafting"):
        raise ValueError("Reminder preferences are available for active opportunities only.")
    opportunity["reminder_preference"] = preference
    save_opportunities(opportunities)
    return opportunity


@pilot_storage.serialized
def finish_reminder_intro(idea_title, grant_title, preference=None):
    """Persist dismissal, optionally with a preference; never schedule delivery."""
    opportunities = load_opportunities()
    opportunity = find_one(opportunities, grant_title=grant_title, matched_research_idea=idea_title)
    if preference is not None:
        if opportunity["current_status"] not in ("Considering", "Pursuing", "Drafting"):
            raise ValueError("Reminder preferences are available for active opportunities only.")
        opportunity["reminder_preference"] = preference
    opportunity["reminder_intro_dismissed"] = True
    save_opportunities(opportunities)
    return opportunity


@pilot_storage.serialized
def save_application_draft(idea_title, grant_title, narrative, missing_information):
    """Save an initial draft once, retaining the original and its real gaps."""
    opportunities = load_opportunities()
    opportunity = find_one(opportunities, grant_title=grant_title, matched_research_idea=idea_title)
    if opportunity.get("application_draft"):
        raise ValueError("A saved draft already exists; it has not been overwritten")
    if not narrative.strip():
        raise ValueError("The draft is empty")
    opportunity["application_draft"] = {
        "original_ai_draft": narrative,
        "initial_application_draft": narrative,
        "missing_information_pi_input_required": list(missing_information),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    opportunity["suggested_next_step"] = "Resume draft"
    save_opportunities(opportunities)
    return opportunity["application_draft"]


@pilot_storage.serialized
def edit_application_draft(idea_title, grant_title, narrative, project_title=None):
    """Replace only the reviewed narrative; preserve original, gaps and status."""
    if not narrative.strip():
        raise ValueError("Add draft content before saving")
    if project_title is not None and not project_title.strip():
        raise ValueError("Add a project title before saving")
    opportunities = load_opportunities()
    opportunity = find_one(opportunities, grant_title=grant_title, matched_research_idea=idea_title)
    draft = opportunity["application_draft"]
    draft["initial_application_draft"] = narrative
    if project_title is not None:
        draft["project_title"] = project_title.strip()
    draft["modified_at"] = datetime.now(timezone.utc).isoformat()
    opportunity["suggested_next_step"] = "Resume draft"
    save_opportunities(opportunities)
    return draft


@pilot_storage.serialized
def reset_test_opportunities():
    """Clear prototype dataset records; preserve any unrelated stored records."""
    grant_titles = {grant["title"] for grant in read_json(GRANTS_PATH)}
    idea_titles = {idea["title"] for idea in read_json(pilot_storage.ideas_path())}
    opportunities = load_opportunities()
    remaining = [
        item for item in opportunities
        if not (
            item.get("grant_title") in grant_titles
            and item.get("matched_research_idea") in idea_titles
        )
    ]
    save_opportunities(remaining)


@pilot_storage.serialized
def change_status(idea_title, grant_title, status):
    if status not in STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")
    opportunities = load_opportunities()
    opportunity = find_one(
        opportunities, grant_title=grant_title, matched_research_idea=idea_title
    )
    opportunity["current_status"] = status
    save_opportunities(opportunities)
    return opportunity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    decision = commands.add_parser("decide", help="Record a reviewed match decision")
    status = commands.add_parser("status", help="Manually change a tracked status")
    for command in (decision, status):
        command.add_argument("--idea", required=True, help="Exact research idea title")
        command.add_argument("--grant", required=True, help="Exact grant title")
    decision.add_argument("--decision", required=True, choices=DECISIONS)
    status.add_argument("--status", required=True, choices=STATUSES)
    args = parser.parse_args()
    if args.command == "decide":
        result = record_decision(args.idea, args.grant, args.decision)
    else:
        result = change_status(args.idea, args.grant, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"Opportunity workflow failed: {error}", file=sys.stderr)
        sys.exit(1)
