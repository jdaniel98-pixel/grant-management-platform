"""Interactive research idea → grant → match → decision → track → draft."""

import re
import sys
import os
import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAIError
from pydantic import ValidationError

import grant_drafter
import matcher
import opportunity_workflow


def select(prompt, choices):
    if not choices:
        raise ValueError(f"No choices available for {prompt}.")
    print(f"\n{prompt}")
    for number, label in enumerate(choices, start=1):
        print(f"{number}. {label}")
    while True:
        answer = input("Select a number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return int(answer) - 1
        print(f"Enter a number from 1 to {len(choices)}.")


def presentation_text(text):
    """Replace source-field identifiers with readable source names for display."""
    text = text.replace("activity_exclusion_or_purpose_conflict", "a conflict with the funder's exclusions or purpose")
    text = text.replace("definite_ineligibility", "confirmed ineligibility")
    text = re.sub(r"hard[ -]constraints?", "eligibility or scope restriction", text, flags=re.IGNORECASE)
    for identifier, label in (
        ("lab_profile", "lab profile"),
        ("research_idea", "research idea"),
        ("grant", "grant information"),
        ("laboratory_capabilities", "lab capabilities"),
        ("fictional_previous_funding", "previous funding"),
        ("example_previous_research", "previous research"),
    ):
        pattern = rf"\b{identifier}(?:\.[A-Za-z_][A-Za-z_0-9]*)+\b" if identifier == "grant" else rf"\b{identifier}(?:\.[A-Za-z_][A-Za-z_0-9]*)*\b"
        text = re.sub(pattern, label, text)
    return text


def concise_explanation(text):
    """Present at most two existing sentences without backend terminology."""
    text = presentation_text(text)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return " ".join(sentences[:2])


def display_match(analysis):
    overall = analysis["overall_result"]
    print("\nOverall result")
    print(f"Match percentage: {overall['match_percentage']}%")
    print(f"Overall assessment: {overall['overall_assessment']}")
    print(f"Total score: {overall['total_score']}/{overall['maximum_score']}")
    print("\nScoring guide")
    guide = analysis["scoring_guide"]
    print(guide["criterion_scale"])
    for band, label in guide["assessment_thresholds"].items():
        print(f"{band} = {label}")
    print("\nSix criterion assessments")
    for item in analysis["criterion_assessments"]:
        print(f"{item['criterion']}: {item['score']}/{item['maximum_score']}")
        print(presentation_text(item["reasoning"]))
    print(f"\nOur recommendation: {analysis['suggested_next_action']}")
    print(concise_explanation(analysis["summary"]))
    if analysis["suggested_next_action"] == "Investigate further":
        print("We recommend saving this opportunity for review while you investigate these points. You’ll be reminded on a schedule you choose, so the opportunity doesn’t get forgotten as the deadline approaches. Reminders can be delivered by email or calendar.")
    constraint = analysis["hard_constraint"]
    if constraint["triggered"]:
        print(f"\nImportant: {concise_explanation(constraint['explanation'])}")
    print(f"Deadline: {analysis['deadline']}")
    print("The recommendation is advisory; you make the final decision.")


def available_grants(idea, grants):
    rejected = {
        item["grant_title"] for item in opportunity_workflow.load_opportunities()
        if item["matched_research_idea"] == idea["title"]
        and item["current_status"] == "Not pursuing"
    }
    return [grant for grant in grants if grant["title"] not in rejected]


class SearchEvaluationError(ValueError):
    """Every available candidate failed; individual diagnostics are retained."""
    def __init__(self, failures):
        self.failures = failures
        super().__init__("No valid opportunity evaluations are available.")


def failure_diagnostic(error, grant_title=None, *, stage="evaluation"):
    """Safe development diagnostics: no request payloads or configuration dumps."""
    source = (
        getattr(error, "failure_source", None)
        or ("OpenAI API" if isinstance(error, OpenAIError)
            else "Structured-output parsing" if isinstance(error, (ValidationError, json.JSONDecodeError))
            else "Streamlit/session state" if type(error).__module__.startswith("streamlit")
            else "Application code")
    )
    message = (
        str(error.errors(include_input=False, include_url=False))
        if isinstance(error, ValidationError) else str(error)
    )
    secret = os.environ.get("OPENAI_API_KEY")
    if secret:
        message = message.replace(secret, "[redacted]")
    message = re.sub(r"sk-[A-Za-z0-9_-]+|(?i:bearer)\s+\S+", "[redacted]", message)
    # Keep source locations without logging locals, model inputs or raw response bodies.
    locations = []
    trace = error.__traceback__
    while trace:
        locations.append(f"{trace.tb_frame.f_code.co_filename}:{trace.tb_lineno} ({trace.tb_frame.f_code.co_name})")
        trace = trace.tb_next
    diagnostic = {
        "opportunity": grant_title or "Search/loading UI",
        "source": source, "stage": stage,
        "exception_type": type(error).__name__, "message": message,
        "locations": locations,
    }
    logging.getLogger(__name__).warning("Funding evaluation diagnostic: %s", diagnostic)
    return diagnostic


def best_alternative(profile, idea, grants, rejected_title, analyses, *, max_workers=1, on_progress=None, on_failure=None):
    candidates = [
        grant for grant in available_grants(idea, grants)
        if grant["title"] != rejected_title
    ]
    if not candidates:
        return None
    failures = []

    def record_failure(error, grant, stage="evaluation"):
        diagnostic = failure_diagnostic(error, grant["title"], stage=stage)
        failures.append(diagnostic)
        if on_failure:
            on_failure(diagnostic)

    pending = [grant for grant in candidates if grant["title"] not in analyses]
    completed = len(candidates) - len(pending)
    if on_progress:
        on_progress(completed, len(candidates))
    if max_workers == 1:
        for grant in pending:
            print(f"Reviewing opportunity: {grant['title']}")
            try:
                analyses[grant["title"]] = matcher.match(profile, idea, grant).model_dump(mode="json")
            except Exception as failure:
                record_failure(failure, grant)
            completed += 1
            if on_progress:
                on_progress(completed, len(candidates))
    elif pending:
        # Workers only call the matcher; cache writes and UI updates stay on the caller thread.
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as executor:
            futures = {
                executor.submit(matcher.match, profile, idea, grant): grant
                for grant in pending
            }
            for future in as_completed(futures):
                grant = futures[future]
                try:
                    analyses[grant["title"]] = future.result().model_dump(mode="json")
                except Exception as failure:
                    record_failure(failure, grant)
                completed += 1
                if on_progress:
                    on_progress(completed, len(candidates))

    def rank(grant):
        analysis = analyses[grant["title"]]
        overall = analysis["overall_result"]
        return (
            {"Poor fit": 0, "Potential fit": 1, "Strong fit": 2}[overall["overall_assessment"]],
            not analysis["hard_constraint"]["triggered"],
            overall["match_percentage"],
        )

    valid = []
    for grant in candidates:
        if grant["title"] not in analyses:
            continue
        try:
            rank(grant)
        except Exception as failure:
            record_failure(failure, grant, stage="result collection/ranking")
            analyses.pop(grant["title"], None)
        else:
            valid.append(grant)
    if not valid:
        raise SearchEvaluationError(failures)
    return max(valid, key=rank)


def main():
    profile = matcher.load_json("profile.json")
    ideas = matcher.load_json("funding_needs.json")
    grants = matcher.load_json("grants.json")
    idea = ideas[select("Select research idea", [item["title"] for item in ideas])]
    available = available_grants(idea, grants)
    if not available:
        print("There are currently no other available opportunities for this research idea.")
        return
    grant = available[select("Select grant opportunity", [item["title"] for item in available])]
    print("\nAssessing this opportunity...")
    analyses = {grant["title"]: matcher.match(profile, idea, grant).model_dump(mode="json")}
    while True:
        analysis = analyses[grant["title"]]
        print(f"\nOpportunity: {grant['title']}")
        display_match(analysis)
        decisions = opportunity_workflow.DECISIONS
        decision = decisions[select("Your decision", decisions)]
        opportunity = opportunity_workflow.record_decision(
            idea["title"], grant["title"], decision, match_analysis=analysis
        )
        if decision != "Reject":
            break
        print("\nOpportunity rejected")
        print("This opportunity has been marked as Not pursuing.")
        print("\nLooking for other relevant opportunities for this research idea…")
        grant = best_alternative(profile, idea, grants, grant["title"], analyses)
        if grant is None:
            print("There are currently no other available opportunities for this research idea.")
            return
        print(f"\nAlternative opportunity found: {grant['title']}")
    print("\nSaved for review." if decision == "Save for review" else "\nOpportunity saved.")
    print(f"Status: {opportunity['current_status']}")
    print(f"Match percentage: {opportunity['match_percentage']}%")
    print(f"Deadline: {opportunity['deadline']}")
    print("Application requirements")
    for requirement in opportunity["application_requirements_checklist"]:
        print(f"- {requirement}")
    print(f"Grant website: {opportunity['grant_website_url']}")
    print(f"Suggested next step: {opportunity['suggested_next_step']}")
    if decision == "Save for review":
        print("Key unresolved points to investigate")
        for point in opportunity["key_unresolved_points"]:
            print(f"- {presentation_text(point)}")
        return
    if select("Generate an initial draft?", ["Yes", "No"]) == 0:
        print("\nGenerating initial draft...\n")
        result = grant_drafter.generate_draft(idea["title"], grant["title"])
        grant_drafter.display_draft(result)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, OpenAIError, EOFError) as error:
        print("Unable to complete this step. Please try again.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(1)
