"""Shared look for every Streamlit page (see
.claude/web-design/DESIGN.md): near-black canvas, one electric-yellow
brand colour, Inter at weight 700 for headlines, 8px buttons and 12px
cards, hairline borders instead of shadows. Native widget colours come
from apps/ui/.streamlit/config.toml; this module injects the rest.

Selectors target Streamlit's `data-testid` attributes, which are far
more stable across versions than its generated class names.
"""

from __future__ import annotations

import streamlit as st

YELLOW = "#faff69"
YELLOW_ACTIVE = "#e6eb52"
YELLOW_DISABLED = "#3a3a1f"
ON_YELLOW = "#0a0a0a"
CANVAS = "#0a0a0a"
SURFACE_SOFT = "#121212"
SURFACE_CARD = "#1a1a1a"
SURFACE_ELEVATED = "#242424"
HAIRLINE = "#2a2a2a"
HAIRLINE_STRONG = "#3a3a3a"
BODY = "#cccccc"
MUTED = "#888888"

_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400&display=swap');

html, body, [class*="st-"], button, input, textarea {{
    font-family: Inter, sans-serif;
}}

/* Headlines: Inter 700, tight negative tracking. Hierarchy by size. */
h1, h2, h3 {{ color: #ffffff; font-weight: 700; }}
h1 {{ font-size: 40px; line-height: 1.15; letter-spacing: -1.5px; }}
h2 {{ font-size: 32px; line-height: 1.2; letter-spacing: -1px; }}
h3 {{ font-size: 24px; line-height: 1.3; letter-spacing: -0.3px; }}
h4, h5, h6 {{ font-weight: 600; }}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {{ color: {BODY}; line-height: 1.55; }}
[data-testid="stCaptionContainer"] {{ color: {MUTED}; }}
/* st.text keeps its line breaks but must wrap, or a long line (a CV
   summary, a bullet) runs off the right edge of the page. */
[data-testid="stText"] {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}}
a {{ color: {YELLOW}; text-decoration: underline; }}

/* Stat numbers: large, yellow, bold. */
[data-testid="stMetricValue"] {{
    color: {YELLOW};
    font-weight: 700;
    letter-spacing: -1.5px;
}}

/* Buttons: 8px radius. Primary is yellow with black text. */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button,
[data-testid="stBaseButton-primary"],
[data-testid="stBaseButton-secondary"] {{
    border-radius: 8px;
    padding: 12px 20px;
    font-size: 14px;
    font-weight: 600;
    line-height: 1;
}}
[data-testid="stBaseButton-primary"] {{
    background: {YELLOW};
    border: 1px solid {YELLOW};
    color: {ON_YELLOW};
}}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primary"]:active {{
    background: {YELLOW_ACTIVE};
    border-color: {YELLOW_ACTIVE};
    color: {ON_YELLOW};
}}
[data-testid="stBaseButton-secondary"] {{
    background: {SURFACE_CARD};
    border: 1px solid {SURFACE_CARD};
    color: #ffffff;
}}
[data-testid="stBaseButton-primary"]:disabled {{
    background: {YELLOW_DISABLED};
    border-color: {YELLOW_DISABLED};
    color: {MUTED};
}}
[data-testid="stBaseButton-secondary"]:disabled {{
    opacity: 0.5;
}}

/* Cards: surface-card fill, 1px hairline, 12px radius, no shadow. */
[data-testid="stExpander"] details,
[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 12px;
}}
[data-testid="stExpander"] details {{
    background: {SURFACE_CARD};
    border: 1px solid {HAIRLINE};
}}
div[role="dialog"] {{
    background: {SURFACE_CARD};
    border: 1px solid {HAIRLINE};
    border-radius: 12px;
}}

/* Inputs: surface-card fill, 8px radius, yellow focus border. */
[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {{
    background: {SURFACE_CARD};
    border-color: {HAIRLINE_STRONG};
    border-radius: 8px;
}}
[data-baseweb="input"]:focus-within,
[data-baseweb="select"]:focus-within > div,
[data-baseweb="textarea"]:focus-within {{
    border-color: {YELLOW};
    box-shadow: 0 0 0 1px {YELLOW};
}}

/* Code: JetBrains Mono in a surface-card window. */
code, pre, [data-testid="stCode"] {{
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 14px;
}}
[data-testid="stCode"] pre {{ background: {SURFACE_CARD}; border-radius: 12px; }}

/* Alerts: 8px radius; status colours come from the theme's semantics. */
[data-testid="stAlert"] {{ border-radius: 8px; }}

/* Sidebar: soft surface with a hairline edge; active page like the
   design's active category tab. */
[data-testid="stSidebar"] {{
    background: {SURFACE_SOFT};
    border-right: 1px solid {HAIRLINE};
}}
[data-testid="stSidebarNav"] a {{ border-radius: 8px; color: {MUTED}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: {SURFACE_CARD};
    color: #ffffff;
}}
"""


def apply_theme() -> None:
    """Inject the shared stylesheet. Call once per page, right after
    `st.set_page_config`; Streamlit re-runs the script on every
    interaction, so it is re-injected each time (it is idempotent).
    """
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
