"""Shared look for every Streamlit page (see
.claude/web-design/DESIGN.md): warm-cream canvas, two greens, pill
buttons, soft cards, Inter type. Native widget colours come from
apps/ui/.streamlit/config.toml; this module injects the rest.

Selectors target Streamlit's `data-testid` attributes, which are far
more stable across versions than its generated class names.
"""

from __future__ import annotations

import streamlit as st

# Two-tier green: the brighter ACCENT is for actions, the deeper BRAND
# for headings, and HOUSE for dark surfaces (the sidebar).
GREEN_ACCENT = "#00754A"
GREEN_BRAND = "#006241"
GREEN_HOUSE = "#1E3932"

_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, [class*="st-"], button, input, textarea {{
    font-family: Inter, "Helvetica Neue", Helvetica, Arial, sans-serif;
    letter-spacing: -0.01em;
}}

/* Headings: hierarchy from weight and colour, not size jumps. */
h1, h2, h3 {{ color: {GREEN_BRAND}; font-weight: 600; letter-spacing: -0.16px; }}
h4, h5, h6 {{ font-weight: 600; }}

/* Buttons: full pill, green, shrink slightly while pressed. */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button,
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-secondary"] {{
    border-radius: 50px;
    padding: 7px 16px;
    font-weight: 600;
    transition: all 0.2s ease;
}}
[data-testid="stBaseButton-primary"] {{
    background: {GREEN_ACCENT};
    border: 1px solid {GREEN_ACCENT};
    color: #ffffff;
}}
[data-testid="stBaseButton-secondary"] {{
    background: transparent;
    border: 1px solid {GREEN_ACCENT};
    color: {GREEN_ACCENT};
}}
[data-testid="stBaseButton-primary"]:active,
[data-testid="stBaseButton-secondary"]:active {{
    transform: scale(0.95);
}}
[data-testid="stBaseButton-primary"]:disabled,
[data-testid="stBaseButton-secondary"]:disabled {{
    opacity: 0.5;
    transform: none;
}}

/* Cards: white, 12px radius, two stacked whisper shadows. */
[data-testid="stExpander"] details,
[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 12px;
}}
[data-testid="stExpander"] details {{
    background: #ffffff;
    border: none;
    box-shadow: 0 0 0.5px rgba(0, 0, 0, 0.14), 0 1px 1px rgba(0, 0, 0, 0.24);
}}
div[role="dialog"] {{
    border-radius: 12px;
}}

/* Inputs: white fields, green focus ring. */
[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {{
    background: #ffffff;
    border-radius: 8px;
}}
[data-baseweb="input"]:focus-within,
[data-baseweb="select"]:focus-within > div,
[data-baseweb="textarea"]:focus-within {{
    border-color: {GREEN_ACCENT};
    box-shadow: 0 0 0 1px {GREEN_ACCENT};
}}

/* Alerts: rounded. */
[data-testid="stAlert"] {{ border-radius: 12px; }}

/* Sidebar: House Green band with white text. */
[data-testid="stSidebar"] {{ background: {GREEN_HOUSE}; }}
[data-testid="stSidebar"] *,
[data-testid="stSidebarNav"] a span {{ color: #ffffff; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: rgba(255, 255, 255, 0.12);
    border-radius: 12px;
}}
[data-testid="stSidebarNav"] a:hover {{
    background: rgba(255, 255, 255, 0.08);
    border-radius: 12px;
}}
"""


def apply_theme() -> None:
    """Inject the shared stylesheet. Call once per page, right after
    `st.set_page_config`; Streamlit re-runs the script on every
    interaction, so it is re-injected each time (it is idempotent).
    """
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
