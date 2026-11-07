"""Shared Visual Design System v1.0 for the Streamlit presentation layer.

light-dark() follows Streamlit's native color-scheme, including theme changes
without a Python rerun. Semantic accents are separate from brand/action colours.

UI copy: omit terminal full stops from short interface text; retain normal punctuation in prose/content.
"""
import streamlit as st

TOKENS = {
    'brand': ('#176B52', '#4EAD88'), 'brand-hover': ('#10513E', '#68BE9D'),
    'brand-subtle': ('#E7F2ED', '#203A30'), 'sage': ('#7FA996', '#7FA996'),
    'sage-light': ('#EDF4F1', '#252C28'), 'background': ('#F7F7F3', '#151917'),
    'surface': ('#FFFFFF', '#1D2320'), 'elevated': ('#EDF4F1', '#252C28'),
    'border': ('#E3E5E1', '#343D38'), 'text': ('#202421', '#F1F4F2'),
    'secondary': ('#626A65', '#B6BDB9'), 'muted': ('#8A918D', '#8E9792'),
    'success': ('#23835E', '#55B88A'), 'success-bg': ('#E7F4ED', '#1D392C'),
    'warning': ('#B7791F', '#E1AD55'), 'warning-bg': ('#FFF4DD', '#3A3020'),
    'danger': ('#C44747', '#E47575'), 'danger-bg': ('#FBEAEA', '#3E2525'),
    'info': ('#3973A8', '#70A7D2'), 'info-bg': ('#EAF2F8', '#203342'),
}

CSS = """
.stApp {font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.stApp :is(p,li,label,input,textarea) {font-family:inherit;font-size:16px;line-height:1.5;}
.stApp h1 {font-size:32px;font-weight:700;line-height:1.25;}
.stApp h2 {font-size:24px;font-weight:650;line-height:1.3;}
.stApp h3 {font-size:18px;font-weight:600;line-height:1.4;}
[data-testid="stCaptionContainer"] {color:var(--secondary);font-size:14px;}
[data-testid="stCaptionContainer"] p {color:inherit;font-size:14px;}
[data-testid="stMainBlockContainer"] {padding-left:32px;padding-right:32px;}
[data-testid="stVerticalBlock"][class*="st-key-ds_card_"] {
    background:var(--surface);border-radius:12px;padding:24px;
}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:var(--border);border-radius:12px;box-shadow:none;}
.stApp button, .stApp [data-testid="stLinkButton"] a {
    border-radius:8px;font-weight:600;font-size:15px;box-shadow:none;
}
.stApp button p, .stApp [data-testid="stLinkButton"] a p {font-size:15px;color:inherit;}
.stApp :is(button[kind="primary"],a[kind="primary"]) {
    background:var(--brand);border-color:var(--brand);color:light-dark(#FFFFFF,#151917);
}
.stApp :is(button[kind="primary"],a[kind="primary"]):hover {
    background:var(--brand-hover);border-color:var(--brand-hover);color:light-dark(#FFFFFF,#151917);
}
.stApp :is(button[kind="secondary"],a[kind="secondary"]) {
    background:transparent;border-color:var(--border);color:var(--text);
}
.stApp :is(button,a):focus-visible {outline:2px solid var(--brand);outline-offset:3px;}
[data-testid="stExpander"] {border-color:var(--border);border-radius:8px;background:var(--surface);}
[data-testid="stExpander"] summary {font-size:15px;color:var(--text);}
[data-testid="stSidebar"] {background:var(--sage-light);}
[data-testid="stSidebar"] h1 {font-size:24px;}
[data-testid="stSidebarContent"] {padding-left:16px;padding-right:16px;}
[data-testid="stSidebarUserContent"] {padding:8px 0 16px;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:not([class*="st-key-"]) {gap:8px;}
[data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style) {display:none;}
[data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button {
    justify-content:flex-start;text-align:left;background:transparent;border:0;color:var(--secondary);
}
[data-testid="stSidebar"] [class*="st-key-researcher_nav_"] button[kind="primary"] {
    background:var(--brand-subtle);color:light-dark(#176B52,#68BE9D);font-weight:600;
}
:is(.st-key-sidebar_applications_card,.st-key-sidebar_connect_tools) {
    background:var(--surface);border:1px solid var(--border);border-radius:12px;
    padding:16px;margin:8px 0 0;gap:4px;
}
:is(.st-key-sidebar_applications_card,.st-key-sidebar_connect_tools) [data-testid="stVerticalBlock"] {gap:4px;}
.st-key-sidebar_applications_card button {justify-content:flex-start;text-align:left;padding:4px 0;min-height:0;width:100%;}
.st-key-sidebar_applications_card button:hover {background:var(--brand-subtle);}
.st-key-sidebar_applications_card button p {text-align:left;font-size:13px;line-height:1.3;color:var(--secondary);margin:0;}
.st-key-sidebar_applications_card button strong {font-size:14px;color:var(--text);font-weight:600;}
.st-key-sidebar_applications_card button em {display:inline-block;font-style:normal;font-size:12px;font-weight:600;
    border-radius:999px;padding:2px 6px;margin-left:4px;background:var(--elevated);color:var(--secondary);}
.st-key-sidebar_applications_card [class*="deadline_amber"] button em {background:var(--warning-bg);color:var(--text);border:1px solid var(--warning);}
.st-key-sidebar_applications_card :is([class*="deadline_red"],[class*="deadline_urgent"]) button em {background:var(--danger-bg);color:var(--text);border:1px solid var(--danger);}
@keyframes deadline-pulse {50% {opacity:.8;}}
.st-key-sidebar_applications_card [class*="deadline_urgent"] button em {animation:deadline-pulse 3s ease-in-out infinite;}
@media(prefers-reduced-motion:reduce) {.st-key-sidebar_applications_card [class*="deadline_urgent"] button em {animation:none;}}
.st-key-sidebar_connect_tools {background:var(--elevated);}
.st-key-sidebar_connect_tools :is(p,strong) {color:var(--text);}
.st-key-sidebar_connect_tools [data-testid="stCaptionContainer"] p {color:var(--secondary);}
.st-key-development_test_reset {margin-top:8px;border-top:1px solid var(--border);padding-top:8px;}
.st-key-workspace_navigation {background:var(--elevated);border:1px solid var(--border);border-radius:8px;padding:4px;margin:24px 0 16px;}
.st-key-workspace_navigation [data-testid="stHorizontalBlock"] {gap:0;}
.st-key-workspace_navigation button {min-height:40px;border:1px solid transparent;background:transparent;padding:8px;}
.st-key-workspace_navigation [class*="workspace_nav_selected"] button {background:var(--brand-subtle);color:var(--brand-hover);border-color:var(--brand);}
.st-key-workspace_navigation [class*="missing_attention"] button {background:var(--warning-bg);color:var(--text);}
.st-key-workspace_navigation [class*="workspace_nav_selected_missing_attention"] button {border-color:var(--warning);}
.st-key-workspace_navigation button :is(p,span) {color:inherit;}
.st-key-workspace_navigation [class*="missing_attention"] button strong {display:inline-block;border-radius:999px;padding:2px 6px;font-size:12px;background:var(--warning);color:light-dark(#202421,#151917);}
.st-key-workspace_reminder {background:var(--info-bg);border:1px solid var(--info);border-radius:12px;padding:16px;margin:16px 0;}
.st-key-review_issues [data-testid="stExpander"] {border-left:3px solid var(--warning);}
.st-key-review_issues [data-testid="stExpander"] summary {background:var(--warning-bg);color:var(--text);}
.st-key-review_issues summary :is(p,span) {color:inherit;}
.st-key-decision_pursue button {background:var(--success-bg);border-color:var(--success);color:var(--text);}
.st-key-decision_review button {background:var(--warning-bg);border-color:var(--warning);color:var(--text);}
.st-key-decision_reject button {background:var(--danger-bg);border-color:var(--danger);color:var(--text);}
:is(.st-key-decision_pursue,.st-key-decision_review,.st-key-decision_reject) button :is(p,span,strong) {color:inherit;}
.st-key-confirm_test_reset button {background:var(--danger);border-color:var(--danger);color:light-dark(#FFFFFF,#151917);}
.st-key-confirm_test_reset button:hover {background:var(--danger-bg);color:var(--text);border-color:var(--danger);}
@media(max-width:640px) {
 [data-testid="stMainBlockContainer"] {padding-left:16px;padding-right:16px;}
 [data-testid="stVerticalBlock"][class*="st-key-ds_card_"] {padding:16px;}
 .st-key-workspace_navigation button {padding:4px;font-size:13px;}
}
"""


def apply_design_system():
    variables = ';'.join(f'--{key}:light-dark({light},{dark})' for key, (light, dark) in TOKENS.items())
    st.html(f'<style>.stApp {{{variables};}}{CSS}</style>')
