"""Offer Check - where an offer sits in the market, honestly (PLAN.md Step
21c). Never a bare percentile: the number of postings that disclosed pay is
always shown, and a slice that is too thin gets a warning instead of a number."""

from __future__ import annotations

import httpx
import streamlit as st

from core.settings import get_settings
from core.ui.theme import apply_theme

st.set_page_config(page_title="Offer Check", layout="wide")
apply_theme()
st.title("Offer Check")

with st.expander("User manual"):
    st.markdown(
        """
Enter an offer and **Check** where it sits among the postings we have collected
that state pay. You always see **how many postings disclosed a figure** and the
**disclosure rate**: "72nd percentile" against 9 postings is not a finding, so
when the slice is too thin you get a warning and **no percentile**. The check
tries the narrowest slice first (category, region, seniority, IR35) and widens
until one has enough data; every step is listed.

For a contract offer with a known IR35 status you also get an **estimated
take-home**. It is an approximation from editable UK tax rates, not tax advice,
and the rates in `config/uk_tax.yml` are marked unverified: check them. **Compare
two offers** puts a day rate inside IR35 next to one outside it.

Checking saves nothing. **Save** records the offer with the benchmark it got.
"""
    )

_base = get_settings().api_base_url
_MD_META = set("\\`*_{}[]()#+-.!|<>~$:&")
_SENIORITY = ["unknown", "junior", "mid", "senior", "lead", "principal"]


def _plain(text: object) -> str:
    """Escape text so Streamlit renders it literally, never as markdown.

    Args:
        text: Untrusted text (anything `str()`-able).

    Returns:
        The text with every markdown metacharacter backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MD_META else ch for ch in str(text))


def _call(method: str, path: str, **kwargs: object) -> httpx.Response:
    """Call the API.

    Args:
        method: `GET` or `POST`.
        path: The path, starting with `/`.
        **kwargs: Extra httpx arguments.

    Returns:
        The response.
    """
    return httpx.request(method, f"{_base}{path}", timeout=30.0, **kwargs)  # type: ignore[arg-type]


def _detail(response: httpx.Response) -> str:
    """Read an API error message.

    Args:
        response: A non-2xx response.

    Returns:
        The `detail` field, or the status code when there is none.
    """
    try:
        detail = response.json().get("detail", response.status_code)
    except (ValueError, AttributeError):
        return str(response.status_code)
    return (
        str(detail) if not isinstance(detail, list) else "check the values you entered"
    )


def _ordinal(n: float) -> str:
    """Format a percentile as an ordinal.

    Args:
        n: A percentile 0-100.

    Returns:
        e.g. `62nd`.
    """
    whole = round(n)
    suffix = (
        "th"
        if 10 <= whole % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(whole % 10, "th")
    )
    return f"{whole}{suffix}"


def _money(value: float | None, unit: str = "") -> str:
    """Format an amount in pounds.

    Args:
        value: The amount, or None.
        unit: An optional suffix such as `/day`.

    Returns:
        e.g. `GBP 525/day`, or an empty string for None.
    """
    return "" if value is None else f"GBP {value:,.0f}{unit}"


def _categories() -> list[str]:
    """List the categories the market data knows.

    Returns:
        Category names, or a sensible default when the market API is down.
    """
    try:
        response = _call("GET", "/market/overview")
        if response.status_code == 200:
            return list(response.json().get("categories") or [])
    except httpx.HTTPError:
        pass
    return ["data_engineer", "software_engineer", "data_scientist", "ai_ml_engineer"]


st.subheader("Your offer")
left, middle, right = st.columns(3)
engagement = left.selectbox("Type", ["contract", "permanent"], key="oc-engagement")
bases = ["daily", "hourly"] if engagement == "contract" else ["annual"]
basis = middle.selectbox("Rate is", bases, key=f"oc-basis-{engagement}")
amount = right.number_input(
    "Amount (GBP)", min_value=0.0, value=0.0, step=10.0, key="oc-amount"
)
col_a, col_b, col_c, col_d = st.columns(4)
ir35 = (
    col_a.selectbox("IR35 status", ["undetermined", "outside", "inside"], key="oc-ir35")
    if engagement == "contract"
    else "undetermined"
)
categories = _categories()
category = col_b.selectbox(
    "Category", categories, format_func=lambda c: c.replace("_", " "), key="oc-category"
)
region = col_c.text_input(
    "Region",
    value="unknown",
    key="oc-region",
    help="A region code such as UKI; unknown skips the region filter.",
)
seniority = col_d.selectbox("Seniority", _SENIORITY, key="oc-seniority")
payload = {
    "amount": amount,
    "basis": basis,
    "engagement_type": engagement,
    "ir35_status": ir35,
    "category": category,
    "region": region.strip() or "unknown",
    "seniority_band": seniority,
}

if st.button("Check this offer", key="oc-check", disabled=amount <= 0):
    try:
        answer = _call("POST", "/offers/benchmark", json=payload)
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to check: {exc}"))
    else:
        if answer.status_code != 200:
            st.error(_plain(_detail(answer)))
        else:
            st.session_state["oc-result"] = answer.json()
            st.session_state["oc-payload"] = payload

result = st.session_state.get("oc-result")
if result:
    bench, net = result["benchmark"], result["net"]
    unit = "/day" if bench["unit"] == "GBP per day" else "/year"
    st.subheader("Where it sits")
    for warning in bench["warnings"]:
        (st.warning if warning.startswith("Too thin") else st.info)(_plain(warning))
    sample = (
        f"{bench['n_disclosed']} postings stating pay out of {bench['n_postings']} "
        f"in this slice ({bench['disclosure_rate']:.0%} disclose)"
    )
    if bench["status"] == "ok":
        first, second, third, fourth = st.columns(4)
        first.metric("Percentile", _ordinal(bench["percentile"]))
        second.metric("25th", _money(bench["p25"], unit))
        third.metric("Median", _money(bench["median"], unit))
        fourth.metric("75th", _money(bench["p75"], unit))
        st.caption(f"Your offer: {_money(bench['value'], unit)} - based on {sample}.")
        st.caption(f"Slice: {_plain(bench['slice_label'])}")
    else:
        st.metric("Percentile", "not given")
        st.caption(
            f"Only {sample}, in: {_plain(bench['slice_label'])}. Treat any "
            "comparison below as anecdotes, not a finding."
        )
    st.markdown(f"**Direction over six months:** {_plain(bench['direction'])}")
    st.caption(_plain(bench["trend_note"]))
    with st.expander("Slices tried, narrowest first"):
        st.dataframe(
            [
                {
                    "Slice": s["label"],
                    "Postings": s["n_postings"],
                    "Disclosed": s["n_disclosed"],
                    "Disclosure rate": f"{s['disclosure_rate']:.0%}",
                }
                for s in bench["ladder"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    if bench["comparables"]:
        st.markdown("**Comparable postings that state pay**")
        st.dataframe(
            [
                {
                    "Role": c["title"],
                    "Company": c["company"],
                    "Pay": _money(c["figure"], unit),
                    "Region": c["region"],
                    "Seniority": c["seniority"],
                }
                for c in bench["comparables"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    st.subheader("Estimated take-home")
    if net:
        first, second, third = st.columns(3)
        first.metric(
            f"Take-home ({net['status']} IR35)", _money(net["net_year"], "/year")
        )
        second.metric("Per billable day", _money(net["net_day"], "/day"))
        third.metric("Invoiced", _money(net["gross_year"], "/year"))
        st.dataframe(
            [
                {"Deduction": s["label"], "GBP / year": round(s["amount"])}
                for s in net["steps"]
            ],
            hide_index=True,
            use_container_width=True,
        )
        st.caption("Assumptions: " + "; ".join(_plain(a) for a in net["assumptions"]))
        st.caption(
            f"Tax year {_plain(net['tax_year'])}. "
            + (
                "Rates verified."
                if net["verified"]
                else "The rates are NOT verified: check them."
            )
            + " An estimate, not tax advice."
        )
    else:
        st.info(_plain(result["net_note"]))
    st.markdown("**Save this offer**")
    save_cols = st.columns(3)
    company = save_cols[0].text_input("Company", key="oc-company")
    title = save_cols[1].text_input("Role", key="oc-title")
    notes = save_cols[2].text_input("Notes", key="oc-notes")
    stale = st.session_state.get("oc-payload") != payload
    if stale:
        st.info(
            "The offer above changed since it was checked: check it again before "
            "saving."
        )
    if st.button("Save this offer", key="oc-save", disabled=stale):
        try:
            saved = _call(
                "POST",
                "/offers",
                json={
                    **st.session_state["oc-payload"],
                    "company": company or None,
                    "title": title or None,
                    "notes": notes or None,
                },
            )
        except httpx.HTTPError as exc:
            st.error(_plain(f"Failed to save: {exc}"))
        else:
            if saved.status_code != 200:
                st.error(_plain(_detail(saved)))
            else:
                st.success("Saved.")

st.divider()
st.subheader("Compare two offers")
st.caption("Put a day rate inside IR35 next to one outside it, on estimated take-home.")
c1, c2, c3, c4 = st.columns(4)
rate_a = c1.number_input(
    "Offer A day rate", min_value=0.0, value=0.0, step=10.0, key="oc-rate-a"
)
status_a = c2.selectbox("A is", ["inside", "outside"], key="oc-status-a")
rate_b = c3.number_input(
    "Offer B day rate", min_value=0.0, value=0.0, step=10.0, key="oc-rate-b"
)
status_b = c4.selectbox("B is", ["outside", "inside"], key="oc-status-b")
if st.button(
    "Compare take-home", key="oc-compare", disabled=rate_a <= 0 or rate_b <= 0
):
    try:
        compared = _call(
            "POST",
            "/offers/net-compare",
            json={
                "first": {"day_rate": rate_a, "ir35_status": status_a},
                "second": {"day_rate": rate_b, "ir35_status": status_b},
            },
        )
    except httpx.HTTPError as exc:
        st.error(_plain(f"Failed to compare: {exc}"))
        st.stop()
    if compared.status_code != 200:
        st.error(_plain(_detail(compared)))
    else:
        both = compared.json()
        x, y, z = st.columns(3)
        x.metric(f"A ({status_a})", _money(both["first"]["net_year"], "/year"))
        y.metric(f"B ({status_b})", _money(both["second"]["net_year"], "/year"))
        if abs(both["difference_year"]) < 1:
            z.metric("Same take-home", "GBP 0/year")
        else:
            better = "B" if both["difference_year"] > 0 else "A"
            z.metric(
                f"{better} takes home more",
                _money(abs(both["difference_year"]), "/year"),
                f"{_money(abs(both['difference_day']), '/day')}",
            )
        for label, side in (("A", both["first"]), ("B", both["second"])):
            st.caption(
                f"Offer {label} ({side['status']} IR35) assumes: "
                + "; ".join(_plain(a) for a in side["assumptions"])
            )
        st.caption(
            f"Tax year {_plain(both['first']['tax_year'])}; "
            + (
                "verified."
                if both["first"]["verified"]
                else "rates NOT verified: check them."
            )
            + " An estimate, not tax advice."
        )

st.divider()
st.subheader("Offers you recorded")
try:
    recorded = _call("GET", "/offers")
except httpx.HTTPError as exc:
    st.error(_plain(f"Failed to load your offers: {exc}"))
else:
    if recorded.status_code != 200:
        st.error(_plain(_detail(recorded)))
        st.stop()
    rows = recorded.json()
    if rows:
        st.dataframe(
            [
                {
                    "Received": o["received_on"],
                    "Company": o["company"],
                    "Role": o["title"],
                    "Amount": f"{o['amount']:,.0f} ({o['basis']})",
                    "IR35": o["ir35_status"],
                    "Percentile": (
                        _ordinal(o["benchmark"]["percentile"])
                        if o["benchmark"]
                        and o["benchmark"].get("percentile") is not None
                        else "not given (thin)"
                    ),
                    "Disclosed (n)": (
                        o["benchmark"]["n_disclosed"] if o["benchmark"] else None
                    ),
                    "Postings (n)": ((o["benchmark"] or {}).get("n_postings")),
                    "Disclosure rate": (
                        f"{o['benchmark']['disclosure_rate']:.0%}"
                        if (o["benchmark"] or {}).get("disclosure_rate") is not None
                        else ""
                    ),
                    "Slice": (o["benchmark"] or {}).get("slice_label"),
                }
                for o in rows
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No offers recorded yet.")
