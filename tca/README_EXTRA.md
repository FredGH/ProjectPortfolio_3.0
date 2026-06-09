# README_EXTRA — Sections Not Implemented

This file contains architecture sections and design alternatives that are **not implemented** in the current codebase. They are preserved here as reference for future work.

---

## Component Interactions with Embedded Tableau (Alternative Version)

```mermaid
sequenceDiagram
    participant Browser
    participant Angular
    participant Okta
    participant Tableau
    participant FastAPI
    participant PostgreSQL
    participant Redis
    participant Airflow

    Note over Airflow,PostgreSQL: 06:45 CET — Batch ingest
    Airflow->>PostgreSQL: dlt pipelines → stg_raw (e.g., orders, fills)
    Airflow->>PostgreSQL: dbt Data Vault → marts (e.g., fact_order_execution)

    Note over Redis,PostgreSQL: Continuous — Real-time fills
    Browser->>Angular: POST /mock/fill (synthetic)
    Angular->>FastAPI: (via nginx proxy /api/)
    FastAPI->>Redis: XADD pb:fills
    Redis-->>PostgreSQL: XREADGROUP consumer → stg_raw.rt_fills

    Note over Browser,Okta: User session with Okta SSO
    Browser->>Angular: Click login
    Angular->>Okta: Redirect to Okta SSO (SAML/OIDC)
    Okta->>Browser: Prompt for PrivateBank credentials
    Browser->>Okta: Authenticate
    Okta->>Angular: Redirect with SAML assertion / OIDC tokens
    Angular->>FastAPI: Exchange Okta tokens for platform JWT
    FastAPI->>Okta: Validate tokens (optional)
    FastAPI-->>Angular: {access_token, refresh_token} (RS256 JWT)
    Angular->>Angular: NgRx store + localStorage

    Browser->>Angular: Navigate to /dashboard
    Angular->>Tableau: Load embedded dashboard (via JS API, passing Okta tokens for SSO)
    Tableau->>Okta: Validate SSO tokens
    Okta-->>Tableau: Confirm auth + user claims
    Tableau->>PostgreSQL: Query marts (e.g., SELECT ... WHERE counterparty_id = :cp from claims)
    PostgreSQL-->>Tableau: TCA results (e.g., slippage, benchmarks)
    Tableau-->>Angular: Render interactive charts (e.g., cost decomposition)
    Angular-->>Browser: Display embedded Tableau viz

    Note over Tableau: Security
    Tableau->>Okta: SSO validation for row-level security (RLS)
    Okta-->>Tableau: User claims (CLIENT sees own data only)
```



This alternative version incorporates Okta SSO for unified authentication across PrivateBank's ecosystem, with Tableau embedded in the Angular SPA using Okta tokens for secure access. It connects directly to PostgreSQL for TCA data visualization, maintaining RBAC via Okta claims and data isolation. Embedding uses Tableau's JS API with Okta SSO for seamless integration.
