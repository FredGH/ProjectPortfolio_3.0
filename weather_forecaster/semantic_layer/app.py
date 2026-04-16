"""
Weather Forecaster — dbt Semantic Layer Demo
============================================
Queries metrics defined in dbt/models/semantic/semantic_models.yml via the
MetricFlow CLI (`mf`).  No SQL is written in this app — callers reference
metrics and dimensions by name; MetricFlow generates and runs the SQL.

Run from the project root:
    cd weather_forecaster/
    streamlit run semantic_layer/app.py
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DBT_DIR = ROOT / "dbt"
DUCKDB_PATH = ROOT / "data" / "etl" / "weather_forecaster.duckdb"
# Snapshot directory: same filename as the main DB so dbt-duckdb derives the
# same catalog name ("weather_forecaster") regardless of which copy is used.
DUCKDB_SNAPSHOT = ROOT / "data" / "etl" / "sl_snapshot" / "weather_forecaster.duckdb"


# ── DuckDB lock detection & snapshot ─────────────────────────────────────────

def _is_locked(path: Path) -> bool:
    """Return True if the DuckDB file cannot be opened read-only."""
    if not path.exists():
        return False
    try:
        conn = duckdb.connect(str(path), read_only=True)
        conn.close()
        return False
    except Exception as exc:
        return "lock" in str(exc).lower() or "conflicting" in str(exc).lower()


def _take_snapshot(src: Path, dst: Path) -> datetime | None:
    """
    Copy the DuckDB file (and its WAL if present) to *dst*.
    The destination directory is created if it does not exist.
    Returns the snapshot timestamp on success, None on failure.
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        wal = Path(str(src) + ".wal")
        if wal.exists():
            shutil.copy2(str(wal), str(dst) + ".wal")
        return datetime.now()
    except Exception:
        return None


def _resolve_db_path() -> tuple[Path, bool, datetime | None]:
    """
    Return (db_path, snapshot_mode, snapshot_taken_at).

    If the main file is locked, falls back to a snapshot copy so MetricFlow
    can query without competing for the write lock (e.g. the dbt VS Code LSP
    holds it while the editor is open).
    """
    if not _is_locked(DUCKDB_PATH):
        return DUCKDB_PATH, False, None
    ts = _take_snapshot(DUCKDB_PATH, DUCKDB_SNAPSHOT)
    if ts is not None:
        return DUCKDB_SNAPSHOT, True, ts
    # Snapshot failed too — return main path and let mf surface the error
    return DUCKDB_PATH, False, None


# Resolve once per Streamlit run (cleared on full page reload)
if "db_path" not in st.session_state:
    _db_path, _snapshot_mode, _snapshot_ts = _resolve_db_path()
    st.session_state["db_path"] = _db_path
    st.session_state["snapshot_mode"] = _snapshot_mode
    st.session_state["snapshot_ts"] = _snapshot_ts

DB_PATH: Path = st.session_state["db_path"]

ENV = {
    **os.environ,
    "DBT_DUCKDB_PATH": str(DB_PATH),
    # Read-only dbt target — opens DuckDB with a shared lock where possible.
    "DBT_TARGET": "metricflow",
}

# ── MetricFlow CLI helpers ────────────────────────────────────────────────────

def _mf(*args: str, timeout: int = 120) -> tuple[str, str, int]:
    """Run a MetricFlow CLI command from the dbt project directory."""
    result = subprocess.run(
        ["mf", *args],
        capture_output=True,
        text=True,
        cwd=str(DBT_DIR),
        env=ENV,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


def _parse_mf_table(output: str) -> pd.DataFrame:
    """
    Parse MetricFlow's plain-text table output into a DataFrame.

    MetricFlow 0.11 outputs a fixed-width text table, not markdown pipes:

        city__city_name      avg_current_temperature
        -----------------  -------------------------
        Cazeau                                 25.09
        Nacional                               29.64

    Strategy: locate the dash-separator row, use the line above it as the
    header, feed header + data rows to pandas read_fwf which handles
    fixed-width columns automatically.
    """
    lines = output.splitlines()

    # Find the separator line: only dashes and spaces, contains at least one dash
    sep_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip() and re.fullmatch(r"[-\s]+", ln.strip())),
        None,
    )
    if sep_idx is None or sep_idx == 0:
        return pd.DataFrame()

    header_line = lines[sep_idx - 1]
    data_lines = [ln for ln in lines[sep_idx + 1:] if ln.strip()]
    if not data_lines:
        return pd.DataFrame()

    table_text = "\n".join([header_line] + data_lines)
    try:
        df = pd.read_fwf(io.StringIO(table_text))
        df = df.dropna(how="all")
        for col in df.columns:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
        return df
    except Exception:
        return pd.DataFrame()


def _bullet_lines(text: str) -> list[str]:
    """
    Extract the content of bullet lines from MetricFlow output.

    MetricFlow prints results as:
        • metric_name: dim1, dim2, …
        • dimension_name

    Returns each line with the leading '• ' stripped.
    """
    result = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped.startswith("•"):
            result.append(stripped.lstrip("•").strip())
    return result


# ── Semantic model description loader ────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _metric_descriptions() -> dict[str, str]:
    """
    Parse descriptions directly from semantic_models.yml.

    `mf list metrics` does not return descriptions, so we read the YAML
    source of truth instead. Returns {metric_name: description}.
    """
    import yaml  # bundled with dbt-core

    sm_path = DBT_DIR / "models" / "semantic" / "semantic_models.yml"
    if not sm_path.exists():
        return {}
    try:
        data = yaml.safe_load(sm_path.read_text())
        return {
            m["name"]: m.get("description", "")
            for m in data.get("metrics", [])
        }
    except Exception:
        return {}


# ── Cached data-fetching functions ────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_metrics() -> list[dict[str, str]]:
    """Return all available metrics as {name, description} dicts.

    Names come from `mf list metrics`; descriptions are loaded from
    semantic_models.yml because the CLI does not expose them.
    """
    stdout, stderr, rc = _mf("list", "metrics")
    if rc != 0:
        return []

    descriptions = _metric_descriptions()
    metrics: list[dict[str, str]] = []
    for line in _bullet_lines(stdout):
        name = line.split(":")[0].strip()
        if name:
            metrics.append({"name": name, "description": descriptions.get(name, "")})
    return metrics


@st.cache_data(ttl=300, show_spinner=False)
def fetch_dimensions(metric: str) -> list[str]:
    """Return dimensions available for a given metric.

    `mf list dimensions --metrics X` output format:
        • city__city_name
        • city__country_code
        • metric_time
    """
    stdout, stderr, rc = _mf("list", "dimensions", "--metrics", metric)
    if rc != 0:
        return []
    return _bullet_lines(stdout)


_LOCK_MARKERS = ("Could not set lock", "Conflicting lock", "IO Error")


def _is_lock_error(text: str) -> bool:
    return any(m in text for m in _LOCK_MARKERS)


def run_query(
    metric: str,
    group_by: list[str],
    limit: int,
) -> tuple[pd.DataFrame, str, str]:
    """Execute `mf query` and return (DataFrame, raw_output, error_kind).

    error_kind is one of:
      ""           — success
      "LOCK_ERROR" — DuckDB file is locked by another process
      "ERROR"      — any other failure
    """
    args = ["query", "--metrics", metric]
    if group_by:
        args += ["--group-by", ",".join(group_by)]
    args += ["--limit", str(limit)]

    stdout, stderr, rc = _mf(*args)

    if rc != 0:
        combined = stderr + stdout
        kind = "LOCK_ERROR" if _is_lock_error(combined) else "ERROR"
        return pd.DataFrame(), combined, kind

    df = _parse_mf_table(stdout)

    # Try to extract the generated SQL (shown after "SQL:" in some mf versions)
    sql = ""
    in_sql_block = False
    sql_lines: list[str] = []
    for ln in stdout.splitlines():
        if ln.strip().upper().startswith("SQL"):
            in_sql_block = True
            continue
        if in_sql_block:
            sql_lines.append(ln)
    if sql_lines:
        sql = "\n".join(sql_lines).strip()

    return df, stdout, sql


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Weather Semantic Layer",
    page_icon="🌤",
    layout="wide",
)

st.title("🌤 Weather Forecaster — dbt Semantic Layer Demo")
st.markdown(
    "Query **weather metrics** defined in the dbt Semantic Layer. "
    "Pick a metric and dimensions — MetricFlow generates the SQL and executes it against DuckDB."
)

# ── MetricFlow availability check ─────────────────────────────────────────────
with st.spinner("Checking MetricFlow..."):
    ver_out, ver_err, ver_rc = _mf("--version")

if ver_rc != 0:
    st.error(
        "MetricFlow (`mf`) not found on PATH.\n\n"
        "Install it with:\n```bash\npip install dbt-metricflow[duckdb]\n```"
    )
    st.stop()

st.caption(f"**MetricFlow:** {ver_out.strip() or 'available'} &nbsp;|&nbsp; "
           f"**DuckDB:** `{DB_PATH.name}`")

if not DUCKDB_PATH.exists():
    st.warning(
        f"DuckDB file not found at `{DUCKDB_PATH}`. "
        "Run the pipeline first: `python weather_forecaster_sources/pipeline_runner.py`"
    )
elif st.session_state.get("snapshot_mode"):
    ts = st.session_state["snapshot_ts"]
    ts_str = ts.strftime("%H:%M:%S") if ts else "unknown"
    c1, c2 = st.columns([5, 1])
    c1.info(
        f"**Snapshot mode** — the main database is locked (dbt VS Code extension is running). "
        f"Querying a read-only copy taken at **{ts_str}**. "
        f"Data reflects the state at that time."
    )
    if c2.button("Refresh snapshot"):
        ts = _take_snapshot(DUCKDB_PATH, DUCKDB_SNAPSHOT)
        st.session_state["snapshot_ts"] = ts
        st.cache_data.clear()
        st.session_state.pop("last_result", None)
        st.rerun()

# ── Load metrics list ─────────────────────────────────────────────────────────
with st.spinner("Loading metrics..."):
    all_metrics = fetch_metrics()

if not all_metrics:
    st.error(
        "No metrics found. Make sure `dbt parse` succeeds and "
        "`dbt/models/semantic/semantic_models.yml` exists."
    )
    st.stop()

metric_names = [m["name"] for m in all_metrics]

# ── Sidebar — Query Builder ───────────────────────────────────────────────────
with st.sidebar:
    st.header("Query Builder")

    selected_metric = st.selectbox("Metric", metric_names)

    meta = next((m for m in all_metrics if m["name"] == selected_metric), {})
    if meta.get("description"):
        st.caption(meta["description"])

    st.divider()

    with st.spinner("Loading dimensions..."):
        available_dims = fetch_dimensions(selected_metric)

    # Sensible defaults: first categorical dimension (skip metric_time)
    default_dims = [d for d in available_dims if "metric_time" not in d][:1]
    selected_dims = st.multiselect(
        "Group by (dimensions)",
        available_dims,
        default=default_dims,
        help="Choose one or more dimensions to slice the metric.",
    )

    limit = st.slider("Row limit", min_value=10, max_value=500, value=50, step=10)

    st.divider()
    run_btn = st.button("Run Query", type="primary", use_container_width=True)

# ── Main area — Results ───────────────────────────────────────────────────────
if run_btn or "last_result" not in st.session_state:
    with st.spinner("Querying semantic layer..."):
        df, raw_out, gen_sql = run_query(selected_metric, selected_dims, limit)
    st.session_state["last_result"] = (df, raw_out, gen_sql, selected_metric, selected_dims)

df, raw_out, gen_sql, queried_metric, queried_dims = st.session_state["last_result"]

if df.empty and gen_sql == "LOCK_ERROR":
    st.error(
        "**DuckDB is still locked — snapshot query also failed.**\n\n"
        "The snapshot copy could not be opened either. "
        "Try stopping all dbt/Dagster processes, then reload the page.\n\n"
        "```bash\n"
        "# Find the locking process\n"
        "lsof data/etl/weather_forecaster.duckdb\n\n"
        "# Kill it (replace <PID> with the value from lsof)\n"
        "kill <PID>\n"
        "```"
    )
    if st.button("🔄 Retry", type="primary"):
        # Re-resolve DB path in case lock was released
        _db_path, _snap, _ts = _resolve_db_path()
        st.session_state["db_path"] = _db_path
        st.session_state["snapshot_mode"] = _snap
        st.session_state["snapshot_ts"] = _ts
        del st.session_state["last_result"]
        st.rerun()
elif df.empty and gen_sql == "ERROR":
    st.error("Query failed.")
    with st.expander("Error details"):
        st.code(raw_out, language="text")
elif df.empty:
    st.info("No results. Try different dimensions or check that the gold tables are populated.")
else:
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = df.select_dtypes(exclude="number").columns.tolist()

    # ── KPI row ──────────────────────────────────────────────────────────────
    if numeric_cols:
        metric_col = numeric_cols[0]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Rows", len(df))
        k2.metric(f"Avg {metric_col}", f"{df[metric_col].mean():.2f}")
        k3.metric(f"Max {metric_col}", f"{df[metric_col].max():.2f}")
        k4.metric(f"Min {metric_col}", f"{df[metric_col].min():.2f}")

    st.divider()

    # ── Chart + Table ─────────────────────────────────────────────────────────
    col_chart, col_table = st.columns([3, 2])

    with col_table:
        st.subheader("Results")
        st.dataframe(df, use_container_width=True, hide_index=True)

    with col_chart:
        st.subheader("Chart")
        if numeric_cols and categorical_cols:
            x_col = categorical_cols[0]
            y_col = numeric_cols[0]

            sorted_df = df.sort_values(y_col, ascending=False)

            # Time-series if the x axis looks like a date
            if any(kw in x_col.lower() for kw in ("date", "month", "time", "year")):
                fig = px.line(
                    df.sort_values(x_col),
                    x=x_col,
                    y=y_col,
                    markers=True,
                    title=f"{queried_metric}",
                    color=categorical_cols[1] if len(categorical_cols) > 1 else None,
                )
            elif len(df) > 30:
                fig = px.histogram(
                    df,
                    x=y_col,
                    title=f"Distribution of {queried_metric}",
                    nbins=20,
                )
            else:
                fig = px.bar(
                    sorted_df,
                    x=x_col,
                    y=y_col,
                    title=f"{queried_metric}",
                    color=y_col,
                    color_continuous_scale="Blues",
                    text_auto=".1f",
                )
                fig.update_layout(showlegend=False)

            fig.update_layout(margin={"t": 40, "b": 20, "l": 10, "r": 10})
            st.plotly_chart(fig, use_container_width=True)

        elif numeric_cols:
            st.info("Add a categorical dimension to visualise the data as a chart.")

    # ── Debug / educational expanders ─────────────────────────────────────────
    with st.expander("Raw MetricFlow output"):
        st.code(raw_out, language="text")

    if gen_sql and gen_sql not in ("LOCK_ERROR", "ERROR"):
        with st.expander("Generated SQL"):
            st.code(gen_sql, language="sql")

# ── Metrics catalogue ─────────────────────────────────────────────────────────
with st.expander("📖 All available metrics"):
    catalogue = pd.DataFrame(all_metrics).rename(
        columns={"name": "Metric", "description": "Description"}
    )
    st.dataframe(catalogue, use_container_width=True, hide_index=True)

# ── How it works ──────────────────────────────────────────────────────────────
with st.expander("⚙ How the Semantic Layer works"):
    st.markdown(
        """
        ### dbt Semantic Layer — local demo stack

        ```
        dbt/models/semantic/semantic_models.yml   ← metric + dimension definitions
                    ↓
        MetricFlow (mf CLI)                   ← translates metric queries → SQL
                    ↓
        DuckDB (data/etl/weather_forecaster.duckdb)  ← executes the SQL
                    ↓
        Streamlit (this app)                  ← renders results
        ```

        **Key idea:** this app never writes SQL. It calls:
        ```bash
        mf query --metrics avg_current_temperature --group-by city_name --limit 50
        ```
        MetricFlow reads `semantic_models.yml`, resolves the metric to a measure,
        joins the right tables, and returns the aggregated result.

        **Semantic model excerpt** (`dbt/models/semantic/semantic_models.yml`):
        ```yaml
        metrics:
          - name: avg_current_temperature
            description: "Average current air temperature (°C) across selected cities."
            type: simple
            type_params:
              measure: weather_summary__current_temp_c
        ```
        """
    )
