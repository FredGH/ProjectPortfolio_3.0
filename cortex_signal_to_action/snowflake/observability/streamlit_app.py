"""
CSTA Observability Dashboard — Streamlit in Snowflake (SiS)
6 pages: Pipeline Overview, Model Health, Cortex Usage & Cost,
         Data Quality, Lineage & Coverage, Cost & Credits.

Deploy steps (run from a SnowSQL session or Snowflake CLI):
  PUT file://snowflake/observability/streamlit_app.py
      @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/streamlit/
      AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
  -- Then run 07_observability_streamlit.sql to CREATE OR REPLACE STREAMLIT.
"""

from __future__ import annotations

import json
import altair as alt
import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

session = get_active_session()

OBS = "CSTA_MARKETING_SHARED.OBSERVABILITY"
SHARED_DB = "CSTA_MARKETING_SHARED"
ARTIFACTS_STAGE = f"@{SHARED_DB}.ARTIFACTS.CSTA_DBT_ARTIFACTS"

st.set_page_config(
    page_title="CSTA Observability",
    page_icon="🔭",
    layout="wide",
)

st.sidebar.title("CSTA Observability")
page = st.sidebar.radio(
    "Page",
    [
        "1. Pipeline Overview",
        "2. Model Health",
        "3. Cortex Usage & Cost",
        "4. Data Quality",
        "5. Lineage & Coverage",
        "6. Cost & Credits",
    ],
)

env_options = ["all", "dev", "uat", "prod"]
selected_env = st.sidebar.selectbox("Environment", env_options, index=0)
env_filter = "" if selected_env == "all" else f"AND env = '{selected_env}'"

lookback_days = st.sidebar.slider("Lookback (days)", min_value=7, max_value=90, value=30)

# ---------------------------------------------------------------------------
# Page 1: Pipeline Overview
# ---------------------------------------------------------------------------
if page == "1. Pipeline Overview":
    st.title("Pipeline Overview")

    runs_df = session.sql(f"""
        SELECT
            run_id,
            env,
            command,
            status,
            started_at,
            finished_at,
            ROUND(duration_seconds / 60.0, 1)  AS duration_min,
            models_run,
            tests_run,
            models_failed,
            tests_failed
        FROM {OBS}.PIPELINE_RUN_LOG
        WHERE started_at >= DATEADD('day', -{lookback_days}, CURRENT_TIMESTAMP)
          {env_filter}
        ORDER BY started_at DESC
        LIMIT 200
    """).to_pandas()

    if runs_df.empty:
        st.info("No pipeline runs found in the selected window.")
    else:
        last = runs_df.iloc[0]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Last Run Status", last["STATUS"])
        col2.metric("Duration (min)", last["DURATION_MIN"])
        col3.metric("Models Run", int(last["MODELS_RUN"]))
        col4.metric("Tests Run", int(last["TESTS_RUN"]))

        st.subheader("Success Rate Trend")
        runs_df["date"] = pd.to_datetime(runs_df["STARTED_AT"]).dt.date
        success_trend = (
            runs_df.groupby("date")
            .apply(lambda g: (g["STATUS"] == "success").mean() * 100)
            .reset_index(name="success_pct")
        )
        chart = (
            alt.Chart(success_trend)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("success_pct:Q", title="Success %", scale=alt.Scale(domain=[0, 100])),
                tooltip=["date:T", "success_pct:Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart, use_container_width=True)

        st.subheader("Run History")
        st.dataframe(
            runs_df[["RUN_ID", "ENV", "COMMAND", "STATUS", "STARTED_AT",
                      "DURATION_MIN", "MODELS_RUN", "TESTS_RUN", "MODELS_FAILED", "TESTS_FAILED"]],
            use_container_width=True,
        )

# ---------------------------------------------------------------------------
# Page 2: Model Health
# ---------------------------------------------------------------------------
elif page == "2. Model Health":
    st.title("Model Health")

    health_df = session.sql(f"""
        SELECT
            mh.model_name,
            mh.schema_name,
            mh.status,
            mh.rows_affected,
            ROUND(mh.execution_time_seconds, 1) AS exec_sec,
            mh.logged_at,
            pl.env
        FROM {OBS}.MODEL_HEALTH_LOG AS mh
        JOIN {OBS}.PIPELINE_RUN_LOG AS pl ON mh.run_id = pl.run_id
        WHERE mh.logged_at >= DATEADD('day', -{lookback_days}, CURRENT_TIMESTAMP)
          {env_filter.replace("env", "pl.env")}
        ORDER BY mh.logged_at DESC
        LIMIT 2000
    """).to_pandas()

    if health_df.empty:
        st.info("No model health data found.")
    else:
        st.subheader("Row Count Trends (top 10 models by volume)")
        top_models = (
            health_df.groupby("MODEL_NAME")["ROWS_AFFECTED"]
            .max()
            .nlargest(10)
            .index.tolist()
        )
        trend_df = health_df[health_df["MODEL_NAME"].isin(top_models)].copy()
        trend_df["date"] = pd.to_datetime(trend_df["LOGGED_AT"]).dt.date

        row_trend = (
            alt.Chart(trend_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("ROWS_AFFECTED:Q", title="Rows Affected"),
                color=alt.Color("MODEL_NAME:N"),
                tooltip=["MODEL_NAME:N", "date:T", "ROWS_AFFECTED:Q"],
            )
            .properties(height=350)
        )
        st.altair_chart(row_trend, use_container_width=True)

        st.subheader("Execution Time Heatmap (seconds)")
        heatmap_df = trend_df.groupby(["MODEL_NAME", "date"])["EXEC_SEC"].mean().reset_index()
        heatmap = (
            alt.Chart(heatmap_df)
            .mark_rect()
            .encode(
                x=alt.X("date:T", title="Date"),
                y=alt.Y("MODEL_NAME:N", title="Model"),
                color=alt.Color("EXEC_SEC:Q", scale=alt.Scale(scheme="reds"), title="Avg Exec (s)"),
                tooltip=["MODEL_NAME:N", "date:T", "EXEC_SEC:Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(heatmap, use_container_width=True)

        st.subheader("All Model Health Records")
        st.dataframe(health_df, use_container_width=True)

# ---------------------------------------------------------------------------
# Page 3: Cortex Usage & Cost
# ---------------------------------------------------------------------------
elif page == "3. Cortex Usage & Cost":
    st.title("Cortex Usage & Cost")

    cortex_df = session.sql(f"""
        SELECT
            cu.function_name,
            cu.calls_count,
            cu.avg_latency_seconds,
            cu.null_output_count,
            cu.input_tokens,
            cu.output_tokens,
            cu.logged_at,
            pl.env
        FROM {OBS}.CORTEX_USAGE_LOG AS cu
        JOIN {OBS}.PIPELINE_RUN_LOG AS pl ON cu.run_id = pl.run_id
        WHERE cu.logged_at >= DATEADD('day', -{lookback_days}, CURRENT_TIMESTAMP)
          {env_filter.replace("env", "pl.env")}
        ORDER BY cu.logged_at DESC
    """).to_pandas()

    if cortex_df.empty:
        st.info("No Cortex usage data found.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Cortex Calls", int(cortex_df["CALLS_COUNT"].sum()))
        col2.metric("Avg Latency (s)", round(cortex_df["AVG_LATENCY_SECONDS"].mean(), 2))
        null_rate = (
            cortex_df["NULL_OUTPUT_COUNT"].sum() / max(cortex_df["CALLS_COUNT"].sum(), 1) * 100
        )
        col3.metric("Null Output Rate %", round(null_rate, 1))

        st.subheader("Credits by Cortex Function")
        cost_view_df = session.sql(f"""
            SELECT
                report_date,
                resource_name AS function_name,
                SUM(credits_used) AS credits_used,
                SUM(estimated_usd_cost) AS estimated_usd
            FROM {OBS}.CORTEX_COST_DAILY
            WHERE report_date >= DATEADD('day', -{lookback_days}, CURRENT_DATE)
            GROUP BY 1, 2
            ORDER BY 1 DESC
        """).to_pandas()

        if not cost_view_df.empty:
            bar = (
                alt.Chart(cost_view_df)
                .mark_bar()
                .encode(
                    x=alt.X("report_date:T", title="Date"),
                    y=alt.Y("credits_used:Q", title="Credits Used"),
                    color=alt.Color("function_name:N", title="Function"),
                    tooltip=["report_date:T", "function_name:N", "credits_used:Q", "estimated_usd:Q"],
                )
                .properties(height=300)
            )
            st.altair_chart(bar, use_container_width=True)

            # Monthly forecast (simple linear extrapolation)
            days_elapsed = min(lookback_days, 30)
            mtd_cost = cost_view_df["estimated_usd"].sum()
            forecast = round(mtd_cost / days_elapsed * 30, 2) if days_elapsed > 0 else 0
            st.metric("Monthly Cost Forecast (USD)", f"${forecast}")

        st.subheader("Latency Trend by Function")
        cortex_df["date"] = pd.to_datetime(cortex_df["LOGGED_AT"]).dt.date
        lat_trend = cortex_df.groupby(["date", "FUNCTION_NAME"])["AVG_LATENCY_SECONDS"].mean().reset_index()
        lat_chart = (
            alt.Chart(lat_trend)
            .mark_line(point=True)
            .encode(
                x=alt.X("date:T"),
                y=alt.Y("AVG_LATENCY_SECONDS:Q", title="Avg Latency (s)"),
                color="FUNCTION_NAME:N",
                tooltip=["date:T", "FUNCTION_NAME:N", "AVG_LATENCY_SECONDS:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(lat_chart, use_container_width=True)

# ---------------------------------------------------------------------------
# Page 4: Data Quality
# ---------------------------------------------------------------------------
elif page == "4. Data Quality":
    st.title("Data Quality")

    dq_df = session.sql(f"""
        SELECT
            dq.test_name,
            dq.model_name,
            dq.column_name,
            dq.status,
            dq.severity,
            dq.failure_count,
            dq.logged_at,
            pl.env
        FROM {OBS}.DATA_QUALITY_LOG AS dq
        JOIN {OBS}.PIPELINE_RUN_LOG AS pl ON dq.run_id = pl.run_id
        WHERE dq.logged_at >= DATEADD('day', -{lookback_days}, CURRENT_TIMESTAMP)
          {env_filter.replace("env", "pl.env")}
        ORDER BY dq.logged_at DESC
        LIMIT 5000
    """).to_pandas()

    if dq_df.empty:
        st.info("No data quality results found.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Tests", len(dq_df))
        col2.metric("Failures", int((dq_df["STATUS"] == "fail").sum()))
        col3.metric(
            "Pass Rate %",
            round((dq_df["STATUS"] == "pass").mean() * 100, 1),
        )

        st.subheader("Test Pass/Fail by Tier")
        dq_df["tier"] = dq_df["TEST_NAME"].apply(
            lambda n: (
                "Tier 4" if "anomaly" in n or "drift" in n or "kl_" in n
                else "Tier 3" if "cortex" in n or "sentiment" in n or "untranslated" in n or "latency" in n
                else "Tier 2" if "expect_" in n or "rfm" in n or "mmm" in n
                else "Tier 1"
            )
        )
        tier_summary = dq_df.groupby(["tier", "STATUS"]).size().reset_index(name="count")
        tier_chart = (
            alt.Chart(tier_summary)
            .mark_bar()
            .encode(
                x=alt.X("tier:N"),
                y=alt.Y("count:Q"),
                color=alt.Color(
                    "STATUS:N",
                    scale=alt.Scale(
                        domain=["pass", "warn", "fail", "error"],
                        range=["#28a745", "#ffc107", "#dc3545", "#6c757d"],
                    ),
                ),
                tooltip=["tier:N", "STATUS:N", "count:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(tier_chart, use_container_width=True)

        failing = dq_df[dq_df["STATUS"].isin(["fail", "error"])]
        if not failing.empty:
            st.subheader("Failing Tests")
            st.dataframe(
                failing[["TEST_NAME", "MODEL_NAME", "COLUMN_NAME", "SEVERITY",
                          "FAILURE_COUNT", "LOGGED_AT"]],
                use_container_width=True,
            )

        st.subheader("Open Alerts (anomaly checks)")
        alerts = dq_df[dq_df["TEST_NAME"].str.startswith("anomaly__", na=False)]
        if alerts.empty:
            st.success("No open anomaly alerts.")
        else:
            st.dataframe(alerts, use_container_width=True)

# ---------------------------------------------------------------------------
# Page 5: Lineage & Coverage
# ---------------------------------------------------------------------------
elif page == "5. Lineage & Coverage":
    st.title("Lineage & Coverage")

    st.info(
        "Lineage is read from `manifest.json` uploaded to the dbt artifacts stage after each run. "
        "Coverage % = (models with ≥1 test) / total models."
    )

    # Attempt to read manifest.json from stage
    try:
        manifest_rows = session.sql(f"""
            SELECT $1 AS manifest_json
            FROM {ARTIFACTS_STAGE}/dev/latest/manifest.json
            (FILE_FORMAT => (TYPE = 'JSON' STRIP_OUTER_ARRAY = FALSE))
        """).collect()

        if manifest_rows:
            manifest = json.loads(manifest_rows[0]["MANIFEST_JSON"])
            nodes = manifest.get("nodes", {})
            model_nodes = {k: v for k, v in nodes.items() if v.get("resource_type") == "model"}
            test_nodes = {k: v for k, v in nodes.items() if v.get("resource_type") == "test"}

            tested_models = {
                t.get("attached_node", "").replace("model.", "")
                for t in test_nodes.values()
                if t.get("attached_node")
            }

            rows = [
                {
                    "model": v["name"],
                    "layer": v.get("fqn", ["", ""])[1] if len(v.get("fqn", [])) > 1 else "",
                    "has_tests": v["name"] in tested_models,
                    "depends_on": ", ".join(
                        d.replace("model.cortex_signal_to_action.", "")
                        for d in v.get("depends_on", {}).get("nodes", [])
                        if "model." in d
                    ),
                }
                for v in model_nodes.values()
            ]
            cov_df = pd.DataFrame(rows)
            coverage_pct = round(cov_df["has_tests"].mean() * 100, 1)
            st.metric("Test Coverage", f"{coverage_pct}%")

            uncovered = cov_df[~cov_df["has_tests"]]
            if not uncovered.empty:
                st.warning(f"{len(uncovered)} model(s) have no tests:")
                st.dataframe(uncovered[["model", "layer", "depends_on"]], use_container_width=True)
            else:
                st.success("All models have at least one test.")

            st.subheader("Full Lineage Table")
            st.dataframe(cov_df, use_container_width=True)
        else:
            st.warning("manifest.json not found in the artifacts stage. Run `dbt run` first.")
    except Exception as exc:
        st.warning(f"Could not read manifest.json from stage: {exc}")
        st.caption("Run CALL UPLOAD_DBT_ARTIFACTS(...) after dbt run to populate this page.")

# ---------------------------------------------------------------------------
# Page 6: Cost & Credits
# ---------------------------------------------------------------------------
elif page == "6. Cost & Credits":
    st.title("Cost & Credits")

    unit_price = st.sidebar.number_input("Credit unit price (USD)", min_value=1.0, max_value=10.0, value=3.0, step=0.25)
    budget_usd = st.sidebar.number_input("Monthly budget (USD)", min_value=0.0, value=500.0, step=10.0)

    cost_df = session.sql(f"""
        SELECT
            report_date,
            env,
            component,
            resource_name,
            credits_used,
            credits_used * {unit_price}   AS estimated_usd_cost,
            query_count,
            storage_tb
        FROM {OBS}.COST_DAILY
        WHERE report_date >= DATEADD('day', -{lookback_days}, CURRENT_DATE)
          {env_filter}
        ORDER BY report_date DESC
    """).to_pandas()

    if cost_df.empty:
        st.info("No cost data found. Run EXECUTE TASK TASK_COST_REPORT or call POPULATE_COST_DAILY first.")
    else:
        # MTD totals
        import datetime
        month_start = datetime.date.today().replace(day=1)
        cost_df["report_date"] = pd.to_datetime(cost_df["REPORT_DATE"]).dt.date
        mtd_df = cost_df[cost_df["report_date"] >= month_start]
        mtd_cost = mtd_df["ESTIMATED_USD_COST"].sum()
        mtd_credits = mtd_df["CREDITS_USED"].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("MTD Cost (USD)", f"${mtd_cost:.2f}")
        col2.metric("MTD Credits", f"{mtd_credits:.2f}")
        budget_pct = round(mtd_cost / budget_usd * 100, 1) if budget_usd > 0 else 0
        col3.metric("Budget Used %", f"{budget_pct}%", delta=f"Budget ${budget_usd:.0f}/mo")

        if budget_usd > 0 and mtd_cost > budget_usd:
            st.error(f"MTD cost ${mtd_cost:.2f} exceeds monthly budget ${budget_usd:.2f}!")

        st.subheader("Daily Cost by Component (stacked bar)")
        stacked = cost_df.groupby(["report_date", "COMPONENT"])["ESTIMATED_USD_COST"].sum().reset_index()
        stacked_chart = (
            alt.Chart(stacked)
            .mark_bar()
            .encode(
                x=alt.X("report_date:T", title="Date"),
                y=alt.Y("ESTIMATED_USD_COST:Q", title="Estimated USD"),
                color=alt.Color("COMPONENT:N", title="Component"),
                tooltip=["report_date:T", "COMPONENT:N",
                         alt.Tooltip("ESTIMATED_USD_COST:Q", format="$.2f")],
            )
            .properties(height=320)
        )
        st.altair_chart(stacked_chart, use_container_width=True)

        st.subheader("Cost by Environment")
        by_env = cost_df.groupby("ENV")["ESTIMATED_USD_COST"].sum().reset_index()
        env_chart = (
            alt.Chart(by_env)
            .mark_arc(innerRadius=50)
            .encode(
                theta=alt.Theta("ESTIMATED_USD_COST:Q"),
                color=alt.Color("ENV:N"),
                tooltip=["ENV:N", alt.Tooltip("ESTIMATED_USD_COST:Q", format="$.2f")],
            )
            .properties(height=280)
        )
        st.altair_chart(env_chart, use_container_width=True)

        # Cost efficiency: credits per 1k reviews processed
        review_count = session.sql("""
            SELECT COUNT(*) AS cnt
            FROM CSTA_MARKETING_DEV.SILVER.SLV_FEEDBACK_ENRICHED
        """).collect()[0]["CNT"]

        if review_count and review_count > 0:
            efficiency = round(mtd_credits / (review_count / 1000), 4)
            st.metric("Credits per 1k Reviews", efficiency)

        # Monthly forecast
        days_so_far = (datetime.date.today() - month_start).days + 1
        forecast_usd = round(mtd_cost / days_so_far * 30, 2) if days_so_far > 0 else 0
        st.metric("Monthly Cost Forecast (USD)", f"${forecast_usd}")

        st.subheader("Cost Detail")
        st.dataframe(
            cost_df.sort_values("report_date", ascending=False),
            use_container_width=True,
        )
