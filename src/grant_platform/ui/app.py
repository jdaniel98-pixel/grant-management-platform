"""Streamlit screens: onboarding, organisation profile, funding needs, grants, matches, applications and drafts.

Run from the project root:
    PYTHONPATH=src .venv/bin/streamlit run src/grant_platform/ui/app.py
The database is instance/grant_platform.sqlite3 unless GRANT_PLATFORM_DB is set.
Screens only collect input and show results; all rules live in grant_platform.services.
"""
import os
import re

import streamlit as st

from grant_platform import db
from grant_platform.repository import Repository
from grant_platform.services import ValidationError, ai, applications, changes as change_rules, delivery, drafting, grants, matching, needs, notifications, profile
from grant_platform.ui import styles

PAGES = ("Organisation profile", "Funding needs", "Grants", "Matches", "Applications", "Notifications", "New organisation")
NOT_PROVIDED = "Not provided"


def database_path():
    return os.environ.get("GRANT_PLATFORM_DB") or db.DEFAULT_DB_PATH


def escape_markdown(text):
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~$<])", r"\\\1", str(text))


def show_errors(error, labels):
    for field, message in error.errors.items():
        st.error(f"{labels.get(field, field)}: {message}")


def flash(message):
    st.session_state["_flash"] = message


def flash_error(message):
    st.session_state.setdefault("_errors", []).append(message)


def show_flash():
    message = st.session_state.pop("_flash", None)
    if message:
        st.success(message)
    for error in st.session_state.pop("_errors", []):
        st.error(error)


def request(**action):
    """Button callback: remember what was asked. The work happens at the start of the next run (run_pending), before
    any screen is drawn, so the page is never interrupted half-way through and shows the finished result."""
    st.session_state["_pending"] = action


def run_pending(repository, gateway):
    action = st.session_state.pop("_pending", None)
    if not action:
        return
    kind, organisation_id, need_id = action["kind"], action.get("organisation_id"), action.get("need_id")
    grant = repository.get_grant(action["grant_id"]) if "grant_id" in action else None
    try:
        if kind == "decide":
            applications.decide(repository, organisation_id, grant["id"], need_id, action["decision"])
            flash(f"Now pursuing {grant['title']}" if action["decision"] == "pursue" else f"{action['label']}: {grant['title']}")
        elif kind == "reconsider":
            applications.decide(repository, organisation_id, grant["id"], need_id, "save")
            flash(f"Moved {grant['title']} back to saved")
        elif kind == "verify":
            grants.mark_verified(repository, grant["id"])
            flash(f"Marked {grant['title']} as verified")
        elif kind == "add_contact":
            contact = notifications.add_contact(repository, organisation_id, action["name"], action["email"], consent_given=action["consent"])
            flash(f"Added {contact['name']}" + ("" if contact["consent_given_at"] else ". No email will be sent until consent is recorded"))
        elif kind == "give_consent":
            notifications.record_consent(repository, organisation_id, action["contact_id"])
            flash("Consent recorded")
        elif kind == "pause_contact":
            notifications.update_preferences(repository, organisation_id, action["contact_id"], alerts_frequency="off")
            flash("Alerts paused")
        elif kind == "save_preferences":
            notifications.update_preferences(repository, organisation_id, action["contact_id"], **action["preferences"])
            flash("Preferences saved")
        elif kind == "unsubscribe_contact":
            contact = repository.get_contact(organisation_id, action["contact_id"])
            notifications.unsubscribe(repository, contact["unsubscribe_token"])
            flash(f"{contact['name']} is unsubscribed from all email")
        elif kind == "dismiss_review":
            grants.dismiss_review(repository, grant["id"])
            flash(f"Kept {grant['title']} as it is")
        elif kind == "close_grant":
            grants.close_grant(repository, grant["id"])
            flash(f"Marked {grant['title']} as closed")
        elif kind == "assess":
            with st.spinner(f"Assessing {grant['title']}"):
                matching.assess(repository, organisation_id, grant["id"], need_id, gateway=gateway, model=ai.model_name(), force=action["force"])
            flash(f"Assessed {grant['title']}")
        elif kind == "assess_all":
            skip = applications.rejected_grant_ids(repository, organisation_id, need_id)
            with st.spinner("Assessing grants"):
                results, errors = matching.assess_all(repository, organisation_id, need_id, gateway=gateway, model=ai.model_name(), skip_grant_ids=skip)
            titles = {item["id"]: item["title"] for item in repository.list_grants()}
            for grant_id, message in errors.items():
                flash_error(f"{titles[grant_id]}: {message}")
            flash(f"Assessed {len(results)} grants")
    except matching.MatchError as error:
        flash_error(f"{grant['title'] + ': ' if grant else ''}{error}")
    except ValidationError as error:
        flash_error("; ".join(f"{field}: {message}" for field, message in error.errors.items()))
    except LookupError as error:
        flash_error(str(error))


def option_select(label, options, current, key, help=None):
    """Selectbox whose first choice is 'Not provided' (None); keeps an unlisted stored value selectable."""
    choices = [None, *options]
    if current is not None and current not in choices:
        choices.append(current)
    return st.selectbox(
        label, choices, index=choices.index(current), key=key, help=help,
        format_func=lambda choice: NOT_PROVIDED if choice is None else choice,
    )


def profile_form(prefix, values, submit_label):
    """The onboarding questionnaire, also used to edit a profile. Returns (submitted, raw answers)."""
    labels = profile.FIELD_LABELS
    with st.form(f"{prefix}_form"):
        st.subheader("About your organisation")
        name = st.text_input(f"{labels['name']} (required)", values.get("name") or "", key=f"{prefix}_name")
        organisation_type = option_select(labels["organisation_type"], profile.ORGANISATION_TYPES, values.get("organisation_type"), f"{prefix}_type")
        st.subheader("Registration")
        registration_country = st.text_input(labels["registration_country"], values.get("registration_country") or "", key=f"{prefix}_country")
        legal_form = option_select(labels["legal_form"], profile.LEGAL_FORMS, values.get("legal_form"), f"{prefix}_legal_form")
        registration_number = st.text_input(labels["registration_number"], values.get("registration_number") or "", key=f"{prefix}_number")
        founded_year = st.text_input(labels["founded_year"], str(values.get("founded_year") or ""), key=f"{prefix}_year")
        annual_income_band = option_select(labels["annual_income_band"], profile.INCOME_BANDS, values.get("annual_income_band"), f"{prefix}_income")
        st.subheader("Where you work")
        regions = st.text_area(f"{labels['regions']} (one place per line)", "\n".join(values.get("regions") or []), key=f"{prefix}_regions", height=100)
        st.subheader("What you do")
        mission = st.text_area(f"{labels['mission']} (required)", values.get("mission") or "", key=f"{prefix}_mission")
        activities = st.text_area(labels["activities"], values.get("activities") or "", key=f"{prefix}_activities")
        beneficiaries = st.text_area(labels["beneficiaries"], values.get("beneficiaries") or "", key=f"{prefix}_beneficiaries")
        submitted = st.form_submit_button(submit_label, type="primary", key=f"{prefix}_submit")
    raw = {
        "name": name, "organisation_type": organisation_type, "registration_country": registration_country,
        "legal_form": legal_form, "registration_number": registration_number, "founded_year": founded_year,
        "annual_income_band": annual_income_band, "regions": regions, "mission": mission,
        "activities": activities, "beneficiaries": beneficiaries,
    }
    return submitted, raw


def need_form(prefix, values, submit_label):
    with st.form(f"{prefix}_form"):
        title = st.text_input("Title (required)", values.get("title") or "", key=f"{prefix}_title")
        kind = st.selectbox(
            "What is it for?", list(needs.KINDS), index=list(needs.KINDS).index(values.get("kind", "programme")),
            format_func=needs.KINDS.get, key=f"{prefix}_kind",
        )
        description = st.text_area("What the funding would pay for (required)", values.get("description") or "", key=f"{prefix}_description")
        amount = st.text_input("Amount sought in pounds", str(values.get("amount_sought") or ""), key=f"{prefix}_amount")
        timeframe = st.text_input("Timeframe", values.get("timeframe") or "", key=f"{prefix}_timeframe", help="For example: 12 months from April 2027")
        geography = st.text_input("Where it will happen", values.get("geography") or "", key=f"{prefix}_geography")
        beneficiaries = st.text_input("Who it will help", values.get("beneficiaries") or "", key=f"{prefix}_beneficiaries")
        submitted = st.form_submit_button(submit_label, type="primary", key=f"{prefix}_submit")
    raw = {
        "title": title, "kind": kind, "description": description, "amount_sought": amount,
        "timeframe": timeframe, "geography": geography, "beneficiaries": beneficiaries,
    }
    return submitted, raw


NEED_LABELS = {
    "title": "Title", "kind": "What is it for", "description": "Description", "amount_sought": "Amount",
    "timeframe": "Timeframe", "geography": "Where", "beneficiaries": "Who it helps",
}


def onboarding_page(repository):
    st.title("Tell us about your organisation")
    st.write(
        "These answers become your organisation profile, which the platform uses to judge which grants you may be "
        "eligible for. Only the name and mission are required. Leave anything you are unsure of as 'Not provided': "
        "missing facts are treated as unknown, never as a reason you are ineligible."
    )
    st.caption("Uploading documents to fill this in automatically comes later")
    submitted, raw = profile_form("onboard", {}, "Create profile")
    if submitted:
        try:
            organisation_id = profile.create_organisation(repository, raw)
        except ValidationError as error:
            show_errors(error, profile.FIELD_LABELS)
            return
        st.session_state["_goto"] = ("Organisation profile", organisation_id)
        flash("Profile created. Review it below and add what you left out when you can.")
        st.rerun()


def profile_summary(organisation):
    st.subheader("Your profile")
    for field in profile.EDITABLE_FIELDS:
        value = organisation.get(field)
        if isinstance(value, list):
            value = ", ".join(value)
        shown = escape_markdown(value) if value not in (None, "") else f"_{NOT_PROVIDED}_"
        st.markdown(f"**{profile.FIELD_LABELS[field]}**  \n{shown}")
        note = profile.source_note(organisation, field)
        if note:
            st.caption(note)
    missing = profile.missing_facts(organisation)
    if missing:
        with st.container(key="missing_facts"):
            st.markdown("**Facts we do not have yet**")
            st.write(", ".join(missing))
            st.caption("Funders often set rules on these. Until you provide them, matching will show eligibility as unclear rather than ineligible")


def profile_page(repository, organisation_id):
    organisation = repository.get_organisation(organisation_id)
    st.title(organisation["name"])
    show_flash()
    profile_summary(organisation)
    with st.expander("Edit profile"):
        submitted, raw = profile_form(f"profile_{organisation_id}", organisation, "Save changes")
        if submitted:
            try:
                changed = profile.update_profile(repository, organisation_id, raw)
            except ValidationError as error:
                show_errors(error, profile.FIELD_LABELS)
                return
            flash("Profile saved" if changed else "No changes to save")
            st.rerun()


def needs_page(repository, organisation_id):
    st.title("Funding needs")
    st.write(
        "Create one need for each programme, project or set of running costs you want funding for. "
        "Matching can look at your whole organisation or at one need at a time."
    )
    show_flash()
    existing = repository.list_funding_needs(organisation_id)
    if not existing:
        st.info("You have no funding needs yet. Add your first one below")
    for need in existing:
        with st.container(border=True):
            st.subheader(need["title"])
            st.caption(f"{needs.KINDS[need['kind']]}  ·  Amount sought: {needs.format_amount(need['amount_sought'])}")
            st.write(need["description"])
            details = [f"{label}: {need[field]}" for field, label in (("timeframe", "Timeframe"), ("geography", "Where"), ("beneficiaries", "Who it helps")) if need[field]]
            if details:
                st.caption("  ·  ".join(details))
            with st.expander("Edit"):
                submitted, raw = need_form(f"need_{organisation_id}_{need['id']}", need, "Save changes")
                if submitted:
                    try:
                        needs.update_need(repository, organisation_id, need["id"], raw)
                    except ValidationError as error:
                        show_errors(error, NEED_LABELS)
                    else:
                        flash(f"Saved {raw['title'].strip()}")
                        st.rerun()
    st.subheader("Add a funding need")
    submitted, raw = need_form(f"newneed_{organisation_id}_{len(existing)}", {}, "Add funding need")
    if submitted:
        try:
            created = needs.create_need(repository, organisation_id, raw)
        except ValidationError as error:
            show_errors(error, NEED_LABELS)
        else:
            flash(f"Added {created['title']}")
            st.rerun()


GRANT_LABELS = {
    "title": "Title", "funder": "Funder", "source_url": "Web address", "status": "Status", "deadline_date": "Deadline date",
    "deadline_text": "Deadline as the funder words it", "purpose": "Purpose", "eligibility": "Who can apply",
    "scope": "Where", "funding": "Funding", "duration": "Duration", "key_requirements": "Key requirements", "exclusions": "Exclusions",
}


def grant_form(prefix, by_url, submit_label):
    with st.form(f"{prefix}_form"):
        title = st.text_input("Title (required)", key=f"{prefix}_title")
        funder = st.text_input("Funder (required)", key=f"{prefix}_funder")
        if by_url:
            source_url = st.text_input("Web address (required)", key=f"{prefix}_url", help="For example: https://www.funder.org/funds/youth-fund")
            st.caption("Nothing is fetched from this address. Type in what the funder's page says below")
        else:
            source_url = st.text_input("Web address", key=f"{prefix}_url", help="Leave empty if the grant has no web page")
        status = st.selectbox("Is it open?", list(grants.STATUSES), index=2, format_func=grants.STATUSES.get, key=f"{prefix}_status")
        deadline_date = st.text_input("Deadline date", key=f"{prefix}_deadline_date", help="For example: 31/03/2027. Used for reminders")
        deadline_text = st.text_input("Deadline as the funder words it", key=f"{prefix}_deadline_text")
        purpose = st.text_area("Purpose", key=f"{prefix}_purpose")
        eligibility = st.text_area("Who can apply", key=f"{prefix}_eligibility")
        scope = st.text_input("Where", key=f"{prefix}_scope")
        funding = st.text_input("Funding", key=f"{prefix}_funding", help="For example: £20,000 to £60,000")
        duration = st.text_input("Duration", key=f"{prefix}_duration")
        key_requirements = st.text_area("Key requirements", key=f"{prefix}_key_requirements")
        exclusions = st.text_area("Exclusions", key=f"{prefix}_exclusions")
        submitted = st.form_submit_button(submit_label, type="primary", key=f"{prefix}_submit")
    raw = {
        "title": title, "funder": funder, "source_url": source_url, "status": status, "deadline_date": deadline_date,
        "deadline_text": deadline_text, "purpose": purpose, "eligibility": eligibility, "scope": scope,
        "funding": funding, "duration": duration, "key_requirements": key_requirements, "exclusions": exclusions,
    }
    return submitted, raw


CHANGE_KINDS = {
    "found": "First found", "updated": "Details changed", "reformatted": "Formatting changed only (left as saved)",
    "verification_reset": "Sent back to unverified", "closed_by_deadline": "Closed: closing date passed",
    "flagged_for_review": "Flagged for review", "review_cleared": "Review flag cleared", "closed_by_person": "Closed by a person",
}


def event_lines(event):
    detail = event["detail"]
    if event["kind"] == "updated":
        return [f"{change_rules.FIELD_LABELS.get(change['field'], change['field'])}: {change['old'] or 'blank'} → {change['new'] or 'blank'}" for change in detail.get("changes", [])]
    if event["kind"] in ("verification_reset", "flagged_for_review"):
        return [detail.get("reason", "")]
    if event["kind"] == "closed_by_deadline":
        return [f"Closing date {detail.get('deadline_date')}"]
    return []


def change_history(repository, grant):
    events = repository.list_grant_events(grant["id"])
    if not events:
        return
    with st.expander(f"Change history ({len(events)})", key=f"history_{grant['id']}"):
        for event in events:
            st.markdown(f"**{CHANGE_KINDS[event['kind']]}**  ·  {event['created_at'][:10]}")
            for line in event_lines(event):
                if line:
                    st.markdown(f"> {escape_markdown(line)}")


def grant_card(repository, grant):
    verified = grant["verification_status"] == "verified"
    with st.container(border=True, key=f"grant_{grant['id']}"):
        st.subheader(grant["title"])
        badge = "Verified" if verified else "Unverified: details not yet checked against the funder"
        st.caption(f"{escape_markdown(grant['funder'])}  ·  {grants.STATUSES[grant['status']]}  ·  {badge}")
        if grant["review_reason"]:
            st.warning(f"Needs review: {grant['review_reason']}. Flagged {grant['review_flagged_at'][:10]}")
            keep, close = st.columns(2)
            keep.button("Keep as it is", key=f"keep_{grant['id']}", on_click=request, kwargs={"kind": "dismiss_review", "grant_id": grant["id"]})
            close.button("Mark as closed", key=f"close_{grant['id']}", on_click=request, kwargs={"kind": "close_grant", "grant_id": grant["id"]})
        if grant["verification_reset_reason"] and not verified:
            st.info(f"Verification was reset: {grant['verification_reset_reason']}")
        st.write(f"Deadline: {grants.deadline_label(grant)}")
        for field in ("purpose", "eligibility", "scope", "funding", "duration", "key_requirements", "exclusions"):
            if grant[field]:
                st.markdown(f"**{GRANT_LABELS[field]}**  \n{escape_markdown(grant[field])}")
        if grant["canonical_url"]:
            st.markdown("**Source**")
            st.text(grant["canonical_url"])  # plain text: never interpreted as markup
            st.link_button("Open source page", grant["canonical_url"], key=f"grant_{grant['id']}_open")
        else:
            st.markdown("**Source**  \n_No web address recorded_")
        source = repository.get_source(grant["source_id"]) if grant["source_id"] else None
        if source:
            licence = f"  ·  {escape_markdown(source['licence'])}" if source["licence"] else ""
            st.caption(f"Monitored source: {escape_markdown(source['name'])}{licence}")
        st.caption(
            f"{grants.ORIGINS[grant['origin']]}  ·  First saved {grant['first_seen_at'][:10]}  ·  Last seen {grant['last_seen_at'][:10]}"
        )
        if grant["last_checked_at"]:
            changed = grant["last_changed_at"][:10] if grant["last_changed_at"] else "never"
            st.caption(f"Last checked {grant['last_checked_at'][:10]}  ·  Details last changed {changed}")
        if verified:
            st.caption(f"Verified {grant['verified_at'][:10]} by {escape_markdown(grant['verified_by'])}")
        else:
            st.button("Mark verified", key=f"verify_{grant['id']}", on_click=request, kwargs={"kind": "verify", "grant_id": grant["id"]})
        change_history(repository, grant)


def grants_page(repository):
    st.title("Grants")
    st.write(
        "Grants are shared by every organisation. Each one shows where it came from and whether anyone has checked "
        "its details against the funder's own page. Grants you add are typed in by hand, and nothing is fetched when you add one. "
        "Grants found by a monitored source are never marked verified automatically, and if their details change they go back to unverified"
    )
    st.warning(
        "Development only: there is no login yet, so anyone using this app can press Mark verified. "
        "It is a placeholder, not secure administrator access"
    )
    show_flash()
    existing = repository.list_grants()
    if not existing:
        st.info("No grants saved yet. Add one below")
    for grant in existing:
        grant_card(repository, grant)
    st.subheader("Add a grant")
    manual_tab, url_tab = st.tabs(["Add by hand", "Add by web address"])
    for tab, by_url, label in ((manual_tab, False, "Add grant"), (url_tab, True, "Add grant from web address")):
        with tab:
            prefix = f"{'urlgrant' if by_url else 'newgrant'}_{len(existing)}"
            submitted, raw = grant_form(prefix, by_url, label)
            if submitted:
                try:
                    created = grants.create_grant(repository, raw, origin="url" if by_url else "manual")
                except ValidationError as error:
                    show_errors(error, GRANT_LABELS)
                else:
                    flash(f"Added {created['title']}. It is unverified until someone checks it")
                    st.rerun()


ELIGIBILITY_ICONS = {"eligible": "Eligible", "unclear": "Unclear", "ineligible": "Ineligible"}
ITEM_LABELS = {"met": "Met", "not_met": "Not met", "unknown": "Unknown"}


def match_card(repository, organisation_id, need_id, grant, match, gateway):
    with st.container(border=True, key=f"match_{need_id or 0}_{grant['id']}"):
        st.subheader(grant["title"])
        label = "Based on unverified grant information" if not match["grant_verified"] else "Grant details verified"
        st.caption(f"{escape_markdown(grant['funder'])}  ·  {label}  ·  Deadline: {grants.deadline_label(grant)}")
        for warning in matching.grant_warnings(grant):
            st.warning(warning)
        if not matching.is_current(repository, organisation_id, grant["id"], need_id, model=ai.model_name()):
            st.caption("The grant, profile or funding need has changed since this was assessed. Assess it again for an up-to-date result")
        st.markdown(f"**Eligibility:** {matching.STATUS_LABELS[match['eligibility_status']]}")
        st.markdown(f"**Suitability:** {match['band']}, {match['suitability_total']} out of {matching.MAX_SCORE}")
        st.markdown(f"**Suggested next step:** {match['recommendation']}")
        if match["exclusion_conflict"].get("verified"):
            st.markdown(f"**Conflict with the grant's exclusions:** {escape_markdown(match['exclusion_conflict']['reasoning'])}")
        st.write(escape_markdown(match["summary"]))
        if match["eligibility_status"] == "ineligible":
            st.caption("Suitability is shown for information only. Eligibility decides first")
        with st.expander("Eligibility checks"):
            if not match["eligibility_items"]:
                st.write("No eligibility requirements could be checked")
            for entry in match["eligibility_items"]:
                st.markdown(f"**{escape_markdown(entry['requirement'])}**: {ITEM_LABELS[entry['status']]}")
                if entry["grant_quote"]:
                    st.caption(f"Grant says: “{escape_markdown(entry['grant_quote'])}”")
                if entry["organisation_fact"]:
                    st.caption(f"Your information: “{escape_markdown(entry['organisation_fact'])}”" + ("" if entry["verified"] else "  ·  not confirmed against your profile or the grant"))
                st.caption(escape_markdown(entry["reasoning"]))
        with st.expander("Suitability scores"):
            for name, label in matching.CRITERIA.items():
                criterion = match["suitability"][name]
                st.markdown(f"**{label}:** {criterion['score']} out of 5")
                st.caption(escape_markdown(criterion["reasoning"]))
        gaps = matching.profile_gaps(repository.get_organisation(organisation_id)) if match["eligibility_status"] == "unclear" else []
        if match["uncertainties"] or match["missing_information"] or gaps:
            with st.expander("Uncertainties and missing information"):
                for text in match["uncertainties"]:
                    st.markdown(f"- {escape_markdown(text)}")
                for text in match["missing_information"]:
                    st.markdown(f"- Missing: {escape_markdown(text)}")
                if gaps:
                    st.markdown(f"- Your profile does not say: {', '.join(gaps)}")
        st.caption(f"Assessed {match['created_at'][:10]} with {escape_markdown(match['model'])}  ·  prompt {match['prompt_version']}")
        decision_buttons(repository, organisation_id, need_id, grant)
        if gateway:
            st.button("Assess again", key=f"reassess_{need_id or 0}_{grant['id']}", on_click=request,
                      kwargs={"kind": "assess", "organisation_id": organisation_id, "need_id": need_id, "grant_id": grant["id"], "force": True})


def decision_buttons(repository, organisation_id, need_id, grant):
    """You decide: save it for later, pursue it (starts tracking) or reject it (hides it for this need)."""
    application = repository.find_application(organisation_id, need_id, grant["id"])
    if application:
        st.caption(f"Application: {applications.STATUSES[application['status']]}. Track it on the Applications page")
        return
    columns = st.columns(3)
    for column, (decision, label) in zip(columns, (("save", "Save for later"), ("pursue", "Pursue"), ("reject", "Reject"))):
        with column:
            st.button(
                label, key=f"decide_{need_id or 0}_{grant['id']}_{decision}", use_container_width=True, on_click=request,
                kwargs={"kind": "decide", "organisation_id": organisation_id, "need_id": need_id, "grant_id": grant["id"], "decision": decision, "label": label},
            )


def matches_page(repository, organisation_id, gateway):
    st.title("Matches")
    st.write(
        "Eligibility asks whether you can apply. Suitability asks whether the grant is a good fit. They are kept apart: "
        "an unclear eligibility result means a fact is missing, never that you are ineligible. You decide what to pursue"
    )
    show_flash()
    funding_needs = repository.list_funding_needs(organisation_id)
    options = [None, *(need["id"] for need in funding_needs)]
    titles = {need["id"]: need["title"] for need in funding_needs}
    need_id = st.selectbox(
        "Assess for", options, key="match_scope",
        format_func=lambda choice: "Whole organisation" if choice is None else titles[choice],
    )
    all_grants = repository.list_grants()
    if not all_grants:
        st.info("There are no grants to assess yet. Add some on the Grants page")
        return
    rejected = applications.rejected_grant_ids(repository, organisation_id, need_id)
    existing = [grant for grant in all_grants if grant["id"] not in rejected]
    if gateway is None:
        st.info(
            "AI assessment is not set up on this computer, so new matches cannot be made. Add OPENAI_API_KEY to the "
            ".env file in the project folder (see .env.example) and restart the app. Saved results still show below"
        )
    else:
        st.caption(
            "Assessing sends this grant and your organisation profile and funding need to OpenAI. Registration numbers and "
            "evidence records are not sent. Use fictional data only until the data-protection work is done. "
            "Grants already assessed with unchanged details are not run again"
        )
        st.button("Assess all grants", key="assess_all", type="primary", on_click=request,
                  kwargs={"kind": "assess_all", "organisation_id": organisation_id, "need_id": need_id})
    stored = {match["grant_id"]: match for match in repository.list_matches(organisation_id, need_id) if match["grant_id"] not in rejected}
    grants_by_id = {grant["id"]: grant for grant in existing}
    for match in matching.rank(stored.values()):
        match_card(repository, organisation_id, need_id, grants_by_id[match["grant_id"]], match, gateway)
    waiting = [grant for grant in existing if grant["id"] not in stored]
    if waiting:
        st.subheader("Not assessed yet")
        for grant in waiting:
            with st.container(border=True, key=f"waiting_{need_id or 0}_{grant['id']}"):
                st.markdown(f"**{escape_markdown(grant['title'])}**")
                st.caption(f"{escape_markdown(grant['funder'])}  ·  Deadline: {grants.deadline_label(grant)}")
                if gateway:
                    st.button("Assess", key=f"assess_{need_id or 0}_{grant['id']}", on_click=request,
                              kwargs={"kind": "assess", "organisation_id": organisation_id, "need_id": need_id, "grant_id": grant["id"], "force": False})
    if rejected:
        with st.expander(f"Rejected for this scope ({len(rejected)})"):
            st.caption("Rejected grants are left out of suggestions and are not assessed")
            for grant in all_grants:
                if grant["id"] in rejected:
                    st.markdown(f"**{escape_markdown(grant['title'])}**  ·  {escape_markdown(grant['funder'])}")
                    st.button("Reconsider", key=f"reconsider_{need_id or 0}_{grant['id']}", on_click=request,
                              kwargs={"kind": "reconsider", "organisation_id": organisation_id, "need_id": need_id, "grant_id": grant["id"]})


APPLICATION_FILTERS = {
    "Active": ("saved", "pursuing", "drafting", "submitted"),
    "Finished": ("awarded", "unsuccessful", "withdrawn"),
    "Rejected": ("rejected",),
    "All": tuple(applications.STATUSES),
}


def keep_open(application_id):
    """After an action inside an application's workspace, show that workspace open again."""
    st.session_state["open_application"] = application_id


def draft_card(repository, organisation_id, draft):
    draft_id = draft["id"]
    with st.container(border=True, key=f"draft_{draft_id}"):
        st.markdown(f"**Question:** {escape_markdown(draft['question'])}")
        limit = f"  ·  Word limit: {draft['word_limit']}" if draft["word_limit"] else ""
        status = "Reviewed by you" if draft["reviewed_by_user"] else "AI draft, not reviewed"
        st.caption(f"{status}  ·  Drafted {draft['created_at'][:10]} with {escape_markdown(draft['model'])}{limit}")
        for flag in draft["review_flags"]:
            st.warning(flag)
        text = st.text_area("Answer", value=drafting.current_text(draft), height=240, key=f"draft_{draft_id}_text")
        st.caption(f"{drafting.word_count(text)} words")
        save, review = st.columns(2)
        with save:
            if st.button("Save edits", key=f"draft_{draft_id}_save"):
                try:
                    drafting.save_edit(repository, organisation_id, draft_id, text)
                except ValidationError as error:
                    show_errors(error, {"text": "Answer"})
                else:
                    flash("Saved your edits. The original AI text is kept")
                    keep_open(draft["application_id"])
                    st.rerun()
        with review:
            if not draft["reviewed_by_user"] and st.button("Mark as reviewed", key=f"draft_{draft_id}_review"):
                try:
                    drafting.save_edit(repository, organisation_id, draft_id, text)
                except ValidationError as error:
                    show_errors(error, {"text": "Answer"})
                else:
                    drafting.set_reviewed(repository, organisation_id, draft_id)
                    flash("Marked as reviewed by you")
                    keep_open(draft["application_id"])
                    st.rerun()
        with st.expander(f"Evidence cited ({len(draft['citations'])})"):
            if not draft["citations"]:
                st.write("No evidence is cited")
            for citation in drafting.citation_records(repository, organisation_id, draft):
                record = citation["record"]
                st.markdown(f"**{escape_markdown(citation['record_id'])}**: {escape_markdown(citation['claim'])}")
                if record and citation["verified"]:
                    period = f" ({record['reporting_period']})" if record["reporting_period"] else ""
                    st.caption(f"Approved record{period}: {escape_markdown(record['text'])}")
                else:
                    st.caption("Not one of the approved records supplied for this draft, so it cannot be checked")
        if draft["missing_evidence"]:
            st.markdown("**Missing evidence**")
            for item in draft["missing_evidence"]:
                st.markdown(f"- {escape_markdown(item)}")
        with st.expander("Original AI text"):
            st.text(draft["ai_text"] or "(no text)")


def drafting_section(repository, organisation_id, application, gateway):
    app_id = application["id"]
    st.markdown("**Drafting**")
    if application["status"] == "rejected":
        st.caption("This grant was rejected. Reconsider it from the Matches page to draft")
        return
    if gateway is None:
        st.info("AI drafting is not set up on this computer. You can still preview the evidence a draft would use and the gaps in it")
    else:
        st.caption(
            "Drafting sends the question, the grant, your funding need and your approved evidence records to OpenAI. "
            "Use fictional data only until the data-protection work is done"
        )
    preview = None
    with st.form(f"app_{app_id}_draft_form"):
        question = st.text_area("Application question", key=f"app_{app_id}_question")
        word_limit = st.text_input("Word limit (optional)", key=f"app_{app_id}_word_limit")
        guidance = st.text_area("Funder guidance (optional)", key=f"app_{app_id}_guidance")
        write = st.form_submit_button("Draft answer", type="primary", key=f"app_{app_id}_draft") if gateway else False
        look = st.form_submit_button("Preview evidence and gaps", key=f"app_{app_id}_preview")
    labels = {"question": "Question", "word_limit": "Word limit", "guidance": "Guidance", "application": "Application"}
    if write:
        try:
            with st.spinner("Drafting"):
                drafting.draft(repository, organisation_id, app_id, question, word_limit=word_limit, guidance=guidance, gateway=gateway, model=ai.model_name())
        except ValidationError as error:
            show_errors(error, labels)
        except matching.MatchError as error:
            st.error(str(error))
        else:
            flash("Draft created. It is an AI draft: check it against the evidence before using it")
            keep_open(app_id)
            st.rerun()
    elif look:
        try:
            preview = drafting.preview(repository, organisation_id, app_id, question, word_limit, guidance)
        except ValidationError as error:
            show_errors(error, labels)
    if preview:
        st.markdown(f"**{len(preview['records'])} approved records would be used**")
        for gap in preview["missing_evidence"]:
            st.warning(f"Known gap: {gap}")
        if not preview["missing_evidence"]:
            st.caption("No known gaps in the evidence bear on this question")
        with st.expander("Records that would be sent"):
            for record in preview["records"]:
                st.caption(f"{escape_markdown(record['record_key'])}: {escape_markdown(record['text'])}")
    for draft in repository.list_drafts(organisation_id, app_id):
        draft_card(repository, organisation_id, draft)


def application_workspace(repository, organisation_id, application, gateway=None):
    app_id = application["id"]
    allowed = applications.next_statuses(application)
    if allowed:
        with st.form(f"app_{app_id}_status_form"):
            to_status = st.selectbox("Move to", allowed, format_func=applications.STATUSES.get, key=f"app_{app_id}_next")
            note = st.text_input("Note (optional)", key=f"app_{app_id}_note")
            amount = st.text_input("Amount awarded in pounds (only when moving to Awarded)", key=f"app_{app_id}_amount")
            if st.form_submit_button("Update status", key=f"app_{app_id}_update"):
                try:
                    applications.change_status(repository, organisation_id, app_id, to_status, note=note, amount_awarded=amount)
                except ValidationError as error:
                    show_errors(error, {"status": "Status", "note": "Note", "amount_awarded": "Amount awarded"})
                else:
                    flash(f"{application['grant_title']} is now {applications.STATUSES[to_status].lower()}")
                    keep_open(app_id)
                    st.rerun()
    else:
        st.caption("This is a final outcome")
    if application["outcome_note"] or application["amount_awarded"]:
        outcome = [f"Amount awarded: {needs.format_amount(application['amount_awarded'])}"] if application["amount_awarded"] else []
        st.markdown("**Outcome**  \n" + "  \n".join([*outcome, escape_markdown(application["outcome_note"] or "")]).strip())

    st.markdown("**Requirements checklist**")
    for item in repository.list_requirements(organisation_id, app_id):
        ticked = st.checkbox(item["text"], value=item["done"], key=f"req_{app_id}_{item['id']}")
        if ticked != item["done"]:
            applications.set_requirement_done(repository, organisation_id, app_id, item["id"], ticked)
            keep_open(app_id)
            st.rerun()
    with st.form(f"app_{app_id}_requirement_form", clear_on_submit=True):
        text = st.text_input("Add a requirement", key=f"app_{app_id}_requirement_text")
        if st.form_submit_button("Add requirement", key=f"app_{app_id}_requirement_add"):
            try:
                applications.add_requirement(repository, organisation_id, app_id, text)
            except ValidationError as error:
                show_errors(error, {"text": "Requirement"})
            else:
                keep_open(app_id)
                st.rerun()

    st.markdown("**Tasks**")
    tasks = repository.list_tasks(organisation_id, app_id)
    if not tasks:
        st.caption("No tasks yet")
    for task in tasks:
        due = f" (due {task['due_date']})" if task["due_date"] else ""
        ticked = st.checkbox(f"{task['text']}{due}", value=task["done"], key=f"task_{app_id}_{task['id']}")
        if ticked != task["done"]:
            applications.set_task_done(repository, organisation_id, app_id, task["id"], ticked)
            keep_open(app_id)
            st.rerun()
    with st.form(f"app_{app_id}_task_form", clear_on_submit=True):
        text = st.text_input("Add a task", key=f"app_{app_id}_task_text")
        due = st.text_input("Due date (optional)", key=f"app_{app_id}_task_due", help="For example: 15/03/2027")
        if st.form_submit_button("Add task", key=f"app_{app_id}_task_add"):
            try:
                applications.add_task(repository, organisation_id, app_id, text, due)
            except ValidationError as error:
                show_errors(error, {"text": "Task", "due_date": "Due date"})
            else:
                keep_open(app_id)
                st.rerun()

    drafting_section(repository, organisation_id, application, gateway)

    st.markdown("**History**")
    for event in repository.list_application_events(organisation_id, app_id):
        before = f"{applications.STATUSES[event['from_status']]} to " if event["from_status"] else "Started as "
        note = f": {escape_markdown(event['note'])}" if event["note"] else ""
        st.caption(f"{event['created_at'][:10]}  ·  {before}{applications.STATUSES[event['to_status']]}{note}")


def applications_page(repository, organisation_id, gateway=None):
    st.title("Applications")
    st.write("Grants you have saved, are pursuing or have finished with, with their deadlines, checklists and tasks. Nothing is sent to any funder from here")
    st.caption(
        "Deadline reminders are written to the outbox folder next to the database by running: "
        "PYTHONPATH=src .venv/bin/python -m grant_platform.jobs notify. They are not emailed"
    )
    show_flash()
    choice = st.selectbox("Show", list(APPLICATION_FILTERS), key="application_filter")
    everything = applications.list_applications(repository, organisation_id, include_rejected=True)
    rows = [row for row in everything if row["status"] in APPLICATION_FILTERS[choice]]
    if not everything:
        st.info("No applications here yet. Save or pursue a grant from the Matches page")
    elif not rows:
        st.info(f"Nothing under {choice.lower()} right now. Change the filter above to see the others")
    for application in rows:
        done, total, open_tasks = applications.progress(repository, organisation_id, application["id"])
        with st.container(border=True, key=f"application_{application['id']}"):
            st.subheader(application["grant_title"])
            st.caption(f"{escape_markdown(application['grant_funder'])}  ·  For: {escape_markdown(applications.scope_label(application))}")
            st.markdown(f"**Status:** {applications.STATUSES[application['status']]}")
            st.write(f"Deadline: {grants.deadline_label(application)}")
            days_left = application["days_left"]
            if application["status"] in ("saved", "pursuing", "drafting") and days_left is not None and days_left < 0:
                st.warning("The deadline has passed")
            if application["status"] != "rejected":
                st.caption(f"Requirements: {done} of {total} done  ·  Open tasks: {open_tasks}")
            with st.expander("Open workspace", expanded=st.session_state.get("open_application") == application["id"]):
                application_workspace(repository, organisation_id, application, gateway)


def contact_card(repository, organisation_id, contact):
    key = f"contact_{contact['id']}"
    with st.container(border=True, key=key):
        st.subheader(contact["name"])
        if contact["unsubscribed_at"]:
            state = f"Unsubscribed {contact['unsubscribed_at'][:10]}: no email will be sent"
        elif not contact["consent_given_at"]:
            state = "No consent recorded: no email will be sent"
        elif contact["alerts_frequency"] == "off":
            state = "Alerts paused"
        else:
            state = "Receives alerts"
        st.caption(f"{escape_markdown(contact['email'])}  ·  {state}")
        if contact["consent_given_at"]:
            st.caption(f"Consent recorded {contact['consent_given_at'][:10]} ({escape_markdown(contact['consent_recorded_by'])})")
        else:
            st.button("Record consent", key=f"{key}_consent", on_click=request,
                      kwargs={"kind": "give_consent", "organisation_id": organisation_id, "contact_id": contact["id"]})
        with st.form(f"{key}_form"):
            frequency = st.selectbox("How often", list(notifications.FREQUENCIES), index=list(notifications.FREQUENCIES).index(contact["alerts_frequency"]),
                                     format_func=notifications.FREQUENCIES.get, key=f"{key}_frequency")
            weekday = st.selectbox("Weekly digest day", range(7), index=contact["digest_weekday"], format_func=lambda day: notifications.WEEKDAYS[day], key=f"{key}_weekday")
            band = st.selectbox("Lowest fit to tell me about", list(notifications.BANDS), index=notifications.BANDS.index(contact["min_band"]), key=f"{key}_band")
            unclear = st.checkbox("Include grants where eligibility is unclear", contact["include_unclear"], key=f"{key}_unclear")
            verified = st.checkbox("Only grants a person has checked against the funder's page", contact["verified_only"], key=f"{key}_verified")
            reminders = st.checkbox("Send deadline reminders", contact["reminders_enabled"], key=f"{key}_reminders")
            saved = st.form_submit_button("Save preferences", key=f"{key}_save")
        if saved:
            request(kind="save_preferences", organisation_id=organisation_id, contact_id=contact["id"], preferences={
                "alerts_frequency": frequency, "digest_weekday": weekday, "min_band": band, "include_unclear": unclear,
                "verified_only": verified, "reminders_enabled": reminders})
            st.rerun()
        if not contact["unsubscribed_at"]:
            st.button("Unsubscribe from all email", key=f"{key}_unsubscribe", on_click=request,
                      kwargs={"kind": "unsubscribe_contact", "organisation_id": organisation_id, "contact_id": contact["id"]})


def notifications_page(repository, organisation_id):
    st.title("Notifications")
    st.write(
        "Alerts about new grants that look relevant to this organisation, and deadline reminders. Each person receives only "
        "this organisation's matches, each grant is offered to them once, and nobody is emailed without recorded consent"
    )
    ai.load_env(names=delivery.ENV_NAMES)
    settings = delivery.EmailSettings.from_env()
    st.warning(
        "Development only: there is no login yet, so anyone using this app can add contacts and record consent. "
        + ("Email sending is ON, but only to the allow-listed addresses in the local settings" if settings.enabled else
           "Email sending is switched off: messages are written to a folder on this computer and nothing is emailed")
    )
    show_flash()
    contacts = repository.list_contacts(organisation_id)
    if not contacts:
        st.info("No contacts yet. Add one below")
    for contact in contacts:
        contact_card(repository, organisation_id, contact)
    st.subheader("Add a contact")
    with st.form("new_contact_form", clear_on_submit=True):
        name = st.text_input("Name", key="new_contact_name")
        email = st.text_input("Email address", key="new_contact_email")
        consent = st.checkbox(notifications.CONSENT_WORDING, key="new_contact_consent")
        submitted = st.form_submit_button("Add contact", type="primary", key="new_contact_submit")
    if submitted:
        request(kind="add_contact", organisation_id=organisation_id, name=name, email=email, consent=consent)
        st.rerun()
    messages = repository.list_messages(organisation_id)
    if messages:
        st.subheader("Messages written")
        names = {contact["id"]: contact["name"] for contact in contacts}
        for message in reversed(messages):
            kind = {"digest": "Digest", "grant_alert": "Grant offered", "deadline_reminder": "Reminder"}[message["kind"]]
            st.caption(f"{message['created_at'][:10]}  ·  {kind}  ·  {escape_markdown(names.get(message['contact_id'], ''))}  ·  {escape_markdown(message['outbox_file'] or message['trigger_key'])}")


def run(repository, gateway=None):
    run_pending(repository, gateway)
    if "_goto" in st.session_state:  # set before the widgets below exist, so it may drive them
        page, organisation_id = st.session_state.pop("_goto")
        st.session_state["page"], st.session_state["organisation"] = page, organisation_id
    st.sidebar.title("Grant Management Platform")
    organisations = repository.list_organisations()
    if not organisations:
        onboarding_page(repository)
        return
    names = {organisation["id"]: organisation["name"] for organisation in organisations}
    if st.session_state.get("organisation") not in names:
        st.session_state["organisation"] = organisations[0]["id"]
    if st.session_state.get("page") not in PAGES:
        st.session_state["page"] = PAGES[0]
    organisation_id = st.sidebar.selectbox("Organisation", list(names), format_func=names.get, key="organisation")
    page = st.sidebar.radio("Go to", PAGES, key="page")
    if page == "New organisation":
        onboarding_page(repository)
    elif page == "Funding needs":
        needs_page(repository, organisation_id)
    elif page == "Grants":
        grants_page(repository)
    elif page == "Matches":
        matches_page(repository, organisation_id, gateway)
    elif page == "Applications":
        applications_page(repository, organisation_id, gateway)
    elif page == "Notifications":
        notifications_page(repository, organisation_id)
    else:
        profile_page(repository, organisation_id)


def main():
    st.set_page_config(page_title="Grant Management Platform", layout="wide")
    styles.apply()
    connection = db.connect(database_path())
    try:
        run(Repository(connection), ai.default_gateway())
    finally:
        connection.close()


if __name__ == "__main__":
    main()
