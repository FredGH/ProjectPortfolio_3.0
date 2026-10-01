"""Shared look for every Streamlit page (see
.claude/web-design/DESIGN.md): white canvas, one purple scale, 12px
rounded (never pill) buttons, whisper-level shadows. Native widget
colours come from apps/ui/.streamlit/config.toml; this module injects
the rest.

Selectors target Streamlit's `data-testid` attributes, which are far
more stable across versions than its generated class names.
"""

from __future__ import annotations

import streamlit as st

PURPLE = "#7132f5"
PURPLE_DARK = "#5741d8"
PURPLE_SUBTLE = "rgba(133, 91, 251, 0.16)"
TEXT = "#101114"
BORDER = "#dedee5"
SUCCESS_TEXT = "#026b3f"
SUBTLE_SHADOW = "rgba(0, 0, 0, 0.03) 0px 4px 24px"
MICRO_SHADOW = "rgba(16, 24, 40, 0.04) 0px 1px 4px"

# Kraken-Brand / Kraken-Product are proprietary; the design's own stated
# fallbacks are used instead (IBM Plex Sans for display, Helvetica for UI).
_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@500;600;700&display=swap');

html, body, [class*="st-"], button, input, textarea {{
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
}}

/* Display type: bold, tightly tracked. */
h1, h2, h3 {{
    font-family: "IBM Plex Sans", Helvetica, Arial, sans-serif;
    color: {TEXT};
}}
h1 {{ font-size: 36px; font-weight: 700; line-height: 1.22; letter-spacing: -0.5px; }}
h2 {{ font-size: 28px; font-weight: 700; line-height: 1.29; letter-spacing: -0.5px; }}
h3 {{ font-size: 22px; font-weight: 600; line-height: 1.2; }}

a {{ color: {PURPLE}; }}

/* Buttons: 12px radius, never a pill. */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button,
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-secondary"] {{
    border-radius: 12px;
    padding: 13px 16px;
    font-weight: 500;
    line-height: 1.38;
    transition: all 0.2s ease;
}}
[data-testid="stBaseButton-primary"] {{
    background: {PURPLE};
    border: 1px solid {PURPLE};
    color: #ffffff;
}}
[data-testid="stBaseButton-primary"]:hover {{
    background: {PURPLE_DARK};
    border-color: {PURPLE_DARK};
}}
[data-testid="stBaseButton-secondary"] {{
    background: #ffffff;
    border: 1px solid {PURPLE_DARK};
    color: {PURPLE_DARK};
}}
[data-testid="stBaseButton-secondary"]:hover {{
    background: {PURPLE_SUBTLE};
    color: {PURPLE};
}}
[data-testid="stBaseButton-primary"]:disabled,
[data-testid="stBaseButton-secondary"]:disabled {{
    opacity: 0.5;
}}

/* Cards: white, hairline border, whisper-level shadow. */
[data-testid="stExpander"] details,
[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 12px;
}}
[data-testid="stExpander"] details {{
    background: #ffffff;
    border: 1px solid {BORDER};
    box-shadow: {SUBTLE_SHADOW};
}}
div[role="dialog"] {{
    border-radius: 16px;
    box-shadow: {SUBTLE_SHADOW};
}}

/* Inputs: white fields, purple focus ring. */
[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {{
    background: #ffffff;
    border-color: {BORDER};
    border-radius: 10px;
    box-shadow: {MICRO_SHADOW};
}}
[data-baseweb="input"]:focus-within,
[data-baseweb="select"]:focus-within > div,
[data-baseweb="textarea"]:focus-within {{
    border-color: {PURPLE};
    box-shadow: 0 0 0 1px {PURPLE};
}}

/* Alerts: rounded; success reads as the design's green badge. */
[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {{
    background: rgba(20, 158, 97, 0.16);
    color: {SUCCESS_TEXT};
}}

/* Sidebar navigation: active page as a subtle purple chip. */
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: {PURPLE_SUBTLE};
    border-radius: 12px;
}}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{
    color: {PURPLE};
    font-weight: 600;
}}
"""


def apply_theme() -> None:
    """Inject the shared stylesheet. Call once per page, right after
    `st.set_page_config`; Streamlit re-runs the script on every
    interaction, so it is re-injected each time (it is idempotent).
    """
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
