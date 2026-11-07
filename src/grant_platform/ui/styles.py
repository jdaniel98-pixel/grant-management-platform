"""Visual design tokens and base styles, adapted from the PI Tool's ui_styles.py (same palette and type scale).

UI copy: omit terminal full stops from short interface text; retain normal punctuation in prose.
"""
import streamlit as st

TOKENS = {
    'brand': ('#176B52', '#4EAD88'), 'brand-hover': ('#10513E', '#68BE9D'),
    'brand-subtle': ('#E7F2ED', '#203A30'), 'sage-light': ('#EDF4F1', '#252C28'),
    'background': ('#F7F7F3', '#151917'), 'surface': ('#FFFFFF', '#1D2320'),
    'elevated': ('#EDF4F1', '#252C28'), 'border': ('#E3E5E1', '#343D38'),
    'text': ('#202421', '#F1F4F2'), 'secondary': ('#626A65', '#B6BDB9'),
    'warning': ('#B7791F', '#E1AD55'), 'warning-bg': ('#FFF4DD', '#3A3020'),
}

CSS = """
.stApp {font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
.stApp :is(p,li,label,input,textarea) {font-family:inherit;font-size:16px;line-height:1.5;}
.stApp h1 {font-size:32px;font-weight:700;line-height:1.25;}
.stApp h2 {font-size:24px;font-weight:650;line-height:1.3;}
.stApp h3 {font-size:18px;font-weight:600;line-height:1.4;}
[data-testid="stCaptionContainer"] p {color:var(--secondary);font-size:14px;}
[data-testid="stMainBlockContainer"] {padding-left:32px;padding-right:32px;}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:var(--border);border-radius:12px;box-shadow:none;}
.stApp button {border-radius:8px;font-weight:600;font-size:15px;box-shadow:none;}
.stApp button[kind="primary"] {background:var(--brand);border-color:var(--brand);color:light-dark(#FFFFFF,#151917);}
.stApp button[kind="primary"]:hover {background:var(--brand-hover);border-color:var(--brand-hover);}
.stApp button:focus-visible {outline:2px solid var(--brand);outline-offset:3px;}
[data-testid="stExpander"] {border-color:var(--border);border-radius:8px;background:var(--surface);}
[data-testid="stSidebar"] {background:var(--sage-light);}
.st-key-missing_facts {background:var(--warning-bg);border:1px solid var(--warning);border-radius:12px;padding:16px;}
@media(max-width:640px) {[data-testid="stMainBlockContainer"] {padding-left:16px;padding-right:16px;}}
"""


def apply():
    variables = ';'.join(f'--{key}:light-dark({light},{dark})' for key, (light, dark) in TOKENS.items())
    st.html(f'<style>.stApp {{{variables};}}{CSS}</style>')
