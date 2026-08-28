# ProjectPortfolio_3.0

A portfolio of AI agent projects built with the Anthropic SDK and Claude.

## Projects

| Project | Description |
|---|---|
| [complaint_analyser](complaint_analyser/) | Production-ready agentic RAG system for triage and urgency scoring of unstructured complaint data, built on a fully self-hosted stack. See [README](complaint_analyser/README.md). |
| [research_to_podcast](research_to_podcast/) | 4-agent sequential pipeline that turns any topic into a two-host MP3 podcast (Researcher → Analyst → Scriptwriter → Audio Producer). See [README](research_to_podcast/README.md). |
| [weather_forecaster](weather_forecaster/) | Data engineering pipeline that extracts weather data from OpenWeather API, loads it into DuckDB via parquet staging, and runs dbt transformations — orchestrated with Dagster. See [README](weather_forecaster/README.md). |
| [tca](tca/) | MiFID II / MiFIR compliant Transaction Cost Analysis platform for PrivateBank's pan-European institutional equities business. Full-stack PoC: dlt ingestion → Data Vault 2.0 (dbt/PostgreSQL+TimescaleDB) → 10 TCA analytics modules → FastAPI (JWT RS256) → Angular 17 SPA, orchestrated with Airflow. 400 synthetic orders across 4 asset classes. See [README](tca/README.md). |

## Related

- [jira_sync_kit](https://github.com/FredGH/jira_sync_kit) — reusable Jira Cloud sync/client package used by `job_search` and any other sibling project that adopts Jira tracking; lives in its own repo so it can be `pip install`ed and versioned independently.
