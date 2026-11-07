"""Streamlit presentation layer for the existing funding workflow."""

import re
import json
import hashlib
from uuid import uuid4
import base64
import mimetypes
from pathlib import Path
from random import choice
from time import monotonic
from calendar import monthrange
from math import ceil
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import streamlit as st
from openai import OpenAIError

import grant_drafter
import matcher
import opportunity_workflow
import prototype
import research_notebook
import notebook_store
import pilot_storage
import pilot_access
from ui_styles import apply_design_system

# Streamlit reruns this script while Python can retain an older storage module
# from before entry titles/deletion were added. Refresh that module only when
# its required entry API is absent; normal reruns keep the existing module.
if not all(hasattr(notebook_store, name) for name in ("entry_title", "delete_entry")):
    from importlib import reload
    reload(notebook_store)
if not all(hasattr(opportunity_workflow, name) for name in ("save_application_draft", "edit_application_draft")):
    from importlib import reload
    reload(opportunity_workflow)


# Text identities avoid network-dependent logos and remain readable offline.
FUNDER_IDENTITIES = {
    "Medical Research Council (MRC), UKRI": "MRC · UKRI",
    "Biotechnology and Biological Sciences Research Council (BBSRC), UKRI": "BBSRC · UKRI",
    "Wellcome": "Wellcome",
}

# Optional official local assets; an unmapped/unavailable logo renders no slot.
FUNDER_LOGO_ASSETS = {}


def funder_logo_markup(funder):
    """Reusable, network-free logo slot with a clean text-only fallback."""
    asset = FUNDER_LOGO_ASSETS.get(funder)
    if not asset:
        return ""
    path = Path(asset)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    try:
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}:
            return ""
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return (
        f'<img class="funding-funder-logo" src="data:{mime};base64,{encoded}" '
        f'alt="{escape(funder or "Funder", quote=True)} logo">'
    )


def show_funding_heading(grant, analysis, description=""):
    """Funding identity and optional logo beside the existing match result."""
    st.html("""<style>
        .funding-funder-logo {width:48px;height:48px;object-fit:contain;flex:0 0 48px;}
        .funding-identity {display:flex;align-items:center;gap:16px;color:var(--text);}
        .funding-identity strong {font-size:14px;}
        .st-key-funding_header_identity h2 {padding:0;margin:0;overflow-wrap:anywhere;}
        .st-key-funding_header_identity {gap:8px;}
        /* Keep the title's full wrapped height in normal flow; Streamlit's
           markdown bottom margin otherwise pulls the website action upward. */
        .st-key-funding_header_identity [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        .st-key-funding_header_controls {gap:8px;padding-top:8px;
            --funding-control-width:14rem;}
        .st-key-funding_header_controls [data-testid="stLinkButton"] a,
        .st-key-funding_header_controls [data-testid="stExpander"] summary {
            width:var(--funding-control-width);max-width:100%;box-sizing:border-box;
            height:36px;min-height:36px;padding:4px 12px;
            font-size:14px;font-weight:400;line-height:1.4;
            border:1px solid var(--border);border-radius:8px;
            background:var(--surface);color:var(--text);
            display:flex;align-items:center;justify-content:center;gap:8px;
        }
        .st-key-funding_header_controls [data-testid="stExpander"] details {
            border:0;background:transparent;
        }
        .st-key-funding_header_controls summary [data-testid="stMarkdownContainer"],
        .st-key-funding_header_controls summary p,
        .st-key-funding_header_controls a [data-testid="stMarkdownContainer"],
        .st-key-funding_header_controls a p {
            font-size:14px !important;font-weight:400;line-height:1.4;color:inherit;margin:0;
        }
        .st-key-funding_header_controls summary > span,
        .st-key-funding_header_controls a [data-has-shortcut] {
            display:inline-flex;align-items:center;justify-content:center;gap:8px;
            width:auto;flex:0 0 auto;
        }
        .st-key-funding_header_controls summary > span {width:max-content;max-width:none;overflow:visible;}
        .st-key-funding_header_controls summary > span > div {width:auto;max-width:none;}
        .st-key-funding_header_controls summary > span > div,
        .st-key-funding_header_controls summary [data-testid="stMarkdownContainer"] {flex:0 0 auto;}
        .st-key-funding_header_controls [data-testid="stIconMaterial"] {
            font-size:16px;width:16px;height:16px;line-height:16px;
        }
        .st-key-funding_header_controls [data-testid="stLinkButton"] a:hover,
        .st-key-funding_header_controls [data-testid="stExpander"] summary:hover {
            background:var(--sage-light);color:var(--text);border-color:var(--sage);
        }
        .st-key-funding_header_controls [data-testid="stExpander"] details > div {
            padding:16px 0 0;
        }
        .st-key-funding_header_row {margin-bottom:16px;}
        @media(max-width:700px) {
            .st-key-funding_header_row {flex-direction:column;}
            .st-key-funding_header_row > [data-testid="stColumn"] {width:100%;flex:1 1 auto;}
        }
    </style>""")
    identity = FUNDER_IDENTITIES.get(grant.get("funder"), grant.get("funder") or "Funder")
    with st.container(key="funding_header_row"):
        left, right = st.columns([3, 2], vertical_alignment="center")
        with left, st.container(key="funding_header_identity"):
            st.html(
                '<div class="funding-identity">'
                + funder_logo_markup(grant.get("funder"))
                + f'<strong>{escape(identity)}</strong></div>'
            )
            st.markdown(f'<h2>{escape(grant["title"])}</h2>', unsafe_allow_html=True)
            with st.container(key="funding_header_controls"):
                if grant.get("source_url"):
                    st.link_button("Visit grant website", grant["source_url"], icon=":material/open_in_new:", type="secondary")
                if description:
                    with st.expander("About this grant", expanded=False):
                        st.write(description)
        with right:
            st.html(match_result_markup(analysis["overall_result"]))


FIT_PRESENTATION = {
    "Poor fit": ("", "var(--danger-bg)", "var(--danger)"),
    "Potential fit": ("", "var(--warning-bg)", "var(--warning)"),
    "Strong fit": ("", "var(--success-bg)", "var(--success)"),
}

RECOMMENDATION_PRESENTATION = {
    "Pursue": FIT_PRESENTATION["Strong fit"],
    "Investigate further": FIT_PRESENTATION["Potential fit"],
    "Reject": FIT_PRESENTATION["Poor fit"],
}


def header_detail(value):
    """Omit unavailable details while retaining any substantive source text."""
    value = (value or "").strip()
    value = re.sub(
        r"^(?:No fixed maximum specified|Not specified|None specified) in the supplied information\.\s*",
        "", value,
    )
    return value


def show_opportunity_card_heading(grant, result_markup, *, compact=False):
    """Shared identity, title and result layout for funding and applications."""
    identity = escape(FUNDER_IDENTITIES.get(grant.get("funder"), grant.get("funder") or "Funder"))
    identity_spacing = 8 if compact else 16
    title_spacing = 4 if compact else 16
    funder = (f'<div style="font-size:14px;color:var(--secondary);line-height:1.5;margin-top:4px;">{escape(grant["funder"])}</div>'
              if compact and grant.get("funder") else "")
    st.markdown(
            f'<div style="margin-bottom:{identity_spacing}px;"><strong>{identity}</strong></div>'
            '<div style="display:flex;flex-wrap:wrap;align-items:center;'
            f'justify-content:space-between;gap:16px;margin-bottom:{title_spacing}px;">'
            '<div style="flex:1 1 320px;min-width:0;"><h2 style="margin:0;padding:0;">'
            f'{escape(grant["title"])}</h2>{funder}</div>'
            f'{result_markup}</div>',
            unsafe_allow_html=True,
    )
    if grant.get("funder") and not compact:
        st.caption(grant["funder"])


def show_card_metadata(details):
    if details:
        for column, (label, value) in zip(st.columns(len(details)), details):
            with column:
                st.markdown(f"**{label}**")
                st.write(value)


def funding_metadata_summary(label, value):
    """Split existing source wording into a scan value and supporting detail.

    Unknown formats retain their source wording; no inferred amounts or dates.
    """
    text = value.strip().removesuffix(".")
    if label == "Funding":
        average = re.search(r"Average award approximately (£[\d,.]+) (million|billion|thousand)", text, re.I)
        if average:
            suffix = {"million": "m", "billion": "bn", "thousand": "k"}[average.group(2).lower()]
            primary = f"~{average.group(1)}{suffix} average"
            supporting = (text[:average.start()] + text[average.end():]).strip()
            supporting = re.sub(r"\.\s*\.", ".", supporting).strip(" .")
            return primary, supporting
        parts = re.split(r"(?<=\.)\s+", text, maxsplit=1)
        first = parts[0].removesuffix(".")
        if first.startswith("Up to ") and "," in first:
            primary, detail = first.split(",", 1)
            return primary, " ".join([detail.strip(), *parts[1:]])
        return first, parts[1] if len(parts) > 1 else ""
    if label == "Duration":
        primary, separator, supporting = text.partition(";")
        if not separator:
            parts = re.split(r"(?<=\.)\s+", text, maxsplit=1)
            primary = parts[0].removesuffix(".")
            supporting = parts[1] if len(parts) > 1 else ""
        supporting = supporting.strip()
        return primary, supporting[:1].upper() + supporting[1:]
    if label == "Deadline":
        primary, separator, supporting = text.partition(";")
        if not separator:
            primary, _, supporting = text.partition(",")
        supporting = supporting.strip()
        return primary.strip(), supporting[:1].upper() + supporting[1:]
    return text, ""


def show_funding_metadata(details):
    """Native disclosure cards: source values first, supporting detail on demand."""
    cards = []
    for label, value in details:
        primary, supporting = funding_metadata_summary(label, value)
        cards.append(
            f'<details class="funding-metadata-card funding-metadata-{label.lower()}">'
            '<summary>'
            f'<span class="funding-metadata-category">{escape(label)}</span>'
            f'<span class="funding-metadata-value">{escape(primary)}</span>'
            '<span class="funding-metadata-chevron" aria-hidden="true">⌄</span>'
            '</summary>'
            f'<p class="funding-metadata-detail">{escape(supporting)}</p></details>'
        )
    if not cards:
        return
    st.html(
        '<style>'
        '.funding-metadata-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));'
        'gap:16px;align-items:start;}'
        '.funding-metadata-card {text-align:center;border:1px solid var(--border);'
        'border-radius:12px;min-width:0;background:var(--elevated);color:var(--text);overflow:hidden;}'
        '.funding-metadata-funding {background:var(--success-bg);}'
        '.funding-metadata-duration {background:var(--sage-light);}'
        '.funding-metadata-deadline {background:var(--warning-bg);}'
        '.funding-metadata-card summary {display:grid;grid-template-rows:18px minmax(0,1fr) 20px;'
        'gap:8px;height:150px;box-sizing:border-box;padding:12px;list-style:none;'
        'cursor:pointer;text-align:center;}'
        '.funding-metadata-card:not([open]) summary {height:112px;padding:4px 12px;gap:4px;}'
        '.funding-metadata-card summary::-webkit-details-marker {display:none;}'
        '.funding-metadata-card summary::marker {content:"";}'
        '.funding-metadata-card summary:focus-visible {outline:2px solid var(--brand);outline-offset:-4px;border-radius:12px;}'
        '.funding-metadata-category {font-size:12px;font-weight:600;letter-spacing:.04em;'
        'text-transform:uppercase;line-height:18px;color:var(--secondary);}'
        '.funding-metadata-value {align-self:center;font-size:22px;font-weight:600;line-height:1.3;'
        'color:var(--text);overflow-wrap:anywhere;}'
        '.funding-metadata-chevron {font-size:20px;line-height:20px;color:var(--secondary);}'
        '.funding-metadata-card[open] .funding-metadata-chevron {transform:rotate(180deg);}'
        '.funding-metadata-card .funding-metadata-detail {margin:0;padding:0 16px 16px;'
        'text-align:center;overflow-wrap:anywhere;font-size:13px;font-weight:400;'
        'line-height:1.5;color:var(--secondary);}'
        '@media(max-width:700px) {.funding-metadata-grid {grid-template-columns:minmax(0,1fr);}}'
        '</style><div class="funding-metadata-grid">'+"".join(cards)+'</div>'
    )


def show_opportunity_header(grant, analysis):
    """Present source-only context before the match assessment."""
    with st.container(border=True, key="ds_card_funding"):
        # Use source sentences directly; no generated context or extra claims.
        context = [
            header_detail(grant.get("purpose")),
            header_detail(grant.get("eligibility")),
            header_detail(grant.get("scope")),
        ]
        summary = [re.split(r"(?<=\.)\s+", text, maxsplit=1)[0] for text in context if text]
        show_funding_heading(grant, analysis, " ".join(summary))
        details = [
            (label, header_detail(grant.get(field)))
            for label, field in (("Funding", "funding"), ("Duration", "duration"), ("Deadline", "deadline"))
        ]
        details = [(label, value) for label, value in details if value]
        show_funding_metadata(details)


def change_route(route):
    st.session_state.route = route
    st.rerun()


def landing():
    # Scoped entry-screen styling leaves every other screen and theme rule intact.
    st.html("""
        <style>
        [data-testid="stMainBlockContainer"]:has(.st-key-entry_experience) {
            max-width:760px;min-height:calc(100vh - 64px);
            display:flex;flex-direction:column;justify-content:center;
            padding-top:96px;padding-bottom:48px;
        }
        .st-key-entry_experience {width:100%;max-width:680px;margin:0 auto;gap:32px;}
        .st-key-entry_experience h1 {text-align:center;padding:0;margin:0;}
        .st-key-entry_experience [data-testid="stHorizontalBlock"] {gap:24px;align-items:stretch;}
        .st-key-entry_experience :is(.st-key-ds_card_researcher,.st-key-ds_card_charity) {
            padding:32px 24px;border-radius:12px;text-align:center;gap:24px;
        }
        .st-key-entry_experience .entry-persona-icon {font-size:40px;line-height:48px;height:48px;text-align:center;filter:none;opacity:1;}
        .st-key-entry_experience .entry-persona-title {font-size:18px;font-weight:600;margin:16px 0;text-align:center;}
        .st-key-entry_experience .entry-persona-description {height:96px;display:flex;align-items:center;justify-content:center;text-align:center;}
        .st-key-entry_experience .entry-persona-description p {margin:0;max-width:250px;color:var(--secondary);text-align:center;}
        .st-key-entry_experience button[kind="primary"] {
            width:100%;height:44px;background:#176B52;border-color:#176B52;
            color:#FFFFFF;border-radius:8px;font-weight:600;
        }
        .st-key-entry_experience button[kind="primary"]:hover {
            background:#10513E;border-color:#10513E;color:#FFFFFF;
        }
        .st-key-entry_experience button p {color:inherit;}
        @media(max-width:640px) {
            .st-key-entry_experience [data-testid="stHorizontalBlock"] {gap:16px;}
        }
        </style>
    """)
    with st.container(key="entry_experience"):
        st.title("Who are you?")
        researcher, charity = st.columns(2)
        for column, title, icon, description, card_key, route in (
            (researcher, "Researcher", "🔬", "Find and manage funding opportunities for your research ideas", "ds_card_researcher", "researcher"),
            (charity, "NGO", "🌍", "Find and manage funding opportunities for your projects and funding needs", "ds_card_charity", "charity"),
        ):
            with column:
                with st.container(border=True, key=card_key):
                    st.markdown(
                        f'<div class="entry-persona-icon" aria-hidden="true">{icon}</div>'
                        f'<div class="entry-persona-title">{title}</div>'
                        f'<div class="entry-persona-description"><p>{description}</p></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("I'm a Researcher" if route == "researcher" else "I'm with an NGO", key=f"entry_{route}", type="primary", use_container_width=True):
                        change_route(route)


def match_result_markup(overall):
    """Compact result treatment that wraps below the identity on narrow cards."""
    indicator, background, accent = FIT_PRESENTATION.get(
        overall["overall_assessment"], ("", "var(--elevated)", "var(--secondary)")
    )
    return result_card_markup(
        f"{overall['match_percentage']}% — {overall['overall_assessment']}",
        f"Total score: {overall['total_score']}/{overall['maximum_score']}",
        (indicator, background, accent),
    )


def result_card_markup(headline, secondary, presentation):
    """One visual treatment for match results and application progress."""
    indicator, background, accent = presentation
    return (
        f'<div style="background:{background};border-left:4px solid {accent};'
        'flex:0 1 auto;max-width:100%;box-sizing:border-box;'
        'border-radius:8px;padding:16px;color:var(--text);">'
        f'<h3 style="margin:0 0 4px;padding:0;font-size:1.125rem;color:var(--text);">'
        f'<span aria-hidden="true">{indicator}</span> {escape(headline)}</h3>'
        f'<p style="margin:0;font-size:0.875rem;color:var(--text);">{escape(secondary)}</p></div>'
    )


def show_match(analysis):
    st.header("Match analysis")
    show_scored_match_analysis(analysis)
    show_match_recommendation(analysis)


def show_scored_match_analysis(analysis):
    """Shared scoring presentation for original and alternative opportunities."""
    overall = analysis["overall_result"]
    thresholds = "".join(
        f'<span class="match-fit-band" style="background:{FIT_PRESENTATION[label][1]};'
        f'border-color:{FIT_PRESENTATION[label][2]};">{escape(label)} {escape(band)}</span>'
        for band, label in analysis["scoring_guide"]["assessment_thresholds"].items()
    )
    st.html(
        '<style>'
        '[data-testid="stMainBlockContainer"]:has(.st-key-ds_card_funding) {max-width:960px;}'
        '[data-testid="stMainBlockContainer"]:has(.st-key-ds_card_funding) [data-testid="stMarkdownContainer"] > p {max-width:80ch;}'
        '.match-scoring-summary {display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;'
        'font-size:14px;line-height:1.5;color:var(--text);margin-bottom:16px;}'
        '.match-fit-bands {display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;}'
        '.match-fit-band {font-size:12px;font-weight:600;line-height:1.5;'
        'border:1px solid;border-radius:999px;padding:4px 8px;color:var(--text);}'
        '.st-key-match_criterion_poor [data-testid="stExpander"],'
        '[class*="st-key-match_criterion_poor_"] [data-testid="stExpander"] {border-left:3px solid var(--danger);}'
        '[class*="st-key-match_criterion_potential_"] [data-testid="stExpander"] {border-left:3px solid var(--warning);}'
        '[class*="st-key-match_criterion_strong_"] [data-testid="stExpander"] {border-left:3px solid var(--success);}'
        '.stApp .st-key-decision_pursue button[kind="secondary"] {background:var(--brand);border-color:var(--brand);color:light-dark(#FFFFFF,#151917);}'
        '.stApp .st-key-decision_review button[kind="secondary"] {background:var(--warning-bg);border-color:var(--warning);color:var(--text);}'
        '.stApp .st-key-decision_reject button[kind="secondary"] {background:var(--danger-bg);border-color:var(--danger);color:var(--text);}'
        '.stApp :is(.st-key-decision_pursue,.st-key-decision_review,.st-key-decision_reject) button {min-height:48px;font-weight:600;}'
        '</style><div class="match-scoring-summary" aria-label="Scoring summary">'
        '<span>Each criterion: <strong>1–5</strong></span><span aria-hidden="true">→</span>'
        f'<span>Total: <strong>{overall["total_score"]}/{overall["maximum_score"]}</strong></span>'
        '<span aria-hidden="true">→</span>'
        f'<span>Match: <strong>{overall["match_percentage"]}%</strong></span>'
        '<span aria-hidden="true">→</span>'
        f'<strong>{escape(overall["overall_assessment"])}</strong></div>'
        f'<div class="match-fit-bands">{thresholds}</div>'
    )
    show_match_criteria(analysis)


def show_match_recommendation(analysis):
    recommendation = analysis["suggested_next_action"]
    indicator, background, accent = RECOMMENDATION_PRESENTATION.get(
        recommendation, ("", "var(--elevated)", "var(--secondary)")
    )
    st.html(
        f'<section style="background:{background};border:1px solid {accent};border-left:4px solid {accent};'
        'border-radius:12px;padding:24px;color:var(--text);">'
        f'<h3 style="margin:0;padding:0;font-size:18px;font-weight:600;">Our recommendation: {escape(recommendation)}</h3>'
        '<h4 style="margin:16px 0 8px;padding:0;font-size:16px;font-weight:600;">Why we recommend this</h4>'
        f'<p style="margin:0;line-height:1.5;max-width:80ch;">{escape(prototype.concise_explanation(analysis["summary"]))}</p></section>'
    )
    if analysis["suggested_next_action"] == "Investigate further":
        st.info("We recommend saving this opportunity for review while you investigate these points. You’ll be reminded on a schedule you choose, so the opportunity doesn’t get forgotten as the deadline approaches. Reminders can be delivered by email or calendar.")
    constraint = analysis["hard_constraint"]
    if constraint["triggered"]:
        st.warning(f"Important: {prototype.concise_explanation(constraint['explanation'])}")


def show_match_criteria(analysis):
    labels = {
        "Eligibility": "Eligibility", "Research/activity fit": "Research fit",
        "Purpose fit": "Purpose fit", "Development/readiness fit": "Readiness",
        "Capability fit": "Capability", "Requirements/constraints": "Requirements",
    }
    st.caption("Expand each criterion to see why it received this score")
    for index, item in enumerate(analysis["criterion_assessments"]):
        band = "poor" if item["score"] <= 2 else "potential" if item["score"] == 3 else "strong"
        with st.container(key=f"match_criterion_{band}_{index}"):
            with st.expander(
                f"{labels.get(item['criterion'], item['criterion'])} — {item['score']}/{item['maximum_score']}",
                expanded=False,
            ):
                st.write(prototype.presentation_text(item["reasoning"]))


FUNDING_INSIGHTS = (
    ('Did you know?', 'Some funders discover projects through their personal networks and recommendations, not just formal grant applications.', 'Build relationships with relevant funders and research networks before you need to apply.'),
    ('Funding can hide in plain sight.', 'Funders may advertise open calls for proposals through their websites, networks and other channels.', 'Monitor relevant funders and funding channels regularly rather than relying on a single source.'),
    ('Check before you apply.', "One of the biggest frustrations for funders is receiving applications that don't meet their eligibility criteria.", 'Check the eligibility criteria before investing significant time in an application.'),
    ('Using AI?', 'Some funders require you to disclose AI use, while others may prohibit it. Always check the guidelines.', "Check the funder's AI policy before using AI to prepare any part of the application."),
    ('AI is a starting point, not the finish line.', 'Funders still expect you to review and tailor your answers to the specific application.', "Treat AI-generated content as a first draft and review every answer against the funder's requirements."),
    ("Vetted doesn't mean suitable.", "Even a strong organisation or project still needs to match the funder's priorities, budget and region.", "Check the opportunity against your project, location, budget and the funder's priorities before deciding to pursue it."),
    ("A great project isn't always enough.", 'Funders may also look at things like governance and financial systems when assessing risk.', 'Keep key organisational, governance and financial information current and ready for due diligence.'),
    ("Don't overlook unsolicited funding routes.", 'Some funders accept unsolicited proposals throughout the year, rather than only through open calls.', 'Check whether relevant funders accept direct approaches even when no open call is advertised.'),
    ('Watch the small print.', 'Grant answers may have strict word or character limits.', 'Record application limits early and draft within them rather than cutting large answers down at the end.'),
    ('Think beyond the application.', "Funders may want to understand how you'll measure and demonstrate impact if they fund you.", 'Define how you will measure success before writing the application.'),
)


def funding_insight_markup(insight):
    """Content only: inline style tags are hidden by the shared UI stylesheet."""
    opening, body, action = insight
    opening, body, action = (text.removesuffix(".") for text in (opening, body, action))
    return (
        '<div class="funding-insight-card"><h3>💡 Funding insight</h3>'
        '<div class="funding-insight-window">'
        '<div class="funding-insight-text">'
        f'<p class="funding-insight-headline">{escape(opening)}</p>'
        f'<p class="funding-insight-description">{escape(body)}</p></div>'
        f'<p class="funding-insight-action"><span class="funding-insight-takeaway"><span aria-hidden="true">→</span><span>{escape(action)}</span></span></p>'
        '</div></div>'
    )


def show_search_diagnostics(workflow):
    diagnostics = workflow.get("search_diagnostics", [])
    if diagnostics:
        with st.expander("Development / testing: search diagnostics", expanded=False):
            for item in diagnostics:
                st.write(item)


def find_opportunity(profile, idea, grants, workflow, *, status_label="Finding funding opportunities…"):
    """Use the existing ranking, rejection filtering and per-idea match cache."""
    workflow["search_error"] = False
    workflow["search_diagnostics"] = []

    def record_failure(diagnostic):
        workflow["search_diagnostics"].append(diagnostic)

    st.html("""
        <style>
        .funding-insight-card {
            background:light-dark(#EDF4F1,#203A30);
            border:1px solid light-dark(#CCDCD4,#3B5A4B);border-radius:12px;
            padding:16px 24px;margin:24px auto;max-width:calc(100% - 48px);
            box-sizing:border-box;color:light-dark(#202421,#F1F4F2);
            font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        }
        .funding-insight-card h3 {font-size:13px;font-weight:600;line-height:1.4;padding:0;margin:0 0 12px;color:inherit;}
        .funding-insight-card p {font-size:14px;font-weight:400;line-height:1.5;margin:0;color:inherit;}
        .funding-insight-card .funding-insight-headline {font-size:15px;font-weight:500;margin-bottom:4px;}
        .funding-insight-card .funding-insight-action {
            border-top:1px solid light-dark(#CCDCD4,#3B5A4B);margin-top:12px;padding-top:12px;}
        .funding-insight-takeaway {display:flex;align-items:baseline;gap:8px;
            background:light-dark(#E3EEE7,#294638);border-radius:8px;padding:8px 12px;font-weight:600;}
        .funding-insight-takeaway > span:first-child {color:light-dark(#626A65,#B6BDB9);flex-shrink:0;}
        [data-testid="stMarkdownContainer"]:has(.funding-insight-card) {margin-bottom:0;}
        .funding-insight-window {overflow:hidden;width:100%;}
        .funding-insight-text {animation:funding-insight-enter 350ms ease-out;}
        @keyframes funding-insight-enter {
            from {transform:translateX(24px);}
            to {transform:translateX(0);}
        }
        @media(prefers-reduced-motion:reduce) {.funding-insight-text {animation:none;}}
        </style>
    """)
    try:
        with st.status(status_label, expanded=True) as status:
            st.write("Evaluating opportunities against your research idea…")
            progress = st.empty()
            insight_panel = st.empty()
            current_insight = choice(FUNDING_INSIGHTS)
            insight_updated_at = monotonic()
            def render_insight():
                try:
                    insight_panel.markdown(funding_insight_markup(current_insight), unsafe_allow_html=True)
                except Exception as failure:
                    record_failure(prototype.failure_diagnostic(failure, stage="insight rendering"))

            render_insight()

            def update_loading(completed, total):
                nonlocal current_insight, insight_updated_at
                if completed < total:
                    progress.write(f"Evaluating opportunity {completed + 1} of {total}…")
                else:
                    progress.write("Opportunity evaluations complete")
                # UI-only rotation on existing callbacks; never wait or rerun the search.
                if completed < total and monotonic() - insight_updated_at >= 8:
                    current_insight = choice([item for item in FUNDING_INSIGHTS if item != current_insight])
                    insight_updated_at = monotonic()
                    render_insight()

            def show_progress(completed, total):
                try:
                    update_loading(completed, total)
                except Exception as failure:
                    record_failure(prototype.failure_diagnostic(failure, stage="loading progress"))

            grant = prototype.best_alternative(
                profile, idea, grants, workflow.get("rejected_title", ""),
                workflow["analyses"],
                max_workers=4, on_progress=show_progress, on_failure=record_failure,
            )
            status.update(label="Review complete", state="complete", expanded=False)
    except Exception as failure:
        if not isinstance(failure, prototype.SearchEvaluationError):
            record_failure(prototype.failure_diagnostic(failure, stage="overall search"))
        workflow["search_error"] = True
        if workflow.get("alternative_state"):
            workflow["alternative_state"] = "searching_for_alternative"
            st.rerun()
        st.error("Unable to finish reviewing opportunities. Please try again.")
        show_search_diagnostics(workflow)
        return
    workflow["grant"] = grant
    workflow["searched"] = True
    workflow["decision"] = None
    workflow["opportunity"] = None
    workflow["draft"] = None
    if workflow.get("alternative_state"):
        workflow["alternative_state"] = "alternative_found" if grant is not None else "no_alternative_found"
        workflow["alternative_index"] = 0
    st.rerun()


def show_tracking(workflow, idea, grant):
    opportunity = workflow["opportunity"]
    st.success("Saved for review" if workflow["decision"] == "Save for review" else "Opportunity saved")
    st.write(f"**Status:** {opportunity['current_status']}")
    st.write(f"**Next step:** {opportunity['suggested_next_step']}")
    st.write(f"**Deadline:** {opportunity['deadline']}")
    with st.expander("Application requirements", expanded=True):
        for requirement in opportunity["application_requirements_checklist"]:
            st.write(f"- {requirement}")
    st.link_button("Grant website", opportunity["grant_website_url"])
    if workflow["decision"] == "Save for review":
        st.subheader("Key unresolved points to investigate")
        for point in opportunity["key_unresolved_points"]:
            st.write(f"- {prototype.presentation_text(point)}")
        return
    show_draft(workflow, idea["title"], grant["title"])


def show_draft_action(workflow, idea_title, grant_title, *, label="Generate initial draft", workspace=False):
    if workflow["draft"] is None and st.button(label, type="primary"):
        loading = st.empty()
        try:
            if workspace:
                with loading.container(), st.container(key="workspace_draft_loading"):
                    st.html("""<style>.st-key-workspace_draft_loading {background:var(--brand-subtle);
                        border:1px solid var(--sage);border-radius:12px;padding:16px;}
                        .st-key-workspace_draft_loading [data-testid="stStatusWidget"] {border:0;background:transparent;}
                        </style><div style="font-size:32px;line-height:1.2;" role="img" aria-label="Drafting assistant">🤖</div>""")
                    with st.status("Just a moment, I’m working on your draft", expanded=True):
                        st.caption("Bringing your research idea and funding requirements together")
                        result = grant_drafter.generate_draft(idea_title, grant_title)
                        opportunity_workflow.save_application_draft(idea_title, grant_title,
                            result.initial_application_draft, result.missing_information_pi_input_required)
                workflow["draft"] = result
            else:
                with st.spinner("Generating initial draft…"):
                    workflow["draft"] = grant_drafter.generate_draft(idea_title, grant_title)
        except (OSError, ValueError, KeyError, OpenAIError):
            st.error("Unable to generate the draft. Please try again.")
        finally:
            loading.empty()


def show_draft(workflow, idea_title, grant_title, *, show_action=True):
    if show_action:
        show_draft_action(workflow, idea_title, grant_title)
    if workflow["draft"] is not None:
        draft = workflow["draft"]
        st.subheader("Initial application draft")
        st.write(draft.initial_application_draft)
        st.subheader("Missing information / PI input required")
        for item in draft.missing_information_pi_input_required:
            st.write(f"- {item}")
        if not draft.missing_information_pi_input_required:
            st.write("None identified from the supplied requirements")


def show_funding_research_context(idea):
    """Compact source-only idea context for the results state."""
    st.html(
        '<style>'
        '.funding-research-context {display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;'
        'background:light-dark(#10513E,#203A30);color:light-dark(#FFFFFF,#F1F4F2);'
        'border:1px solid light-dark(#10513E,#343D38);border-radius:12px;padding:16px 24px;}'
        '.funding-research-context .context-label {font-size:12px;font-weight:600;'
        'letter-spacing:0.08em;color:light-dark(#E7F2ED,#B6BDB9);flex-shrink:0;}'
        '.funding-research-context .context-title {font-size:16px;line-height:1.5;'
        'font-weight:600;flex:1 1 240px;min-width:0;color:inherit;overflow-wrap:anywhere;}'
        '</style><section class="funding-research-context" aria-label="Selected research idea">'
        '<div class="context-label">RESEARCH IDEA</div>'
        f'<div class="context-title">{escape(idea["title"])}</div></section>'
    )


def find_funding(main_view):
    if st.session_state.get("alternatives_exit_dialog"):
        alternatives_exit_dialog()
        return
    profile = matcher.load_json("profile.json")
    ideas = matcher.load_json("funding_needs.json")
    grants = matcher.load_json("grants.json")
    if not ideas:
        st.title("Find Funding")
        st.info("No research ideas are currently available")
        return
    titles = [idea["title"] for idea in ideas]
    previous = st.session_state.get("selected_idea", titles[0])
    previous_workflow = st.session_state.researcher_workflows.get(previous, {})
    # Preserve in-progress sessions created by the earlier transition UI.
    legacy_states = {
        "searching": "searching_for_alternative", "error": "searching_for_alternative",
        "result": "alternative_found", "empty": "no_alternative_found",
    }
    if previous_workflow.get("alternative_state") in legacy_states:
        previous_workflow["alternative_state"] = legacy_states[previous_workflow["alternative_state"]]
    if previous_workflow.get("alternative_state"):
        show_search_diagnostics(previous_workflow)
        idea = next(item for item in ideas if item["title"] == previous)
        show_alternative_transition(profile, idea, grants, previous_workflow, main_view)
        return
    st.title("Find Funding")
    st.caption("Research Idea → Funding Opportunity → Match → Decision → Track → Draft")
    if previous_workflow.get("grant") is not None:
        with st.expander("Change research idea", expanded=False):
            selected = st.selectbox("Choose a research idea", titles, index=titles.index(previous) if previous in titles else 0)
    else:
        selected = st.selectbox("Choose a research idea", titles, index=titles.index(previous) if previous in titles else 0)
    st.session_state.selected_idea = selected
    idea = next(item for item in ideas if item["title"] == selected)
    workflow = st.session_state.researcher_workflows.setdefault(selected, {
        "analyses": {}, "grant": None, "decision": None,
        "opportunity": None, "draft": None, "searched": False,
        "search_error": False, "rejected_title": "",
    })
    if workflow["grant"] is None:
        st.html(
            '<style>'
            '.selected-research-context {background:light-dark(#EDF4F1,#203A30);'
            'border:1px solid light-dark(#7FA996,#4EAD88);border-radius:12px;'
            'padding:24px;color:light-dark(#202421,#F1F4F2);}'
            '.selected-research-context .context-label {color:var(--brand);'
            'font-size:12px;font-weight:600;letter-spacing:0.08em;margin-bottom:8px;}'
            '.selected-research-context p {margin:0;line-height:1.5;color:inherit;overflow-wrap:anywhere;}'
            '</style><section class="selected-research-context" aria-label="Selected research idea">'
            '<div class="context-label">SELECTED RESEARCH IDEA</div>'
            f'<p>{escape(idea["proposed_research"])}</p></section>'
        )
    else:
        show_funding_research_context(idea)
    show_search_diagnostics(workflow)
    if workflow["grant"] is None:
        if workflow["search_error"]:
            st.info("The search is incomplete. Your decision has been retained.")
        elif workflow["searched"]:
            st.info("There are currently no other available opportunities for this research idea")
            return
        if st.button("Find funding opportunities", type="primary"):
            find_opportunity(profile, idea, grants, workflow)
        return
    show_funding_result(profile, idea, grants, workflow, main_view)


def show_alternative_transition(profile, idea, grants, workflow, main_view):
    state = workflow["alternative_state"]
    if state == "searching_for_alternative":
        st.markdown(
            '<div style="min-height:240px;display:flex;flex-direction:column;'
            'justify-content:center;text-align:center;padding:24px 0;">'
            '<h1 style="font-size:2rem;">🔎 Searching for another grant opportunity…</h1>'
            f'<p>We\'ve marked <strong>{escape(workflow["rejected_title"])}</strong> as <strong>Not pursuing</strong></p>'
            f'<p>Checking the remaining opportunities for <strong>{escape(idea["title"])}</strong>…</p></div>',
            unsafe_allow_html=True,
        )
        if workflow["search_error"]:
            st.info("Your rejection is saved. The alternative search could not finish.")
            if st.button("Retry alternative search", type="primary"):
                workflow["search_error"] = False
                st.rerun()
        else:
            with st.spinner("Checking remaining grant opportunities…"):
                find_opportunity(profile, idea, grants, workflow)
        return
    if state == "no_alternative_found":
        st.title("No other grant opportunities found")
        st.write(f"We've marked **{workflow['rejected_title']}** as **Not pursuing**")
        st.write(f"There aren't any other current opportunities in the prototype that we can surface for **{idea['title']}**")
        with st.container(horizontal=True, gap="small", wrap=True):
            if st.button("Back to research ideas", key="alternative_back", type="primary"):
                workflow.pop("alternative_state", None)
                st.rerun()
            if st.button("View applications", key="alternative_applications"):
                navigate_researcher("Saved Applications / Opportunities")
        return
    if state == "alternative_found":
        show_alternative_carousel(idea, grants, workflow)
        return
    if state == "viewing_alternative" and workflow.get("grant") is not None:
        st.title("Find Funding")
        st.caption(f"Research idea: {idea['title']}")
        show_funding_result(profile, idea, grants, workflow, main_view)


def show_alternative_carousel(idea, grants, workflow):
    candidates = [
        grant for grant in prototype.available_grants(idea, grants)
        if grant["title"] != workflow.get("rejected_title")
        and grant["title"] in workflow["analyses"]
    ]
    candidates.sort(
        key=lambda grant: workflow["analyses"][grant["title"]]["overall_result"]["match_percentage"],
        reverse=True,
    )
    if not candidates:
        workflow["alternative_state"] = "no_alternative_found"
        workflow["grant"] = None
        st.rerun()
    index = min(max(workflow.get("alternative_index", 0), 0), len(candidates) - 1)
    workflow["alternative_index"] = index
    grant = candidates[index]
    analysis = workflow["analyses"][grant["title"]]
    overall = analysis["overall_result"]
    st.html("""<style>
        .st-key-alternative_browsing_header {width:100%;gap:8px;}
        .st-key-alternative_browsing_header h1 {text-align:left;margin:0;padding:0;}
        .st-key-alternative_browsing_header > [data-testid="stElementContainer"]:has(h1) {margin-bottom:8px;}
        .alternative-research-context {background:var(--brand-subtle);border:1px solid var(--border);
            border-radius:12px;padding:8px 12px;text-align:left;color:var(--text);}
        .alternative-research-label {color:var(--brand);font-size:12px;font-weight:600;
            letter-spacing:0.08em;margin-bottom:4px;}
        .alternative-research-title {font-size:16px;font-weight:600;line-height:1.4;overflow-wrap:break-word;}
        .alternative-browsing-instruction {text-align:left;color:var(--secondary);font-size:14px;line-height:1.5;margin:0;}
        .st-key-alternative_navigation {justify-content:center;flex-wrap:wrap;gap:8px;align-items:center;
            width:fit-content;max-width:100%;margin-inline:auto;align-self:center;
            background:var(--elevated);border:1px solid var(--border);border-radius:8px;padding:6px 8px;}
        .st-key-alternative_navigation > [data-testid="stElementContainer"] {flex:0 0 auto;width:auto;}
        .st-key-alternative_navigation button {height:34px;min-height:34px;}
        .st-key-alternative_navigation p {margin:0;}
        .st-key-alternative_navigation > [data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"]) {
            display:flex;align-items:center;align-self:center;margin-block:0;padding-block:0;}
        .st-key-alternative_navigation [data-testid="stMarkdownContainer"] {
            display:flex;align-items:center;margin:0;padding:0;}
        .st-key-alternative_navigation [data-testid="stMarkdownContainer"] p {margin:0;padding:0;}
        .st-key-alternative_navigation > .st-key-alternative_exit {margin-left:0;max-width:100%;}
        .alternative-navigation-divider {height:20px;width:1px;background:var(--border);}
        .stApp .st-key-alternative_exit button[kind="secondary"] {
            color:var(--danger);border:1px solid transparent;background:transparent;
            min-height:34px;padding:4px 8px;}
        .stApp .st-key-alternative_exit button[kind="secondary"] p {color:inherit;font-size:13px;}
        .stApp .st-key-alternative_exit button[kind="secondary"]:hover {
            background:var(--danger-bg);color:var(--danger);}
    </style>""")
    with st.container(key="alternative_browsing_header"):
        st.title("Alternative opportunities")
        st.html('<p class="alternative-browsing-instruction">Browse the remaining matches for your research idea</p>')
        st.html('<div class="alternative-research-context">'
                '<div class="alternative-research-label">RESEARCH IDEA</div>'
                f'<div class="alternative-research-title">{escape(idea["title"])}</div></div>')
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key="alternative_navigation"):
            if st.button("← Previous", disabled=index == 0):
                workflow["alternative_index"] = index - 1
                st.rerun()
            st.write(f"{index + 1} of {len(candidates)}")
            if st.button("Next →", disabled=index == len(candidates) - 1):
                workflow["alternative_index"] = index + 1
                st.rerun()
            st.html('<div class="alternative-navigation-divider" aria-hidden="true"></div>')
            if st.button("Not interested in any of these", key="alternative_exit", type="secondary"):
                # Leaving the carousel changes only browsing state, never saved decisions.
                workflow.pop("alternative_state", None)
                workflow.pop("alternative_index", None)
                workflow.update({"grant": None, "decision": None, "searched": False, "search_error": False})
                st.session_state.alternatives_exit_dialog = True
                st.rerun()
    with st.container(border=True, key="ds_card_alternative"):
        st.html("""<style>
            .st-key-ds_card_alternative {gap:16px;}
            .alternative-card-heading {display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;}
            .alternative-card-heading h2 {flex:1 1 280px;margin:0;padding:0;overflow-wrap:anywhere;}
            .alternative-fit-badge {border:1px solid;border-radius:8px;padding:8px 12px;font-size:15px;font-weight:600;white-space:nowrap;}
            .alternative-about {background:var(--elevated);border:1px solid var(--border);border-radius:8px;padding:16px;color:var(--text);}
            .alternative-about h3 {margin:0 0 8px;padding:0;font-size:15px;}
            .alternative-about p {margin:0;line-height:1.5;font-size:14px;}
            .alternative-recommendation {border-left:3px solid;padding:4px 0 4px 12px;}
            .alternative-recommendation h3 {margin:0 0 8px;padding:0;font-size:16px;}
            .alternative-recommendation p {margin:0;color:var(--text);line-height:1.5;}
            .stApp .st-key-alternative_card_actions button[kind="primary"] {background:#176B52;border-color:#176B52;color:#FFFFFF;}
            .stApp .st-key-alternative_card_actions button[kind="primary"] p {color:inherit;}
            .stApp .st-key-alternative_card_actions button[kind="primary"]:hover {background:#10513E;border-color:#10513E;}
        </style>""")
        _, background, accent = FIT_PRESENTATION.get(overall["overall_assessment"], ("", "var(--elevated)", "var(--secondary)"))
        st.html('<div class="alternative-card-heading">'
                f'<h2>{escape(grant["title"])}</h2>'
                f'<div class="alternative-fit-badge" style="background:{background};border-color:{accent};color:var(--text);">'
                f'{overall["match_percentage"]}% — {escape(overall["overall_assessment"])}</div></div>')
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key="alternative_card_actions"):
            if st.button("View opportunity", type="primary"):
                workflow["grant"] = grant
                workflow["alternative_state"] = "viewing_alternative"
                st.rerun()
            url = grant.get("source_url", "")
            try:
                parsed_url = urlparse(url)
                valid_url = parsed_url.scheme in ("http", "https") and bool(parsed_url.hostname)
            except (ValueError, TypeError):
                valid_url = False
            if valid_url:
                st.link_button("Grant website", url, icon=":material/open_in_new:", type="secondary")
            else:
                st.button("Grant website unavailable", disabled=True, type="secondary")
        context = [grant.get(field, "") for field in ("purpose", "scope", "eligibility")]
        summary = " ".join(re.split(r"(?<=\.)\s+", text, maxsplit=1)[0] for text in context if text)
        if summary:
            st.html('<section class="alternative-about"><h3>About this grant</h3>'
                    f'<p>{escape(summary)}</p></section>')
        recommendation = analysis["suggested_next_action"]
        _, _, accent = RECOMMENDATION_PRESENTATION.get(recommendation, ("", "var(--elevated)", "var(--secondary)"))
        st.html(f'<section class="alternative-recommendation" style="border-color:{accent};">'
                f'<h3>Our recommendation: <span style="color:{accent};">{escape(recommendation)}</span></h3>'
                f'<p>{escape(prototype.concise_explanation(analysis["summary"]))}</p></section>')
        with st.expander("Detailed match analysis", expanded=False):
            show_scored_match_analysis(analysis)
            constraint = analysis["hard_constraint"]
            if constraint["triggered"]:
                st.warning(f"Important: {prototype.concise_explanation(constraint['explanation'])}")
            # Retain the complete cross-criterion summary without duplicating it
            # as an always-visible paragraph alongside the six assessments.
            with st.expander("Overall match context", expanded=False):
                st.write(analysis["summary"])


def reject_current_opportunity(workflow, idea, grant, analysis, main_view):
    """Run before page rendering, so no old decision controls are rebuilt."""
    try:
        opportunity = opportunity_workflow.record_decision(
            idea["title"], grant["title"], "Reject", match_analysis=analysis,
        )
    except (OSError, ValueError, KeyError):
        workflow["decision_error"] = "Unable to save your decision. Please try again."
        return
    workflow.update({
        "decision": "Reject", "opportunity": opportunity,
        "rejected_title": grant["title"], "grant": None,
        "alternative_state": "searching_for_alternative", "search_error": False,
    })
    workflow.pop("decision_error", None)
    # Clear the complete previous page outside its rendering context.
    main_view.empty()


def show_funding_result(profile, idea, grants, workflow, main_view):
    grant = workflow.get("grant")
    if grant is None or workflow.get("alternative_state") in ("searching_for_alternative", "alternative_found", "no_alternative_found"):
        return
    analysis = workflow["analyses"][grant["title"]]
    show_opportunity_header(grant, analysis)
    show_match(analysis)
    if workflow["decision"] is not None:
        if workflow["decision"] in ("Pursue", "Save for review"):
            if st.button("Open application workspace", type="primary"):
                navigate_researcher(
                    "Saved Applications / Opportunities", (idea["title"], grant["title"])
                )
        else:
            show_tracking(workflow, idea, grant)
        return
    st.subheader("Your decision")
    st.caption("The recommendation is advisory; you make the final decision")
    if workflow.get("decision_error"):
        st.error(workflow["decision_error"])
    decision_styles = {
        "Pursue": ("decision_pursue", FIT_PRESENTATION["Strong fit"]),
        "Save for review": ("decision_review", FIT_PRESENTATION["Potential fit"]),
        "Reject": ("decision_reject", FIT_PRESENTATION["Poor fit"]),
    }
    for column, decision in zip(st.columns(3), opportunity_workflow.DECISIONS):
        with column:
            with st.container(key=decision_styles[decision][0]):
                if decision == "Reject":
                    st.button(
                        decision, use_container_width=True,
                        on_click=reject_current_opportunity,
                        args=(workflow, idea, grant, analysis, main_view),
                    )
                    clicked = False
                else:
                    clicked = st.button(decision, use_container_width=True)
        if clicked:
            try:
                opportunity = opportunity_workflow.record_decision(
                    idea["title"], grant["title"], decision, match_analysis=analysis
                )
            except (OSError, ValueError, KeyError):
                st.error("Unable to save your decision. Please try again.")
                return
            workflow["decision"] = decision
            workflow["opportunity"] = opportunity
            if decision in ("Pursue", "Save for review"):
                if decision == "Save for review":
                    st.session_state.review_confirmation_identity = (idea["title"], grant["title"])
                    navigate_researcher("Saved Applications / Opportunities")
                if decision == "Pursue":
                    saved = opportunity_workflow.find_one(opportunity_workflow.load_opportunities(),
                        matched_research_idea=idea["title"], grant_title=grant["title"])
                    if not saved.get("reminder_intro_dismissed"):
                        st.session_state.reminder_dialog_identity = (idea["title"], grant["title"])
                    st.session_state.workspace_scroll_to_top = True
                    st.session_state.setdefault("application_drafts", {}).setdefault(
                        (idea["title"], grant["title"]), {"draft": None})["section"] = "Overview"
                navigate_researcher(
                    "Saved Applications / Opportunities", (idea["title"], grant["title"])
                )
            else:
                st.rerun()



def dismiss_alternatives_exit():
    st.session_state.pop("alternatives_exit_dialog", None)
    st.session_state.researcher_area = "Home / Dashboard"
    st.session_state.application_workspace = None
    st.session_state.alternatives_home_scroll = True


@st.dialog("No worries, let’s keep exploring", width="small", on_dismiss=dismiss_alternatives_exit)
def alternatives_exit_dialog():
    from ui_styles import TOKENS
    variables = ';'.join(f'--{key}:light-dark({light},{dark})' for key, (light, dark) in TOKENS.items())
    st.html('<style>[role="dialog"]:has(.st-key-alternatives_exit_content) {' + variables + ';}</style>')
    st.html("""<style>
        [role="dialog"]:has(.st-key-alternatives_exit_content) h2 {
            position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip-path:inset(50%);white-space:nowrap;}
        [data-testid="stVerticalBlock"].st-key-alternatives_exit_content {
            width:100%;max-width:380px;margin:0 auto;padding:0;gap:12px;text-align:center;box-sizing:border-box;color:var(--text);}
        .alternatives-exit-intro {display:flex;flex-direction:column;align-items:center;gap:10px;text-align:center;}
        .alternatives-exit-icon {width:56px;height:56px;border-radius:50%;background:#176B52;
            display:flex;align-items:center;justify-content:center;color:#FFFFFF;font-size:32px;font-weight:600;line-height:1;}
        .alternatives-exit-heading {font-size:24px;font-weight:700;line-height:1.3;color:var(--text);}
        .alternatives-exit-message {text-align:center;font-size:14px;line-height:1.5;margin:4px 0 0;color:var(--secondary);}
        .alternatives-exit-suggestions {background:var(--brand-subtle);border:1px solid var(--border);
            border-radius:10px;padding:12px 16px;margin-top:16px;color:var(--text);}
        .alternatives-exit-suggestions-title {font-size:16px;font-weight:600;margin-bottom:10px;}
        .alternatives-exit-suggestion {display:flex;align-items:flex-start;gap:8px;text-align:left;
            font-size:14px;line-height:1.5;margin-top:10px;}
        .alternatives-exit-suggestion > span:first-child {color:var(--brand);flex:0 0 16px;text-align:center;}
        .st-key-alternatives_exit_content [data-testid="stCaptionContainer"] p {
            font-size:12px;color:var(--secondary);line-height:1.5;text-align:center;}
        .st-key-alternatives_back_home {margin-top:4px;margin-bottom:4px;}
        .st-key-alternatives_back_home button {min-height:42px;width:100%;justify-content:center;}
        [role="dialog"] .st-key-alternatives_back_home button[kind="primary"] {
            background:#176B52;border-color:#176B52;color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-alternatives_back_home button[kind="primary"] p {color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-alternatives_back_home button[kind="primary"]:is(:hover,:focus-visible) {
            background:#10513E;border-color:#10513E;color:#FFFFFF !important;}
        @media(max-width:480px) {.alternatives-exit-heading {font-size:22px;}}
    </style>""")
    with st.container(key="alternatives_exit_content"):
        st.html('<div class="alternatives-exit-intro"><div class="alternatives-exit-icon" aria-hidden="true">✓</div>'
                '<div class="alternatives-exit-heading" role="heading" aria-level="2">No worries, let’s keep exploring</div>'
                '<p class="alternatives-exit-message">Not every opportunity will be the right fit, and that’s okay</p></div>'
                '<div class="alternatives-exit-suggestions"><div class="alternatives-exit-suggestions-title">What you can do next</div>'
                '<div class="alternatives-exit-suggestion"><span aria-hidden="true">✎</span><span>Add more research notes to develop your idea</span></div>'
                '<div class="alternatives-exit-suggestion"><span aria-hidden="true">↗</span><span>Explore funding for another research idea</span></div>'
                '<div class="alternatives-exit-suggestion"><span aria-hidden="true">↻</span><span>Check back later as new opportunities become available</span></div></div>')
        if st.button("Back to Home", key="alternatives_back_home", type="primary", use_container_width=True):
            dismiss_alternatives_exit()
            st.rerun()
        st.caption("The current dataset is not updated automatically, and no notifications are sent")


def navigate_researcher(area, workspace=None):
    st.session_state.researcher_area = area
    st.session_state.application_workspace = workspace
    st.rerun()


def saved_opportunities():
    opportunities = opportunity_workflow.load_opportunities()
    st.title(f"Saved Applications ({len(opportunities)})")
    if not opportunities:
        st.info("You have no saved opportunities yet")
        return
    now = datetime.now(ZoneInfo("Europe/London"))

    def deadline_order(record):
        _, opportunity = record
        date, moment = application_deadline(opportunity.get("deadline", ""))
        if date is None:
            return (2, "")
        passed = moment.timestamp() <= now.timestamp() if moment else date < now.date()
        return (1 if passed else 0, moment.isoformat() if moment else date.isoformat())

    records = list(enumerate(opportunities))
    # Drafting/submitted records remain in the application group, with their
    # actual status preserved on the card.
    pursuing = [record for record in records if record[1]["current_status"] in ("Pursuing", "Drafting", "Submitted")]
    considering = [record for record in records if record[1]["current_status"] == "Considering"]
    rejected = [record for record in records if record[1]["current_status"] == "Not pursuing"]
    for heading, group in (("🟢 Pursuing", pursuing), ("🟠 Considering", considering)):
        st.subheader(f"{heading} ({len(group)})")
        show_saved_opportunity_cards(sorted(group, key=deadline_order))
    with st.expander(f"🔴 Not pursuing ({len(rejected)})", expanded=False):
        show_saved_opportunity_cards(rejected)
    identity = st.session_state.get("review_confirmation_identity")
    if identity:
        opportunity = opportunity_workflow.find_one(opportunities,
            matched_research_idea=identity[0], grant_title=identity[1])
        review_confirmation_dialog(opportunity)


def dismiss_review_confirmation():
    st.session_state.pop("review_confirmation_identity", None)
    st.session_state.pop("review_confirmation_error", None)


@st.dialog("Saved", width="small", on_dismiss=dismiss_review_confirmation)
def review_confirmation_dialog(opportunity):
    identity = (opportunity["matched_research_idea"], opportunity["grant_title"])
    from ui_styles import TOKENS
    variables = ';'.join(f'--{key}:light-dark({light},{dark})' for key, (light, dark) in TOKENS.items())
    st.html('<style>[role="dialog"]:has(.st-key-review_confirmation_content) {' + variables + ';}</style>')
    st.html("""<style>
        [role="dialog"]:has(.st-key-review_confirmation_content) h2 {
            position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip-path:inset(50%);white-space:nowrap;}
        [data-testid="stVerticalBlock"].st-key-review_confirmation_content {
            width:100%;max-width:380px;margin:0 auto;padding:0;gap:12px;text-align:center;box-sizing:border-box;}
        .st-key-review_confirmation_content [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        .review-success {display:flex;flex-direction:column;align-items:center;gap:10px;text-align:center;}
        .review-success-icon {width:56px;height:56px;border-radius:50%;background:#176B52;
            display:flex;align-items:center;justify-content:center;color:#FFFFFF;}
        .review-success-headline {font-size:24px;font-weight:700;line-height:1.3;color:var(--text);}
        .review-success-copy {font-size:14px;line-height:1.5;color:var(--secondary);}
        .st-key-review_confirmation_saved {background:var(--brand-subtle);border:1px solid var(--border);
            border-radius:10px;padding:12px 16px;gap:4px;text-align:center;}
        .review-saved-label {font-size:11px;font-weight:600;letter-spacing:0.06em;color:var(--secondary);}
        .review-saved-name {font-size:17px;font-weight:600;line-height:1.4;color:var(--text);margin:4px 0;}
        .review-saved-state {font-size:13px;color:var(--secondary);}
        .review-reminder-heading {display:flex;flex-direction:column;align-items:center;gap:4px;
            font-size:16px;font-weight:600;line-height:1.4;color:var(--text);}
        .review-reminder-heading > span {font-size:18px;color:var(--brand);}
        .st-key-review_confirmation_reminder {gap:8px;padding:0;text-align:center;}
        .st-key-review_confirmation_reminder [data-testid="stSelectbox"] {width:100%;margin:0 auto;}
        .st-key-review_confirmation_reminder [data-testid="stCaptionContainer"] p {
            font-size:12px;color:var(--secondary);line-height:1.5;text-align:center;}
        .st-key-review_confirmation_actions {width:100%;gap:8px;margin:0 auto;}
        .st-key-review_confirmation_actions button {min-height:42px;width:100%;justify-content:center;}
        [role="dialog"] .st-key-review_confirmation_actions button[kind="primary"] {
            background:#176B52;border-color:#176B52;color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-review_confirmation_actions button[kind="primary"] p {
            color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-review_confirmation_actions button[kind="primary"]:is(:hover,:focus-visible) {
            background:#10513E;border-color:#10513E;color:#FFFFFF !important;}
        [role="dialog"] .st-key-review_confirmation_actions button[kind="primary"]:disabled {
            background:#496F60;border-color:#496F60;color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-review_confirmation_actions button[kind="secondary"] {
            background:var(--surface);border:1px solid var(--border);color:var(--text);}
        @media(max-width:480px) {.review-success-headline {font-size:22px;}}
    </style>""")
    with st.container(key="review_confirmation_content"):
        st.html("""<div class="review-success">
            <div class="review-success-icon" role="img" aria-label="Success">
            <span aria-hidden="true" style="font-size:32px;font-weight:600;line-height:1;color:#FFFFFF;">✓</span></div>
            <div class="review-success-headline" role="heading" aria-level="2">Opportunity saved!</div>
            <div class="review-success-copy">You can find this opportunity in Saved Applications whenever you're ready</div>
        </div>""")
        with st.container(key="review_confirmation_saved"):
            st.html(f'<div class="review-saved-label">SAVED OPPORTUNITY</div>'
                    f'<div class="review-saved-name">{escape(opportunity["grant_title"])}</div>'
                    '<div class="review-saved-state">Saved for review</div>')
        st.html('<hr style="border:0;border-top:1px solid var(--border);margin:4px 0;">')
        choices = reminder_choices(opportunity.get("deadline", ""), datetime.now(ZoneInfo("Europe/London")))
        preference = opportunity.get("reminder_preference") or {}
        selected = None
        with st.container(key="review_confirmation_reminder"):
            st.html('<div class="review-reminder-heading"><span aria-hidden="true">🔔</span>When would you like to review it?</div>')
            if choices:
                labels = list(choices)
                selected = st.selectbox("Review timing", labels,
                    index=labels.index(preference["timing"]) if preference.get("timing") in labels else None,
                    placeholder="Choose a reminder timing", key=f"review_reminder_{identity!r}", label_visibility="collapsed")
            else:
                st.caption("No future reminder timing choices are available for this deadline")
            st.caption("Reminder preferences are saved, but notifications aren't active yet")
        with st.container(key="review_confirmation_actions"):
            if st.button("Save preference", type="primary", disabled=selected is None, use_container_width=True):
                try:
                    opportunity_workflow.save_reminder_preference(*identity, choices[selected])
                    dismiss_review_confirmation()
                    st.rerun()
                except (OSError, ValueError, KeyError):
                    st.session_state.review_confirmation_error = "Unable to save your reminder preference. Please try again"
            if st.button("Skip for now", use_container_width=True):
                dismiss_review_confirmation()
                st.rerun()
        if st.session_state.get("review_confirmation_error"):
            st.error(st.session_state.review_confirmation_error)


def show_saved_opportunity_cards(records):
    status_styles = {
        "Pursuing": FIT_PRESENTATION["Strong fit"],
        "Considering": FIT_PRESENTATION["Potential fit"],
        "Not pursuing": FIT_PRESENTATION["Poor fit"],
    }
    for number, opportunity in records:
        with st.container(border=True, key=f"ds_card_tracker_{number}"):
            st.subheader(opportunity["grant_title"])
            st.write(f"**Research Idea:** {opportunity['matched_research_idea']}")
            status = opportunity["current_status"]
            _, background, accent = status_styles.get(status, ("", "var(--elevated)", "var(--secondary)"))
            st.markdown(
                f'<span style="display:inline-block;background:{background};color:var(--text);'
                f'border:1px solid {accent};border-radius:999px;padding:3px 9px;'
                f'font-size:12px;font-weight:600;">{escape(status)}</span>',
                unsafe_allow_html=True,
            )
            st.write(f"**Deadline:** {opportunity['deadline']}")
            if st.button("Open application workspace", key=f"open_workspace_{number}"):
                navigate_researcher(
                    "Saved Applications / Opportunities",
                    (opportunity["matched_research_idea"], opportunity["grant_title"]),
                )


def application_completion(opportunity, draft):
    """Four equal, evidenced preparation milestones; not submission readiness."""
    milestones = {
        "Application saved": bool(opportunity.get("grant_title") and opportunity.get("matched_research_idea")),
        "Requirements available": bool(opportunity.get("application_requirements_checklist")),
        "Initial draft generated": draft is not None,
        "No outstanding PI-input items": draft is not None and not draft.missing_information_pi_input_required,
    }
    return round(sum(milestones.values()) / len(milestones) * 100), milestones


def show_workspace_summary(opportunity, next_step):
    deadline = opportunity.get("deadline", "")
    date, moment = application_deadline(deadline)
    if date:
        summary = date.strftime("%d %b %Y") + (moment.strftime(" · %H:%M UK") if moment else "")
    elif "no fixed deadline" in deadline.lower():
        summary = "No fixed deadline"
    else:
        summary = deadline if len(deadline) <= 65 else deadline[:62].rsplit(" ", 1)[0] + "…"
    st.html("""<style>
        .workspace-summary {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:4px 0;}
        .workspace-summary-card {border:1px solid var(--border);border-radius:12px;padding:8px 12px;
            height:86px;display:flex;flex-direction:column;justify-content:center;box-sizing:border-box;text-align:center;background:var(--elevated);}
        .workspace-summary-card:first-child {background:var(--brand-subtle);}
        .workspace-summary-card:nth-child(2) {background:var(--warning-bg);}
        .workspace-summary-label {font-size:12px;font-weight:500;color:var(--secondary);margin-bottom:4px;}
        .workspace-summary-value {font-size:18px;font-weight:600;line-height:1.35;color:var(--text);overflow-wrap:anywhere;}
        .st-key-workspace_actions {padding:0;margin:0;}
        .st-key-workspace_header {gap:4px;}
        .st-key-workspace_header [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        .stApp .st-key-workspace_back button[kind="secondary"] {background:var(--brand-subtle);color:var(--text);border:1px solid var(--sage);border-radius:8px;}
        .stApp .st-key-workspace_back button[kind="secondary"]:hover {background:var(--sage-light);border-color:var(--brand);}
        .st-key-workspace_actions :is(button,a) {min-height:40px;}
        .st-key-workspace_actions a {border:1px solid var(--brand);color:var(--brand);background:transparent;}
        .st-key-workspace_generate button[kind="primary"] {background:var(--brand);border-color:var(--brand);}
        .st-key-workspace_generate button[kind="primary"]:hover {background:var(--brand-hover);border-color:var(--brand-hover);}
        @media(max-width:640px) {.workspace-summary {grid-template-columns:1fr;}}
    </style>""")
    values = (("Status", opportunity["current_status"]), ("Deadline", summary or "Not specified"),
              ("Next step", next_step or "Not set"))
    st.html('<div class="workspace-summary">' + ''.join(
        f'<div class="workspace-summary-card"><div class="workspace-summary-label">{escape(label)}</div>'
        f'<div class="workspace-summary-value">{escape(value)}</div></div>' for label, value in values) + '</div>')


def application_workspace():
    with st.container(key="workspace_back"):
        if st.button("← Back to Saved Applications"):
            navigate_researcher("Saved Applications / Opportunities")
    idea_title, grant_title = st.session_state.application_workspace
    opportunity = opportunity_workflow.find_one(
        opportunity_workflow.load_opportunities(),
        matched_research_idea=idea_title, grant_title=grant_title,
    )
    st.title("Application Workspace")
    # All sections and both entry routes share state for this saved application.
    drafts = st.session_state.setdefault("application_drafts", {})
    previous = st.session_state.researcher_workflows.get(idea_title, {})
    existing_draft = (
        previous.get("draft")
        if (previous.get("grant") or {}).get("title") == grant_title else None
    )
    draft_state = drafts.setdefault((idea_title, grant_title), {"draft": existing_draft})
    saved_draft = opportunity.get("application_draft")
    if saved_draft:
        draft_state["project_title"] = saved_draft.get("project_title")
        draft_state["draft"] = grant_drafter.InitialDraft(
            initial_application_draft=saved_draft["initial_application_draft"],
            missing_information_pi_input_required=saved_draft["missing_information_pi_input_required"])
    if draft_state["draft"] is None and existing_draft is not None:
        draft_state["draft"] = existing_draft
    grant = opportunity_workflow.find_one(matcher.load_json("grants.json"), title=grant_title)
    considering = opportunity["current_status"] == "Considering"
    completion, milestones = application_completion(opportunity, draft_state["draft"])
    with st.container(border=True, key="ds_card_workspace"):
        with st.container(key="workspace_header"):
            show_opportunity_card_heading(
                grant,
                result_card_markup(
                    "Considering" if considering else f"{completion}% — Complete",
                    "Saved for review" if considering else "Application preparation",
                    FIT_PRESENTATION["Potential fit"] if considering else
                    FIT_PRESENTATION["Strong fit"] if completion == 100 else ("", "var(--elevated)", "var(--secondary)"),
                ),
                compact=True,
            )
            with st.container(key="workspace_actions", horizontal=True, gap="small", wrap=True, vertical_alignment="center"):
                if draft_state["draft"] is not None and not considering:
                    if st.button("Resume draft", type="primary"):
                        draft_state["section"] = "Draft"
                        st.rerun()
                elif opportunity["current_status"] == "Pursuing":
                    show_draft_action(draft_state, idea_title, grant_title, label="Generate Draft", workspace=True)
                    if draft_state["draft"] is not None:
                        draft_state["section"] = "Draft"
                        st.rerun()
                st.link_button("Visit Grant Website", opportunity["grant_website_url"], type="secondary")
        st.write(f"**Research Idea:** {opportunity['matched_research_idea']}")
        next_step = opportunity.get("suggested_next_step")
        if opportunity.get("suggested_next_step"):
            next_step = opportunity["suggested_next_step"]
            if draft_state["draft"] is not None and next_step == "Start draft":
                next_step = "Resume draft"
            elif next_step == "Start draft":
                next_step = "Generate draft"
        show_workspace_summary(opportunity, next_step)
        workspace_navigation(opportunity, draft_state, previous, idea_title, grant_title, milestones)

    if st.session_state.pop("workspace_scroll_to_top", False):
        # Streamlit preserves the old page's scroll offset across st.rerun().
        # Scroll the actual main scrolling container once after workspace entry.
        st.html("""<script>
            const main = document.querySelector('[data-testid="stMain"]');
            if (main) main.scrollTo({top:0, behavior:'instant'});
            window.scrollTo({top:0, behavior:'instant'});
        </script>""", unsafe_allow_javascript=True)
    if st.session_state.get("reminder_dialog_identity") == (idea_title, grant_title):
        reminder_dialog(opportunity)


def workspace_navigation(opportunity, draft_state, previous, idea_title, grant_title, milestones):
    sections = ("Overview", "Requirements", "Draft", "Missing Information")
    section = draft_state.get("section", "Overview")
    draft = draft_state["draft"]
    missing_count = (
        len(opportunity.get("key_unresolved_points", []))
        if opportunity["current_status"] == "Considering"
        else len(draft.missing_information_pi_input_required) if draft is not None else 0
    )
    with st.container(key="workspace_navigation", horizontal=True, gap=None, wrap=True):
        for name in sections:
            attention = name == "Missing Information" and missing_count > 0
            label = f"{name} **{missing_count}**" if attention else name
            container_key = "workspace_nav_selected" if name == section else f"workspace_nav_{name.replace(' ', '_')}"
            if attention:
                container_key += "_missing_attention"
            with st.container(key=container_key, width="content"):
                if st.button(label, key=f"workspace_nav_button_{name}", type="secondary"):
                    draft_state["section"] = name
                    st.rerun()
    with st.container(key="workspace_content"):
        workspace_section_content(section, opportunity, draft_state, previous, idea_title, grant_title, milestones)


def handle_workspace_draft_edit(idea_title, grant_title, action, key):
    state = st.session_state.application_drafts[(idea_title, grant_title)]
    if action == "start":
        state["editing"] = True
        title, narrative = workspace_draft_fields(state, idea_title)
        state["edit_text"] = narrative
        state["edit_title"] = title
        st.session_state.pop(key, None)
        st.session_state.pop(key + "_title", None)
    elif action == "change":
        state["edit_text"] = st.session_state[key]
        state["edit_title"] = st.session_state[key + "_title"]
    elif action == "cancel":
        state["editing"] = False
        state.pop("edit_text", None)
        state.pop("edit_title", None)
        state.pop("edit_error", None)
        st.session_state.pop(key, None)
        st.session_state.pop(key + "_title", None)
    elif action == "save":
        try:
            saved = opportunity_workflow.find_one(opportunity_workflow.load_opportunities(),
                matched_research_idea=idea_title, grant_title=grant_title)
            if not saved.get("application_draft"):
                # Preserve a legacy session-only original before storing its edit.
                opportunity_workflow.save_application_draft(idea_title, grant_title,
                    state["draft"].initial_application_draft,
                    state["draft"].missing_information_pi_input_required)
            result = opportunity_workflow.edit_application_draft(idea_title, grant_title, st.session_state[key],
                project_title=st.session_state[key + "_title"])
            state["project_title"] = result["project_title"]
            state["draft"] = grant_drafter.InitialDraft(
                initial_application_draft=result["initial_application_draft"],
                missing_information_pi_input_required=result["missing_information_pi_input_required"])
            state["editing"] = False
            state.pop("edit_text", None)
            state.pop("edit_title", None)
            state.pop("edit_error", None)
            state["edit_saved"] = True
        except (OSError, ValueError, KeyError) as error:
            state["edit_error"] = f"Unable to save draft changes: {error}"


def workspace_draft_fields(state, idea_title):
    """Read an existing leading title locally; preserve the stored AI original."""
    text = state["draft"].initial_application_draft
    if state.get("project_title"):
        return state["project_title"], text
    first, separator, rest = text.partition("\n")
    match = re.fullmatch(r"(?:Project|Research(?: project)?) title(?:\s*\([^)]*\))?\s*:\s*(.+)",
                        first.strip(), re.IGNORECASE)
    if separator and rest.strip() and (match or first.strip() == idea_title):
        return (match[1].strip().strip("*") if match else idea_title), rest.lstrip("\r\n")
    return idea_title, text


def document_draft_markdown(narrative):
    """Display-only emphasis for explicit standalone headings; retain all words."""
    lines = narrative.splitlines(keepends=True)
    heading = re.compile(r"^(?:Summary(?: and alignment)?|Research question(?: and approach)?|"
                         r"Fit to |Novelty and |High-level feasibility|Objectives|Approach|"
                         r"Methodology|Expected outcomes)\b", re.IGNORECASE)
    for index, line in enumerate(lines):
        text = line.strip()
        standalone = index == 0 or not lines[index - 1].strip()
        followed_by_text = index + 1 < len(lines) and lines[index + 1].strip()
        if (standalone and followed_by_text and 0 < len(text) <= 120
                and not text.endswith(('.', '!', '?')) and heading.match(text)):
            # Markdown's explicit headings/lists and unknown text remain untouched.
            newline = line[len(line.rstrip('\r\n')):]
            lines[index] = f"**{text}**{newline or chr(10)}\n"
    return ''.join(lines)


def show_workspace_draft_editor(state, idea_title, grant_title):
    key = f"application_draft_edit_{(idea_title, grant_title)!r}"
    with st.container(horizontal=True, gap="small", wrap=True, vertical_alignment="center"):
        st.subheader("Initial application draft", width="content")
        if not state.get("editing"):
            st.button("Edit draft", on_click=handle_workspace_draft_edit,
                args=(idea_title, grant_title, "start", key))
    with st.container(key="workspace_draft_disclaimer"):
        st.caption("AI-generated starting point — review and adapt to the funder’s application requirements")
    title, narrative = workspace_draft_fields(state, idea_title)
    if state.get("editing"):
        st.session_state.setdefault(key, state.get("edit_text", narrative))
        st.session_state.setdefault(key + "_title", state.get("edit_title", title))
        st.text_input("Project title", key=key + "_title", on_change=handle_workspace_draft_edit,
            args=(idea_title, grant_title, "change", key))
        st.text_area("Initial draft", key=key, height=400,
            on_change=handle_workspace_draft_edit, args=(idea_title, grant_title, "change", key))
        with st.container(horizontal=True, gap="small", wrap=True):
            st.button("Save changes", type="primary", on_click=handle_workspace_draft_edit,
                args=(idea_title, grant_title, "save", key))
            st.button("Cancel editing", on_click=handle_workspace_draft_edit,
                args=(idea_title, grant_title, "cancel", key))
    else:
        st.html("""<style>
            .st-key-workspace_draft_disclaimer [data-testid="stCaptionContainer"] p {font-size:13px;color:var(--secondary);}
            [data-testid="stVerticalBlock"].st-key-workspace_draft_document {
                background:var(--surface);border:1px solid var(--border);border-radius:12px;
                padding:24px 32px;gap:16px;max-width:900px;margin:0 auto;}
            .st-key-workspace_draft_document [data-testid="stMarkdownContainer"] {margin-bottom:0;}
            .st-key-workspace_draft_document p {line-height:1.7;margin:0 0 16px;color:var(--text);}
            .st-key-workspace_draft_document p:last-child {margin-bottom:0;}
            .st-key-workspace_draft_document p:has(> strong:only-child) {margin-top:24px;margin-bottom:12px;line-height:1.5;}
            .st-key-workspace_draft_document p:first-child {margin-top:0;}
            .workspace-document-title {font-size:24px;font-weight:700;line-height:1.35;color:var(--text);margin:0;}
            @media(max-width:640px) {[data-testid="stVerticalBlock"].st-key-workspace_draft_document {padding:20px 16px;}}
        </style>""")
        with st.container(key="workspace_draft_document"):
            st.html(f'<div class="workspace-document-title" role="heading" aria-level="2">{escape(title)}</div>')
            st.write(document_draft_markdown(narrative))
    if state.get("edit_error"):
        st.error(state["edit_error"])
    if state.pop("edit_saved", False):
        st.success("Draft changes saved")


def missing_information_parts(item, number):
    """Split saved wording locally; never infer additional funder requirements."""
    heading, separator, explanation = item.partition(":")
    if not separator or len(heading) > 150:
        heading, explanation = f"PI input required {number}", item
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", explanation.strip())
    actions, problems = [], []
    action_start = re.compile(
        r"^(?:(?:The\s+)?(?:PI|researcher|applicant|you)\s+(?:must|should|needs? to)\s+|Please\s+)?"
        r"(?:provide|confirm|define|specify|clarify|supply|identify|describe|document|verify|check|establish)\b",
        re.IGNORECASE)
    for sentence in sentences:
        (actions if action_start.match(sentence) else problems).append(sentence)
    problem = " ".join(problems) or f"Missing information identified: {heading}"
    action = " ".join(actions) or f"Provide or confirm the missing evidence or information for {heading.lower()}"
    return heading.strip(), problem, action


def workspace_section_content(section, opportunity, draft_state, previous, idea_title, grant_title, milestones):
    draft = draft_state["draft"]

    if section == "Overview":
        if opportunity["current_status"] in ("Considering", "Pursuing", "Drafting"):
            show_reminder_card(opportunity)
        if opportunity["current_status"] == "Considering":
            st.write("Saved for investigation before deciding whether to pursue")
            st.caption("Open Missing Information to review the saved questions and issues to resolve")
            return
        st.caption("Completion counts four equally weighted preparation steps; it does not confirm readiness to submit")
        for label, complete in milestones.items():
            st.write(f"{'✓' if complete else '○'} {label}")
        st.caption("Requirements review and resolution of individual PI-input items are not yet tracked")
    elif section == "Requirements":
        st.markdown("**Deadline**")
        st.write(opportunity.get("deadline") or "Not specified")
        grant = opportunity_workflow.find_one(
            matcher.load_json("grants.json"), title=grant_title,
        )
        for field, label in (
            ("eligibility", "Eligibility"), ("development_stage", "Development stage"),
            ("funding", "Funding"), ("duration", "Duration"),
            ("key_requirements", "Key requirements"), ("exclusions", "Exclusions"),
        ):
            if grant.get(field):
                st.markdown(f"**{label}**")
                st.write(grant[field])
    elif section == "Draft":
        if draft is None:
            st.info("No draft generated yet")
            st.caption("Use Generate Draft in the grant header" if opportunity["current_status"] == "Pursuing"
                       else "Draft generation is available for pursued opportunities")
        if draft_state["draft"] is not None:
            show_workspace_draft_editor(draft_state, idea_title, grant_title)
    elif section == "Missing Information":
        if opportunity["current_status"] == "Considering":
            st.subheader("Questions to resolve before deciding whether to pursue")
            points = opportunity.get("key_unresolved_points", [])
            if points:
                st.caption(f"{len(points)} {'issue' if len(points) == 1 else 'issues'} to investigate. Expand an issue to view the details.")
                with st.container(key="review_issues"):
                    for number, point in enumerate(points, start=1):
                        category = unresolved_issue_category(point, number)
                        with st.expander(f"{category} — Needs confirmation", expanded=False):
                            st.write(prototype.presentation_text(point))
            if not points:
                st.info("No unresolved points were recorded for this opportunity")
        elif draft is None:
            st.info("Generate an initial draft first to identify information that still requires PI input")
        else:
            st.subheader("Missing Information")
            st.html("""<style>
                .workspace-missing-heading {display:flex;align-items:baseline;gap:8px;
                    color:var(--text);font-size:16px;font-weight:600;line-height:1.5;}
                .workspace-missing-heading > span {color:var(--danger);flex-shrink:0;font-size:14px;}
                .st-key-workspace_missing_items {gap:16px;}
                [data-testid="stVerticalBlock"][class*="st-key-missing_item_"] {padding:0;gap:0;overflow:hidden;border-radius:8px;}
                [data-testid="stVerticalBlock"][class*="st-key-missing_problem_"] {background:var(--danger-bg);padding:12px 16px;gap:8px;}
                [data-testid="stVerticalBlock"][class*="st-key-missing_action_"] {padding:0;gap:0;}
                [class*="st-key-missing_action_"] [data-testid="stExpander"] {background:var(--sage-light);border:0;border-radius:0;}
                [class*="st-key-missing_action_"] [data-testid="stExpander"] summary {color:var(--text);padding:10px 16px;}
                .st-key-workspace_missing_items [data-testid="stMarkdownContainer"] {margin-bottom:0;}
            </style>""")
            with st.container(key="workspace_missing_items"):
                for number, item in enumerate(draft.missing_information_pi_input_required, start=1):
                    heading, problem, action = missing_information_parts(item, number)
                    with st.container(border=True, key=f"missing_item_{number}"):
                        with st.container(key=f"missing_problem_{number}"):
                            st.html(f'<div class="workspace-missing-heading" role="heading" aria-level="4">'
                                    f'<span aria-hidden="true">⚠</span>{escape(heading)}</div>')
                            st.write(problem)
                        with st.container(key=f"missing_action_{number}"):
                            with st.expander("What to do", expanded=False):
                                st.write(action)
            if not draft.missing_information_pi_input_required:
                st.write("None identified from the supplied requirements")


def reminder_choices(deadline, now):
    """Compute genuine future return times using the stored UK deadline."""
    date, moment = application_deadline(deadline)
    choices = {}
    if date is not None:
        for days in (30, 14, 7, 3, 1):
            target = (moment or date) - timedelta(days=days)
            future = target.timestamp() > now.timestamp() if moment else target > now.date()
            if future:
                label = f"{days} {'day' if days == 1 else 'days'} before"
                choices[label] = {
                    "timing": label, "basis": "deadline",
                    "return_at" if moment else "return_date": target.isoformat(),
                }
    elif "no fixed deadline" in (deadline or "").lower():
        month = now.month % 12 + 1
        year = now.year + (1 if now.month == 12 else 0)
        next_month = now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))
        for label, target in (
            ("In 1 week", now + timedelta(weeks=1)),
            ("In 2 weeks", now + timedelta(weeks=2)),
            ("In 1 month", next_month),
        ):
            choices[label] = {"timing": label, "basis": "relative", "return_at": target.isoformat()}
    return choices


def dismiss_reminder_dialog():
    identity = st.session_state.get("reminder_dialog_identity")
    if not identity:
        return
    try:
        opportunity_workflow.finish_reminder_intro(*identity)
        st.session_state.pop("reminder_dialog_identity", None)
        st.session_state.pop("reminder_dialog_error", None)
    except (OSError, ValueError, KeyError) as error:
        st.session_state.reminder_dialog_error = f"Unable to save dismissal: {error}"


@st.dialog("Stay on top of this opportunity", width="small", on_dismiss=dismiss_reminder_dialog)
def reminder_dialog(opportunity):
    identity = (opportunity["matched_research_idea"], opportunity["grant_title"])
    st.write(opportunity["grant_title"])
    st.write("Don't lose track of this opportunity while you investigate whether to pursue it"
             if opportunity["current_status"] == "Considering" else
             "Keep this application moving and make sure you don't miss the deadline")
    st.caption("Email and calendar notifications are not active yet — saving a preference does not schedule or send a notification")
    choices = reminder_choices(opportunity.get("deadline", ""), datetime.now(ZoneInfo("Europe/London")))
    preference = opportunity.get("reminder_preference") or {}
    selected = None
    if choices:
        labels = list(choices)
        selected = st.selectbox("When would you like to return?", labels,
            index=labels.index(preference["timing"]) if preference.get("timing") in labels else None,
            placeholder="Choose a reminder timing", key=f"reminder_dialog_timing_{identity!r}")
    else:
        st.caption("No future deadline-relative reminder options are available" if deadline_date(opportunity.get("deadline", "")) else "A deadline is needed to offer reminder timing choices")
    save, later = st.columns(2)
    if save.button("Save preference", type="primary", disabled=selected is None):
        try:
            opportunity_workflow.finish_reminder_intro(*identity, preference=choices[selected])
            st.session_state.pop("reminder_dialog_identity", None)
            st.session_state.pop("reminder_dialog_error", None)
            st.session_state.reminder_saved_identity = identity
            st.rerun()
        except (OSError, ValueError, KeyError) as error:
            st.session_state.reminder_dialog_error = f"Unable to save preference: {error}"
    if later.button("Not now"):
        dismiss_reminder_dialog()
        st.rerun()
    if st.session_state.get("reminder_dialog_error"):
        st.error(st.session_state.reminder_dialog_error)
    with st.container(horizontal=True, gap="small", wrap=True):
        if st.button("✉️ Connect email", type="secondary"):
            st.info("Email integration coming soon")
        if st.button("📅 Connect calendar", type="secondary"):
            st.info("Calendar integration coming soon")


def show_reminder_card(opportunity):
    identity = (opportunity["matched_research_idea"], opportunity["grant_title"])
    with st.container(key="workspace_reminder", gap="small"):
        st.html("""<style>.st-key-workspace_reminder {background:var(--elevated);
            border:1px solid var(--border);padding:0;margin:16px 0;}
            .st-key-workspace_reminder [data-testid="stExpander"] {background:var(--brand-subtle);border-color:var(--sage);}
            .st-key-workspace_reminder [data-testid="stMarkdownContainer"] {margin-bottom:0;}</style>""")
        with st.expander("🔔 Reminder settings", expanded=False):
            st.write(f"**Reminder:** {(opportunity.get('reminder_preference') or {}).get('timing', 'Not set')}")
            if st.button("Manage reminder", key=f"manage_reminder_{identity!r}"):
                st.session_state.reminder_dialog_identity = identity
                st.rerun()
            if st.session_state.pop("reminder_saved_identity", None) == identity:
                st.success("Reminder preference saved")
            st.caption("Email and calendar notifications are not active yet")


def unresolved_issue_category(point, number):
    """Read the saved category prefix; never infer a category from reasoning."""
    categories = {
        "eligibility": "Eligibility",
        "research/activity fit": "Research/activity fit",
        "research activity fit": "Research/activity fit",
        "purpose fit": "Purpose fit",
        "development/readiness fit": "Development/readiness",
        "development readiness fit": "Development/readiness",
        "development/readiness": "Development/readiness",
        "capability fit": "Capability fit",
        "requirements/constraints": "Requirements/constraints",
        "requirements and constraints": "Requirements/constraints",
    }
    prefix, separator, _ = point.partition(":")
    return categories.get(prefix.strip().lower().replace("_", " "), f"Review issue {number}") if separator else f"Review issue {number}"


def deadline_date(text):
    """Read the existing UK deadline format without inventing a date."""
    match = re.match(r"^(\d{1,2} [A-Za-z]+ \d{4})\b", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d %B %Y").date()
    except ValueError:
        return None


def home_dashboard():
    st.title("Home")
    st.write("Your research ideas, funding opportunities and upcoming deadlines")
    ideas = matcher.load_json("funding_needs.json")
    opportunities = opportunity_workflow.load_opportunities()
    idea_count, opportunity_count = st.columns(2)
    for column, count, label, key in (
        (idea_count, len(ideas), "Research ideas", "home_metric_ideas"),
        (opportunity_count, len(opportunities), "Tracked opportunities", "home_metric_opportunities"),
    ):
        with column:
            with st.container(key=key):
                st.markdown(
                    f'<div class="home-metric-number">{count}</div>'
                    f'<div class="home-metric-label">{label}</div>',
                    unsafe_allow_html=True,
                )

    with st.container(key="home_quick_actions"):
        funding, applications, notes = st.columns(3)
        if funding.button("🔍 Find Funding", key="dashboard_find", type="primary", use_container_width=True):
            navigate_researcher("Find Funding")
        if applications.button("📄 View Applications", key="dashboard_applications", use_container_width=True):
            navigate_researcher("Saved Applications / Opportunities")
        if notes.button("📝 My Notes", key="dashboard_notes", use_container_width=True):
            navigate_researcher("My Notes")

    with st.container(key="home_applications"):
        st.subheader("Applications")
        now = datetime.now(ZoneInfo("Europe/London"))
        snapshot = active_application_snapshot(opportunities, now)
        for number, item in enumerate(snapshot):
            pair = (item["matched_research_idea"], item["grant_title"])
            saved_draft = st.session_state.get("application_drafts", {}).get(pair, {}).get("draft")
            workflow = st.session_state.researcher_workflows.get(pair[0], {})
            workflow_draft = (
                workflow.get("draft")
                if (workflow.get("grant") or {}).get("title") == pair[1] else None
            )
            started = item["current_status"] == "Drafting" or saved_draft is not None or workflow_draft is not None
            with st.container(key=f"home_application_{number}"):
                details, action = st.columns([3, 2], vertical_alignment="center")
                with details:
                    _, background, accent = FIT_PRESENTATION.get(
                        "Potential fit" if item["current_status"] == "Considering" else "Strong fit"
                    )
                    countdown = deadline_countdown(item.get("deadline", ""), now)
                    st.markdown(
                        '<div class="home-application-details">'
                        f'<div class="home-application-title">{escape(item["grant_title"])}</div>'
                        f'<div class="home-application-idea">{escape(item["matched_research_idea"])}</div>'
                        '<div class="home-application-metadata">'
                        f'<span class="home-application-status" style="background:{background};border-color:{accent};">'
                        f'{escape(item["current_status"])}</span>'
                        '<span aria-hidden="true">·</span>'
                        f'<span class="home-application-deadline">{escape(countdown)}</span>'
                        '</div></div>',
                        unsafe_allow_html=True,
                    )
                with action:
                    if st.button(
                        "Resume application →" if started else "View application →",
                        key=f"home_open_application_{number}", use_container_width=True,
                    ):
                        navigate_researcher("Saved Applications / Opportunities", pair)
        if not snapshot:
            st.info("No active applications yet. Choose Find Funding to get started.")
        if st.button("View all applications →", key="home_all_applications"):
            navigate_researcher("Saved Applications / Opportunities")

    with st.container(key="home_deadlines"):
        st.subheader("⏰ Upcoming deadlines")
        today = datetime.now(ZoneInfo("Europe/London")).date()
        active = [item for item in opportunities if item["current_status"] in (
            "Considering", "Pursuing", "Drafting",
        )]
        upcoming = []
        undated = []
        for item in active:
            date = deadline_date(item["deadline"])
            if date is None:
                undated.append(item)
            elif date >= today:
                upcoming.append((date, item))
        upcoming.sort(key=lambda entry: entry[0])
        st.caption("For opportunities you are considering, pursuing or drafting")
        if upcoming:
            st.table([
                {
                    "Opportunity": item["grant_title"],
                    "Research Idea": item["matched_research_idea"],
                    "Deadline": item["deadline"],
                }
                for _, item in upcoming[:5]
            ])
            if len(upcoming) > 5:
                st.caption("Showing the next five deadlines. View Applications for all opportunities.")
        else:
            st.info("No upcoming dated deadlines for your active opportunities")
        if undated:
            with st.expander("Other deadline information"):
                for item in undated:
                    st.write(f"**{item['grant_title']}** — {item['matched_research_idea']}")
                    st.write(item["deadline"])

    with st.container(key="home_notes_panel"):
        st.subheader("📝 My Notes")
        st.write("Capture rough thoughts and develop them into research ideas. Type or dictate a thought, then structure and save it in My Notes.")
        if st.button("Time to brainstorm →", key="home_brainstorm"):
            navigate_researcher("My Notes")



IDEA_FIELDS = {
    "title": "Idea title",
    "research_problem": "Research problem/question",
    "proposed_research": "Proposed research",
    "why_it_matters": "Why it matters",
    "existing_evidence": "Existing evidence/starting point",
    "relevant_lab_capabilities": "Relevant lab capabilities",
}


def remember_rough_note():
    st.session_state.notebook["rough_note"] = st.session_state["_rough_note"]


def add_recording_to_note():
    notebook = st.session_state.notebook
    audio = st.session_state.get("_notebook_audio")
    if audio is None:
        return
    notebook["error"] = None
    try:
        with st.spinner("Transcribing your research thought…"):
            transcript = research_notebook.transcribe_recording(audio.getvalue())
        rough = notebook["rough_note"].strip()
        notebook["rough_note"] = f"{rough}\n\n{transcript}" if rough else transcript
        st.session_state["_rough_note"] = notebook["rough_note"]
    except (OSError, ValueError, OpenAIError):
        notebook["error"] = "Unable to transcribe the recording. Please check your connection and API access, then try again."


def structure_notebook_idea():
    notebook = st.session_state.notebook
    notebook["error"] = None
    if not notebook["rough_note"].strip():
        notebook["error"] = "Type or record a rough research thought first"
        return
    try:
        with st.spinner("Structuring your research idea…"):
            result = research_notebook.structure_idea(
                notebook["rough_note"], matcher.load_json("profile.json")
            )
        notebook["draft"] = result.model_dump()
        notebook["saved_title"] = None
        if st.session_state.get("research_notebook_id"):
            notebook_store.update_notebook(st.session_state.research_notebook_id,
                                          {"review_draft": notebook["draft"], "saved_title": None})
        for field, value in notebook["draft"].items():
            st.session_state[f"_idea_edit_{field}"] = value
    except (OSError, ValueError, OpenAIError):
        notebook["error"] = "Unable to structure this idea. Your note has been retained; please try again."


def remember_idea_edit(field):
    notebook = st.session_state.notebook
    notebook["draft"][field] = st.session_state[f"_idea_edit_{field}"]
    notebook["saved_title"] = None
    if st.session_state.get("research_notebook_id"):
        notebook_store.update_notebook(st.session_state.research_notebook_id,
                                      {"review_draft": notebook["draft"], "saved_title": None})


def save_notebook_idea():
    notebook = st.session_state.notebook
    notebook["error"] = None
    try:
        idea = research_notebook.save_research_idea(notebook["draft"])
        notebook["saved_title"] = idea.title
        if st.session_state.get("research_notebook_id"):
            notebook_store.update_notebook(st.session_state.research_notebook_id, {"saved_title": idea.title})
        st.session_state.selected_idea = idea.title
    except ValueError:
        notebook["error"] = "Please complete all six fields and use a title that is not already in your notebook"
    except OSError:
        notebook["error"] = "Unable to save the idea. Your edits have been retained; please try again."


UPLOADS_DIRECTORY = Path(__file__).parent / "uploads"


def uploads_directory():
    return pilot_storage.path("uploads") if pilot_storage.enabled() else UPLOADS_DIRECTORY
UPLOAD_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def load_research_attachments(directory=None):
    directory = directory if directory is not None else uploads_directory()
    manifest = directory / "metadata.json"
    return json.loads(manifest.read_text()) if manifest.exists() else []


@pilot_storage.serialized
def save_research_attachments(files, directory=None):
    """Explicit local saving with content deduplication across reruns/restarts."""
    directory = directory if directory is not None else uploads_directory()
    prepared = []
    for file in files:
        name = Path(file.name.replace("\\", "/")).name
        suffix = Path(name).suffix.lower()
        content = file.getvalue()
        if suffix not in UPLOAD_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {name}")
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"{name}: choose a non-empty file no larger than 10 MB")
        prepared.append((name, suffix, content, hashlib.sha256(content).hexdigest()))
    records = load_research_attachments(directory)
    known = {record["sha256"] for record in records if (directory / record["stored_filename"]).is_file()}
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for name, suffix, content, digest in prepared:
            if digest in known:
                continue
            filename = uuid4().hex + suffix
            path = directory / filename
            path.write_bytes(content)
            written.append(path)
            records.append({"original_filename": name, "stored_filename": filename,
                            "size_bytes": len(content), "sha256": digest,
                            "content_type": mimetypes.guess_type(name)[0],
                            "saved_at": datetime.now(ZoneInfo("UTC")).isoformat()})
            known.add(digest)
        pilot_storage.atomic_json(directory / "metadata.json", records)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return len(written)


def select_research_notebook(notebook_id):
    record = notebook_store.get_notebook(notebook_id)
    st.session_state.research_notebook_id = notebook_id
    st.session_state.notebook = {"rough_note": notebook_store.structuring_source(record),
                                 "draft": record["review_draft"], "saved_title": record["saved_title"], "error": None}
    for field in IDEA_FIELDS:
        st.session_state.pop(f"_idea_edit_{field}", None)


def open_research_notebook(notebook_id):
    select_research_notebook(notebook_id)
    st.rerun()


def close_research_notebook():
    st.session_state.research_notebook_id = None


def remember_notebook_text(notebook_id, key):
    try:
        notebook_store.update_notebook(notebook_id, {"pending_text": st.session_state[key]})
    except OSError:
        st.session_state.notebook["error"] = "Unable to retain this text — keep a copy before leaving"


def save_notebook_text(notebook_id, key):
    try:
        record = notebook_store.get_notebook(notebook_id)
        text = st.session_state.get(key, record["pending_text"])
        if not text.strip():
            raise ValueError("Write a thought before saving")
        token = "text:" + hashlib.sha256((text + record["updated_at"]).encode()).hexdigest()
        notebook_store.add_entry(notebook_id, "text", text, token)
        st.session_state[key] = ""
        st.session_state.notebook["error"] = None
        st.session_state["notebook_feedback"] = "Research thought saved"
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = str(error)


def remember_pending_transcript(notebook_id, key):
    try:
        record = notebook_store.get_notebook(notebook_id)
        if record["pending_transcript"]:
            pending = dict(record["pending_transcript"], text=st.session_state[key])
            notebook_store.update_notebook(notebook_id, {"pending_transcript": pending})
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = f"Unable to retain transcript edits: {error}"


def save_pending_transcript(notebook_id, key):
    """Callbacks execute before widgets render; reset only after durable saving."""
    try:
        pending = notebook_store.get_notebook(notebook_id)["pending_transcript"]
        if pending is None:
            return
        text = st.session_state.get(key, pending["text"])
        notebook_store.add_entry(notebook_id, "transcript", text, "transcript:" + pending["digest"])
        generation_key = f"recording_generation_{notebook_id}"
        st.session_state[generation_key] = st.session_state.get(generation_key, 0) + 1
        st.session_state.notebook["error"] = None
        st.session_state.notebook_feedback = "Transcript added to notebook"
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = f"Unable to save transcript: {error}"


def cancel_pending_transcript(notebook_id):
    try:
        notebook_store.update_notebook(notebook_id, {"pending_transcript": None})
        st.session_state.notebook["error"] = None
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = f"Unable to cancel transcript: {error}"


def handle_entry_edit(notebook_id, entry_id, action, key):
    try:
        if action == "start":
            st.session_state.pop(key, None)
            st.session_state.pop(key + "_title", None)
        if action == "save":
            notebook_store.edit_entry(notebook_id, entry_id, st.session_state.get(key),
                                      title=st.session_state.get(key + "_title"))
            st.session_state.notebook_feedback = "Note updated"
        else:
            notebook_store.set_entry_edit(notebook_id, entry_id,
                                         st.session_state[key] if action == "change" else None,
                                         cancel=action == "cancel",
                                         title=st.session_state.get(key + "_title") if action == "change" else None)
        st.session_state.notebook["error"] = None
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = f"Unable to update note: {error}"


def confirm_entry_delete(notebook_id, entry_id, action):
    if action == "ask":
        st.session_state.entry_delete_confirmation = (notebook_id, entry_id)
        return
    if action == "delete":
        try:
            notebook_store.delete_entry(notebook_id, entry_id, uploads_directory())
            st.session_state.notebook_feedback = "Entry deleted"
            st.session_state.notebook["error"] = None
        except (OSError, ValueError) as error:
            st.session_state.notebook["error"] = f"Unable to delete entry: {error}"
            return
    st.session_state.pop("entry_delete_confirmation", None)


@pilot_storage.serialized
def save_notebook_attachments(notebook_id, uploader_key):
    """Save before rendering, rather than interrupting a half-rendered card."""
    files = st.session_state.get(uploader_key) or []
    if not files:
        return
    try:
        notebook_store.get_notebook(notebook_id)
        directory = uploads_directory() / notebook_id
        save_research_attachments(files, directory)
        selected = {hashlib.sha256(file.getvalue()).hexdigest() for file in files}
        count = 0
        for attachment in load_research_attachments(directory):
            if attachment["sha256"] in selected:
                count += notebook_store.add_entry(notebook_id, "attachment", attachment,
                                                  "attachment:" + attachment["sha256"])
        st.session_state.notebook["error"] = None
        st.session_state.notebook_feedback = f"Saved {count} attachment entries" if count else "These attachments are already saved"
    except (OSError, ValueError) as error:
        st.session_state.notebook["error"] = f"Unable to save attachments: {error}"


def my_notes():
    st.title("My Notes")
    st.html("""<style>
        .st-key-notes_introduction {margin-bottom:16px;gap:16px;background:var(--brand-subtle);
            border:1px solid var(--sage);border-radius:12px;padding:24px;}
        .st-key-notes_introduction [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        .st-key-notes_introduction p {line-height:1.7;}
        .st-key-notes_introduction h2 {padding:0;}
        .st-key-notebook_instructions {background:var(--brand-subtle);color:var(--text);
            border:1px solid var(--sage);border-left:4px solid var(--brand);
            border-radius:12px;padding:16px;gap:8px;}
        .st-key-notebook_instructions h3 {padding:0;margin:0;color:var(--brand);}
        .st-key-notebook_instructions [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        [data-testid="stVerticalBlock"].st-key-notes_record_card,
        [data-testid="stVerticalBlock"].st-key-notes_write_card,
        [data-testid="stVerticalBlock"].st-key-notes_upload_card {
            border:1px solid var(--border);border-radius:12px;padding:24px;color:var(--text);gap:16px;}
        .st-key-notes_record_card {background:var(--brand-subtle);}
        .st-key-notes_write_card {background:var(--sage-light);}
        .st-key-notes_upload_card {background:var(--elevated);}
        .st-key-notes_record_card h3,.st-key-notes_write_card h3,
        .st-key-notes_upload_card h3 {padding:0;margin:0;}
        .st-key-notes_record_card [data-testid="stMarkdownContainer"],
        .st-key-notes_write_card [data-testid="stMarkdownContainer"],
        .st-key-notes_upload_card [data-testid="stMarkdownContainer"] {margin-bottom:0;}
    </style>""")
    notebook_id = st.session_state.get("research_notebook_id")
    if notebook_id:
        with st.container(key=f"notes_notebook_view_{notebook_id}"):
            show_research_notebook(notebook_id)
    else:
        with st.container(key="notes_home_view"):
            show_notebook_home()


def show_notebook_home():
    with st.container(key="notes_introduction"):
        st.header("Capture your research ideas")
        st.write("Record or type your thoughts as they come, or upload research material. Save them over time, then use AI to structure them into a research idea when you’re ready.")
    if st.button("+ New notebook", type="primary"):
        st.session_state["creating_notebook"] = True
    if st.session_state.get("creating_notebook"):
        with st.form("new_notebook"):
            st.subheader("Name your research notebook")
            st.write("Give your notebook a name based on the research idea you’re exploring. You can always change it later.")
            title = st.text_input("Notebook name", placeholder="e.g. Improving Lipid Nanoparticles for mRNA Delivery")
            if st.form_submit_button("Create notebook"):
                try:
                    record = notebook_store.create_notebook(title)
                    st.session_state["creating_notebook"] = False
                    open_research_notebook(record["id"])
                except (OSError, ValueError) as error:
                    st.error(str(error))
    records = sorted(notebook_store.load_notebooks(), key=lambda item: item["updated_at"], reverse=True)
    for record in records:
        with st.container(border=True, key=f"ds_card_notebook_{record['id']}"):
            st.subheader(record["title"])
            date = datetime.fromisoformat(record["updated_at"]).astimezone(ZoneInfo("Europe/London"))
            st.caption(f"Updated {date:%d %b %Y, %H:%M} · {len(record['entries'])} entries")
            if record["pending_text"] or record["pending_transcript"]:
                st.caption("Unfinished thought retained")
            st.button("Open notebook →", key=f"open_notebook_{record['id']}",
                      on_click=select_research_notebook, args=(record["id"],))


def show_research_notebook(notebook_id):
    record = notebook_store.get_notebook(notebook_id)
    notebook = st.session_state.notebook
    notebook.update({"rough_note": notebook_store.structuring_source(record),
                     "draft": record["review_draft"], "saved_title": record["saved_title"]})
    st.button("← All notebooks", on_click=close_research_notebook)
    st.header(record["title"])
    with st.container(key="notebook_instructions"):
        st.subheader("Build your research idea over time")
        st.write("Add notes, recordings and supporting files whenever inspiration strikes. Everything is saved in this notebook. When you’re ready, scroll to the bottom and click Structure research idea to turn your notes into an organised research proposal outline.")
    with st.expander("Rename notebook"):
        with st.form(f"rename_notebook_{notebook_id}"):
            name = st.text_input("Notebook name", value=record["title"])
            if st.form_submit_button("Save name"):
                try:
                    notebook_store.update_notebook(notebook_id, {"title": name})
                    st.session_state.notebook_feedback = "Notebook renamed"
                    st.rerun()
                except (OSError, ValueError) as error:
                    st.error(str(error))
    feedback = st.empty()
    if st.session_state.get("notebook_feedback"):
        feedback.success(st.session_state.pop("notebook_feedback"))
    st.subheader("Research entries")
    # One stable block keeps growing entry history from shifting the input cards.
    with st.container(key="notes_entry_collection"):
        if not record["entries"]:
            st.caption("Add your first thought or attachment below")
        for entry in record["entries"]:
            date = datetime.fromisoformat(entry["created_at"]).astimezone(ZoneInfo("Europe/London"))
            with st.container(border=True):
                st.subheader(entry["content"]["original_filename"] if entry["kind"] == "attachment"
                             else notebook_store.entry_title(entry))
                st.caption(f"{date:%d %b %Y, %H:%M} · {entry['kind'].title()}")
                edit_key = f"entry_edit_{notebook_id}_{entry['id']}"
                if entry["kind"] != "attachment" and entry["id"] in record.get("entry_edits", {}):
                    st.session_state.setdefault(edit_key + "_title", record.get("entry_title_edits", {}).get(entry["id"], notebook_store.entry_title(entry)))
                    st.text_input("Entry title", key=edit_key + "_title", on_change=handle_entry_edit,
                                  args=(notebook_id, entry["id"], "change", edit_key))
                    st.session_state.setdefault(edit_key, record["entry_edits"][entry["id"]])
                    if entry["kind"] != "attachment":
                        st.text_area("Edit note", key=edit_key, height=160,
                                     on_change=handle_entry_edit, args=(notebook_id, entry["id"], "change", edit_key))
                    save, cancel = st.columns(2)
                    save.button("Save changes", key=f"save_entry_{entry['id']}", on_click=handle_entry_edit,
                                args=(notebook_id, entry["id"], "save", edit_key))
                    cancel.button("Cancel", key=f"cancel_entry_{entry['id']}", on_click=handle_entry_edit,
                                  args=(notebook_id, entry["id"], "cancel", edit_key))
                else:
                    if entry["kind"] == "attachment":
                        attachment = entry["content"]
                        path = uploads_directory() / notebook_id / attachment["stored_filename"]
                        if path.is_file():
                            st.download_button("Download attachment", path.read_bytes(), file_name=attachment["original_filename"],
                                               mime=attachment["content_type"], key=f"attachment_{entry['id']}")
                        else:
                            st.warning("This saved attachment is unavailable on disk")
                    else:
                        st.write(entry["content"])
                    if entry["kind"] != "attachment" and entry.get("modified_at"):
                        modified = datetime.fromisoformat(entry["modified_at"]).astimezone(ZoneInfo("Europe/London"))
                        st.caption(f"Edited {modified:%d %b %Y, %H:%M}")
                    if entry["kind"] == "attachment":
                        delete, _ = st.columns([1, 4])
                    else:
                        edit, delete, _ = st.columns([1, 1, 3])
                        edit.button("Edit note", key=f"edit_entry_{entry['id']}", on_click=handle_entry_edit,
                                    args=(notebook_id, entry["id"], "start", edit_key))
                    delete.button("Delete", key=f"delete_entry_{entry['id']}",
                                  on_click=confirm_entry_delete, args=(notebook_id, entry["id"], "ask"))
                if st.session_state.get("entry_delete_confirmation") == (notebook_id, entry["id"]):
                    st.warning("Delete this entry? This cannot be undone")
                    confirm, cancel, _ = st.columns([1, 1, 3])
                    confirm.button("Delete entry", key=f"confirm_delete_{entry['id']}",
                                   on_click=confirm_entry_delete, args=(notebook_id, entry["id"], "delete"))
                    cancel.button("Cancel", key=f"cancel_delete_{entry['id']}",
                                  on_click=confirm_entry_delete, args=(notebook_id, entry["id"], "cancel"))
    # A normal keyed container has one lifecycle within this notebook view.
    # Do not clear/recreate a placeholder or render inputs from the homepage.
    with st.container(key="notes_input_surface"):
        with st.container(key="notes_record_card"):
            st.subheader("🎙️ Record a research thought")
            generation = st.session_state.get(f"recording_generation_{notebook_id}", 0)
            audio = st.audio_input("Record a research thought", key=f"audio_{notebook_id}_{generation}", sample_rate=16000, label_visibility="collapsed")
            st.caption("Transcribe your recording, then save the transcript as an entry")
            if st.button("Transcribe recording", disabled=audio is None):
                digest = hashlib.sha256(audio.getvalue()).hexdigest()
                if any(entry["save_token"] == "transcript:" + digest for entry in record["entries"]):
                    st.info("This recording has already been saved")
                elif record["pending_transcript"] and record["pending_transcript"]["digest"] != digest:
                    st.warning("Save the current transcript before transcribing another recording")
                elif not record["pending_transcript"]:
                    try:
                        with st.spinner("Transcribing your research thought…"):
                            text = research_notebook.transcribe_recording(audio.getvalue())
                        notebook_store.update_notebook(notebook_id, {"pending_transcript": {"text": text, "digest": digest}})
                        st.rerun()
                    except (OSError, ValueError, OpenAIError):
                        st.error("Unable to transcribe the recording — please try again")
            with st.container(key="notes_transcript_editor"):
                if record["pending_transcript"]:
                    pending = record["pending_transcript"]
                    transcript_key = f"pending_transcript_{notebook_id}_{pending['digest']}"
                    st.session_state.setdefault(transcript_key, pending["text"])
                    st.text_area("Review and correct transcript", key=transcript_key, height=180,
                                 on_change=remember_pending_transcript, args=(notebook_id, transcript_key))
                    st.caption("Correct technical terms before adding this transcript — edits are retained")
                    st.button("Save transcript", on_click=save_pending_transcript, args=(notebook_id, transcript_key))
                    st.button("Cancel transcript", on_click=cancel_pending_transcript, args=(notebook_id,))
        with st.container(key="notes_write_card"):
            st.subheader("✏️ Write a research thought")
            key = f"notebook_text_{notebook_id}"
            st.session_state.setdefault(key, record["pending_text"])
            st.text_area("Rough research thought", height=180, key=key, label_visibility="collapsed",
                         on_change=remember_notebook_text, args=(notebook_id, key))
            st.caption("Unfinished text is retained when you leave this field — save it to add a dated entry")
            st.button("Save thought", on_click=save_notebook_text, args=(notebook_id, key))
        with st.container(key="notes_upload_card"):
            st.subheader("📎 Upload research material")
            st.caption("Saved attachments only — AI structuring cannot read these files")
            files = st.file_uploader("Research material", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"],
                                     accept_multiple_files=True, key=f"files_{notebook_id}", label_visibility="collapsed")
            st.caption("Maximum 10 MB per file · Selecting files does not save them")
            st.button("Save attachments", key=f"save_attachments_{notebook_id}", disabled=not files,
                      on_click=save_notebook_attachments, args=(notebook_id, f"files_{notebook_id}"))
    st.subheader("Structure research idea")
    st.caption("Uses this notebook’s saved text and transcripts only — unfinished thoughts and attachments are excluded")
    st.button("Structure research idea", type="primary", on_click=structure_notebook_idea,
              disabled=not notebook_store.structuring_source(record).strip())
    if notebook["error"]:
        st.error(notebook["error"])
    if notebook["draft"] is not None:
        st.subheader("Review and edit")
        st.caption("Confirm the content, including anything marked Not yet provided, before saving")
        for field, label in IDEA_FIELDS.items():
            key = f"_idea_edit_{field}"
            st.session_state.setdefault(key, notebook["draft"][field])
            if field == "title":
                st.text_input(label, key=key, on_change=remember_idea_edit, args=(field,))
            else:
                st.text_area(label, key=key, height=100, on_change=remember_idea_edit, args=(field,))
        st.button("Save as research idea", on_click=save_notebook_idea, disabled=notebook["saved_title"] is not None)
        if notebook["saved_title"]:
            st.success("Research idea saved. It is now available in Find Funding.")
            if st.button("Find Funding for this idea"):
                navigate_researcher("Find Funding")


def application_deadline(text):
    """Read stored UK dates/times without supplying an unstated deadline time."""
    date = deadline_date(text or "")
    if date is None:
        return None, None
    clock = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", text, re.IGNORECASE)
    if clock is None:
        return date, None
    hour, minute, period = clock.groups()
    if not 1 <= int(hour) <= 12 or not 0 <= int(minute) <= 59:
        return date, None
    hour = int(hour) % 12 + (12 if period.lower() == "pm" else 0)
    return date, datetime(
        date.year, date.month, date.day, hour, int(minute),
        tzinfo=ZoneInfo("Europe/London"),
    )


def deadline_countdown(text, now):
    date, moment = application_deadline(text)
    if date is None:
        return "No fixed deadline" if "no fixed deadline" in (text or "").lower() else "Deadline not specified"
    if moment is None:
        days = (date - now.date()).days
        if days < 0:
            return "Deadline passed"
        if days == 0:
            return "Deadline today (time not specified)"
        return f"{days} {'day' if days == 1 else 'days'} left"
    seconds = moment.timestamp() - now.timestamp()
    if seconds <= 0:
        return "Deadline passed"
    if seconds <= 3600:
        return "Less than 1 hour left"
    if seconds <= 48 * 3600:
        return f"{ceil(seconds / 3600)} hours left"
    return f"{ceil(seconds / 86400)} days left"


def active_application_snapshot(opportunities, now):
    """Shared active/deadline ordering used by Home and sidebar quick access."""
    active = [
        item for item in opportunities
        if item["current_status"] in ("Considering", "Pursuing", "Drafting")
    ]

    def deadline_order(item):
        date, moment = application_deadline(item.get("deadline", ""))
        if date is None:
            return (1, "")
        passed = (moment.timestamp() <= now.timestamp()) if moment else date < now.date()
        return (2 if passed else 0, moment.isoformat() if moment else date.isoformat())

    return sorted(active, key=deadline_order)[:3]


def sidebar_applications():
    now = datetime.now(ZoneInfo("Europe/London"))
    active = active_application_snapshot(opportunity_workflow.load_opportunities(), now)

    with st.container(key="sidebar_applications_card", gap="small"):
        st.markdown("**Applications**")
        for number, item in enumerate(active):
            countdown = deadline_countdown(item.get("deadline", ""), now)
            date, moment = application_deadline(item.get("deadline", ""))
            urgency = "neutral"
            if date is not None:
                remaining_days = (
                    (moment.timestamp() - now.timestamp()) / 86400
                    if moment else (date - now.date()).days
                )
                if countdown == "Deadline passed":
                    urgency = "red"
                elif moment is not None and remaining_days < 2:
                    urgency = "urgent"
                elif remaining_days < 7:
                    urgency = "red"
                elif remaining_days <= 14:
                    urgency = "amber"
            with st.container(key=f"sidebar_deadline_{urgency}_{number}"):
                clicked = st.button(
                    f"**{item['grant_title']}**  \n{item['current_status']} *{countdown}*",
                    key=f"sidebar_application_{number}", type="tertiary",
                    use_container_width=True,
                )
            if clicked:
                navigate_researcher(
                    "Saved Applications / Opportunities",
                    (item["matched_research_idea"], item["grant_title"]),
                )
        if st.button("View all applications →", key="sidebar_view_applications", type="tertiary"):
            navigate_researcher("Saved Applications / Opportunities")


def sidebar_connect_tools():
    with st.container(key="sidebar_connect_tools", gap="small"):
        st.markdown("**Connect your tools**")
        st.caption("Get application reminders and updates where you already work")
        if st.button("Connect email & calendar", icon=":material/calendar_month:", type="secondary", use_container_width=True):
            st.info("Email and calendar integrations are coming soon")


@st.dialog("Reset prototype test data?")
def confirm_test_reset():
    from ui_styles import TOKENS
    variables = ';'.join(f'--{key}:light-dark({light},{dark})' for key, (light, dark) in TOKENS.items())
    st.html('<style>[role="dialog"]:has(.st-key-reset_confirmation_content) {' + variables + ';}</style>')
    st.html("""<style>
        [role="dialog"]:has(.st-key-reset_confirmation_content) h2::before {
            content:"⚠";color:#C44747;font-size:18px;margin-right:8px;vertical-align:middle;}
        .st-key-reset_confirmation_content {gap:16px;}
        .st-key-reset_confirmation_content [data-testid="stMarkdownContainer"] {margin-bottom:0;}
        .st-key-reset_confirmation_content [data-testid="stHorizontalBlock"] {gap:12px;}
        .st-key-reset_confirmation_content button {width:100%;min-height:42px;border-radius:8px;font-weight:600;}
        [role="dialog"] .st-key-confirm_test_reset button[kind="primary"] {
            background:#C44747;border-color:#C44747;color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-confirm_test_reset button[kind="primary"] p {color:#FFFFFF !important;font-weight:700;opacity:1;}
        [role="dialog"] .st-key-confirm_test_reset button[kind="primary"]:is(:hover,:focus-visible) {
            background:#A83B3B;border-color:#A83B3B;color:#FFFFFF !important;}
        [role="dialog"] .st-key-confirm_test_reset button[kind="primary"]:disabled {
            background:#915353;border-color:#915353;color:#FFFFFF !important;opacity:1;}
        [role="dialog"] .st-key-reset_confirmation_content button[kind="secondary"] {
            background:var(--surface);color:var(--text);border:1px solid var(--border);}
        [role="dialog"] .st-key-reset_confirmation_content button[kind="secondary"] p {color:inherit;}
        [role="dialog"] .st-key-reset_confirmation_content button[kind="secondary"]:hover {
            background:var(--elevated);border-color:var(--secondary);}
    </style>""")
    with st.container(key="reset_confirmation_content"):
        st.write("This clears your test applications, decisions and generated drafts, but keeps the fictional lab, research ideas and grant dataset.")
        cancel, reset = st.columns(2)
        if cancel.button("Cancel", use_container_width=True):
            st.rerun()
        if reset.button("Reset test data", key="confirm_test_reset", type="primary", use_container_width=True):
            try:
                opportunity_workflow.reset_test_opportunities()
            except (OSError, ValueError, KeyError, TypeError):
                st.error("Unable to reset test data. Please try again.")
                return
            # Drafts, reminder widgets, match caches and navigation are session-only.
            identity = st.session_state.get("_pilot_identity")
            st.session_state.clear()
            if identity:
                st.session_state["_pilot_identity"] = identity
            st.rerun()


def researcher():
    st.html("""
        <style>
        :is(.st-key-home_metric_ideas,.st-key-home_metric_opportunities) {
            background:var(--surface);border:1px solid var(--border);border-radius:12px;
            padding:24px;min-height:136px;box-sizing:border-box;
            border-top:3px solid var(--sage);
        }
        .home-metric-number {font-size:40px;line-height:1.2;font-weight:700;color:var(--text);}
        .home-metric-label {font-size:14px;color:var(--secondary);margin-top:8px;}
        .st-key-home_quick_actions {margin-top:8px;margin-bottom:16px;}
        .st-key-home_quick_actions [data-testid="stHorizontalBlock"] {gap:16px;}
        .st-key-home_quick_actions button {height:44px;}
        .st-key-home_quick_actions button[kind="primary"] {background:#176B52;color:#FFFFFF;border-color:#176B52;}
        .st-key-home_quick_actions button[kind="primary"]:hover {background:#10513E;color:#FFFFFF;border-color:#10513E;}
        .st-key-home_applications {margin-top:16px;}
        [class*="st-key-home_application_"] {background:var(--surface);border:1px solid var(--border);
            border-radius:12px;padding:16px;gap:8px;}
        [class*="st-key-home_application_"] [data-testid="stHorizontalBlock"] {
            display:grid;grid-template-columns:minmax(0,1fr) minmax(0,200px);
            gap:24px;align-items:center;
        }
        [class*="st-key-home_application_"] [data-testid="stColumn"] {width:100%;min-width:0;}
        [class*="st-key-home_application_"] [data-testid="stMarkdownContainer"]:has(.home-application-details) {
            margin-bottom:0;
        }
        .home-application-details {display:flex;flex-direction:column;gap:8px;overflow-wrap:anywhere;}
        .home-application-title {font-size:16px;font-weight:600;line-height:1.5;color:var(--text);}
        .home-application-idea {font-size:14px;line-height:1.5;color:var(--secondary);}
        .home-application-metadata {display:flex;flex-wrap:wrap;align-items:center;gap:8px;line-height:1.5;color:var(--secondary);}
        @media(max-width:700px) {
            [class*="st-key-home_application_"] [data-testid="stHorizontalBlock"] {
                grid-template-columns:minmax(0,1fr);gap:16px;
            }
        }
        .home-application-status {display:inline-block;border:1px solid;border-radius:999px;
            padding:2px 8px;color:var(--text);font-size:12px;font-weight:600;}
        .home-application-deadline {font-size:13px;color:var(--secondary);}
        .st-key-home_notes_panel {background:var(--sage-light);border:1px solid var(--border);
            border-radius:12px;padding:24px;margin:8px 0 24px;}
        .st-key-home_notes_panel h3 {font-size:24px;font-weight:650;}
        .st-key-home_applications h3, .st-key-home_deadlines h3 {font-size:24px;font-weight:650;}
        .st-key-home_deadlines {margin:32px 0 24px;padding:16px 24px;
            border-left:3px solid var(--warning);border-radius:8px;background:var(--surface);}
        .stApp .st-key-home_brainstorm button[kind="secondary"] {
            background:#10513E;color:#FFFFFF;border:none;border-radius:8px;
        }
        .stApp .st-key-home_brainstorm button[kind="secondary"]:hover {background:#176B52;color:#FFFFFF;}
        .stApp .st-key-home_brainstorm button[kind="secondary"] p {color:inherit;}
        .stApp [data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button {
            width:100%;min-height:44px;padding:8px 16px;background:var(--surface);
            border:1px solid var(--border);color:var(--secondary);border-radius:8px;
        }
        .stApp [data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button:hover {
            background:var(--sage-light);color:var(--text);
        }
        .stApp [data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button[kind="primary"] {
            background:var(--brand-subtle);border:1px solid var(--sage);
            color:light-dark(#176B52,#68BE9D);font-weight:600;
        }
        .stApp [data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button :is(p,span) {color:inherit;}
        .stApp :is(.st-key-researcher_back,.st-key-sidebar_test_reset,.st-key-sidebar_view_applications) {
            align-self:center;margin-inline:auto;
        }
        .stApp .st-key-sidebar_view_applications [data-testid="stButton"] {display:flex;justify-content:center;}
        .stApp .st-key-researcher_back [data-testid="stButton"],
        .stApp .st-key-sidebar_test_reset [data-testid="stButton"] {display:flex;justify-content:center;}
        .stApp :is(.st-key-researcher_back,.st-key-sidebar_test_reset) button {
            background:var(--surface);border:1px solid var(--border);border-radius:8px;
            color:var(--secondary);min-height:40px;padding:8px 16px;justify-content:center;
        }
        .stApp .st-key-researcher_back button:hover {background:var(--brand-subtle);color:var(--text);}
        .stApp .st-key-sidebar_test_reset button {
            background:var(--surface);border:1px solid var(--danger);color:var(--danger);
        }
        .stApp .st-key-sidebar_test_reset button:is(:hover,:focus-visible) {
            background:var(--danger-bg);border-color:var(--danger);color:var(--danger);outline-color:var(--danger);
        }
        .stApp :is(.st-key-researcher_back,.st-key-sidebar_test_reset) button p {text-align:center;color:inherit;}
        .stApp .st-key-sidebar_applications_card {gap:8px;}
        .stApp .st-key-sidebar_applications_card [data-testid="stMarkdownContainer"] {text-align:center;}
        .stApp .st-key-sidebar_applications_card button {justify-content:center;text-align:center;padding:8px 4px;}
        .stApp .st-key-sidebar_applications_card button p {text-align:center;}
        .stApp .st-key-sidebar_applications_card button strong {text-align:center;}
        .st-key-sidebar_connect_tools {background:var(--brand-subtle);border-color:var(--sage);gap:8px;}
        .st-key-sidebar_connect_tools strong {font-size:16px;font-weight:600;}
        .stApp .st-key-sidebar_connect_tools [data-testid="stMarkdownContainer"] {text-align:center;}
        .stApp .st-key-sidebar_connect_tools [data-testid="stButton"] {display:flex;justify-content:center;}
        .stApp .st-key-sidebar_connect_tools button[kind="secondary"] {
            background:#176B52;color:#FFFFFF;border:none;border-radius:8px;
            font-weight:600;justify-content:center;align-items:center;gap:0;
            height:53px;min-height:53px;padding:4px 8px;
        }
        .stApp .st-key-sidebar_connect_tools button[kind="secondary"] > div {width:100%;justify-content:center;}
        .stApp .st-key-sidebar_connect_tools button [data-has-shortcut] {
            display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;
        }
        .stApp .st-key-sidebar_connect_tools button span:has(> [data-testid="stIconMaterial"]) {
            display:flex;align-items:center;justify-content:center;gap:8px;height:16px;
        }
        .stApp .st-key-sidebar_connect_tools button [data-testid="stIconMaterial"] {
            font-size:16px;line-height:16px;width:16px;height:16px;
        }
        .stApp .st-key-sidebar_connect_tools button[kind="secondary"]:hover {background:#10513E;color:#FFFFFF;}
        .stApp .st-key-sidebar_connect_tools button[kind="secondary"] :is(p,span) {color:inherit;text-align:center;}
        /* Local mail outline pairs with the existing native calendar icon. */
        .stApp .st-key-sidebar_connect_tools button span:has(> [data-testid="stIconMaterial"])::before {
            content:"";display:block;width:16px;height:16px;flex-shrink:0;background:currentColor;
            mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cg fill='none' stroke='black' stroke-width='2'%3E%3Crect x='3' y='5' width='18' height='14' rx='2'/%3E%3Cpath d='m3 6 9 7 9-7'/%3E%3C/g%3E%3C/svg%3E") center/contain no-repeat;
        }
        .stApp .st-key-development_test_reset [data-testid="stCaptionContainer"] {text-align:center;color:var(--muted);}
        .stApp .st-key-development_test_reset [data-testid="stCaptionContainer"] p {color:inherit;}
        </style>
    """)
    areas = (
        "Home / Dashboard", "Find Funding", "My Notes",
    )
    with st.sidebar:
        for area in areas:
            if st.button(
                "Home" if area == "Home / Dashboard" else area, key=f"researcher_nav_{area}", use_container_width=True,
                icon={"Home / Dashboard": ":material/home:", "Find Funding": ":material/search:", "My Notes": ":material/edit_note:"}[area],
                type="primary" if st.session_state.researcher_area == area else "secondary",
            ):
                navigate_researcher(area)
        sidebar_applications()
        sidebar_connect_tools()
        if st.button("Change user type", key="researcher_back"):
            change_route(None)
        with st.container(key="development_test_reset"):
            st.caption("Prototype version — for demonstration and testing only. Some features are not yet fully functional.")
            if st.button("↻ Reset test data", key="sidebar_test_reset", type="secondary"):
                confirm_test_reset()
        if pilot_storage.enabled() and st.button("Sign out", key="pilot_sign_out"):
            st.session_state.clear()
            st.rerun()
    if st.session_state.application_workspace is not None:
        application_workspace()
    elif st.session_state.researcher_area == "Home / Dashboard":
        home_dashboard()
        if st.session_state.pop("alternatives_home_scroll", False):
            st.html("""<script>
                const main = document.querySelector('[data-testid="stMain"]');
                if (main) main.scrollTo({top:0, behavior:'instant'});
                window.scrollTo({top:0, behavior:'instant'});
            </script>""", unsafe_allow_javascript=True)
    elif st.session_state.researcher_area == "Find Funding":
        main_view = st.empty()
        with main_view.container():
            find_funding(main_view)
    elif st.session_state.researcher_area == "Saved Applications / Opportunities":
        saved_opportunities()
    elif st.session_state.researcher_area == "My Notes":
        my_notes()


def main():
    st.set_page_config(page_title="Funding opportunities", layout="centered")
    apply_design_system()
    pilot_access.require_access()
    st.session_state.setdefault("route", None)
    st.session_state.setdefault("researcher_workflows", {})
    st.session_state.setdefault("researcher_area", "Home / Dashboard")
    st.session_state.setdefault("application_workspace", None)
    st.session_state.setdefault("notebook", {
        "rough_note": "", "draft": None, "saved_title": None, "error": None,
    })
    if st.session_state.route is None:
        landing()
        return
    if st.session_state.route == "charity":
        if st.button("Back to Who are you?"):
            change_route(None)
        st.title("Charity / NGO workflow coming soon")
    elif st.session_state.route == "researcher":
        try:
            researcher()
        except (OSError, ValueError, KeyError):
            st.error("Unable to load this opportunity. Please try again.")


if __name__ == "__main__":
    main()
