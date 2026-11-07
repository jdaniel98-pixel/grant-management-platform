"""One real match and one real draft, for the person to review by hand before a phase is signed off.

    PYTHONPATH=src .venv/bin/python -m grant_platform.live_check --run

Makes two OpenAI requests using only the fictional Bright Path data, in a throwaway in-memory database, and writes a
report to docs/evaluations/ with space for a human verdict. The API key is read from the environment or the git-ignored
.env file and is never printed or written."""
import argparse
import sys
from datetime import date
from pathlib import Path

from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ai, applications, drafting, matching

REPORT_DIR = Path(__file__).resolve().parents[2] / "docs" / "evaluations"
NEED_TITLE = "Digital Futures expansion"
QUESTION = "Describe the impact of your work"


def run(gateway, model, scenario=6, today=None):
    """Make the two requests and return the report as Markdown."""
    connection = db.connect(":memory:")
    try:
        repository = Repository(connection)
        organisation_id, _ = seed.seed_brightpath(repository)
        stored, _ = seed.seed_scenario_grants(repository)
        need_id = next(need["id"] for need in repository.list_funding_needs(organisation_id) if need["title"] == NEED_TITLE)
        match = matching.assess(repository, organisation_id, stored[scenario], need_id, gateway=gateway, model=model)
        application = applications.decide(repository, organisation_id, stored[scenario], need_id, "pursue")
        draft = drafting.draft(repository, organisation_id, application["id"], QUESTION, gateway=gateway, model=model)
        grant = repository.get_grant(stored[scenario])
        resolved = drafting.citation_records(repository, organisation_id, draft)
    finally:
        connection.close()
    lines = [
        f"# Live check, {today or date.today()}", "",
        f"Model: {model}. Matching prompt: {matching.PROMPT_VERSION}. Drafting prompt: {drafting.PROMPT_VERSION}.",
        "Data: fictional Bright Path Youth Foundation and a fictional scenario grant only.", "",
        f"## Match: {grant['title']} for the need '{NEED_TITLE}'", "",
        f"- Eligibility: {matching.STATUS_LABELS[match['eligibility_status']]}",
        f"- Suitability: {match['band']}, {match['suitability_total']} out of {matching.MAX_SCORE}",
        f"- Suggested next step: {match['recommendation']}",
        f"- Conflict with exclusions: {match['exclusion_conflict']['reasoning'] if match['exclusion_conflict'].get('verified') else 'none confirmed'}",
        f"- Summary: {match['summary']}", "", "Eligibility checks:",
    ]
    for item in match["eligibility_items"]:
        lines.append(f"- {item['requirement']}: {item['status']}" + ("" if item["verified"] or item["status"] == "unknown" else " (not confirmed against the text)")
                     + f". Grant says: \"{item['grant_quote']}\". Fact used: \"{item['organisation_fact']}\"")
    lines += ["", "Suitability:"] + [f"- {label}: {match['suitability'][name]['score']}/5. {match['suitability'][name]['reasoning']}" for name, label in matching.CRITERIA.items()]
    lines += ["", "Uncertainties: " + ("; ".join(match["uncertainties"]) or "none"), "Missing information: " + ("; ".join(match["missing_information"]) or "none"),
              "", f"## Draft: {QUESTION}", "", "Answer (AI draft, not reviewed):", "", draft["ai_text"] or "(no text)", "", "Citations:"]
    for citation in resolved:
        record = citation["record"]
        lines.append(f"- {citation['record_id']} ({'approved record found' if record and citation['verified'] else 'NOT an approved supplied record'}): {citation['claim']}")
    lines += ["", "Checks run by Python: " + ("; ".join(draft["review_flags"]) or "all citations resolve, no unsupported figures, no word-limit problem"),
              "Missing evidence: " + ("; ".join(draft["missing_evidence"]) or "none"), "",
              "## Human verdict", "", "Is the eligibility result right? ", "", "Is the suitability result sensible? ", "",
              "Is every claim in the draft supported by the cited records? ", "", "Anything invented, missed or badly phrased? ", ""]
    return "\n".join(lines) + "\n"


def main(argv=None, report_dir=REPORT_DIR):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], prog="grant_platform.live_check")
    parser.add_argument("--run", action="store_true", help="confirm you want to make two OpenAI requests")
    parser.add_argument("--grant", type=int, default=6, choices=range(1, 8), help="scenario grant number (default 6)")
    args = parser.parse_args(argv)
    if not args.run:
        print("This makes two OpenAI requests using fictional data. Add --run to go ahead")
        return 2
    gateway = ai.default_gateway()
    if gateway is None:
        print("No OpenAI key found. Set OPENAI_API_KEY in your shell or in the .env file in the project folder (see .env.example)")
        return 1
    try:
        report = run(gateway, ai.model_name(), args.grant)
    except matching.MatchError as error:
        print(f"The live check could not finish: {error}")
        return 1
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"live_check_{date.today()}_scenario{args.grant}.md"
    path.write_text(report, encoding="utf-8")
    print(f"Report written to {path}")
    print(report.split("## Draft")[0].split("## Match: ")[1].split("Eligibility checks:")[0].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
