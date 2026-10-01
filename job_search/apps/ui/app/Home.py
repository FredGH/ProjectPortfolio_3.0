"""Streamlit entrypoint."""

from __future__ import annotations

import streamlit as st

from core.ui.theme import apply_theme

st.set_page_config(page_title="Job Search Platform", layout="wide")
apply_theme()
st.title("Job Search Platform")
st.write("Skeleton is up. Manual job entry lands in Step 2.")
