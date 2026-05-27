# ProjectPortfolio_3.0

A portfolio of AI agent projects built with the Anthropic SDK and Claude.

## Projects

| Project | Description |
|---|---|
| [complaint_analyser](ProjectPortfolio_3.0/complaint_analyser/) | Production-ready agentic RAG system for triage and urgency scoring of unstructured complaint data, built on a fully self-hosted stack. See [README](ProjectPortfolio_3.0/complaint_analyser/README.md). |
| [research_to_podcast](ProjectPortfolio_3.0/research_to_podcast/) | 4-agent sequential pipeline that turns any topic into a two-host MP3 podcast (Researcher → Analyst → Scriptwriter → Audio Producer). See [README](ProjectPortfolio_3.0/research_to_podcast/README.md). |
| [weather_forecaster](ProjectPortfolio_3.0/weather_forecaster/) | Data engineering pipeline that extracts weather data from OpenWeather API, loads it into DuckDB via parquet staging, and runs dbt transformations — orchestrated with Dagster. See [README](ProjectPortfolio_3.0/weather_forecaster/README.md). |
| [tca](ProjectPortfolio_3.0/tca/) | MiFID II / MiFIR compliant Transaction Cost Analysis platform for PrivateBank's pan-European institutional equities business. Full-stack PoC: dlt ingestion → Data Vault 2.0 (dbt/PostgreSQL+TimescaleDB) → 10 TCA analytics modules → FastAPI (JWT RS256) → Angular 17 SPA, orchestrated with Airflow. 400 synthetic orders across 4 asset classes. See [README](ProjectPortfolio_3.0/tca/README.md). |
