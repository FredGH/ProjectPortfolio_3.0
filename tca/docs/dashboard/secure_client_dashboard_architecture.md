# Secure Client Dashboard Architecture for Snowflake Gold Layer

This document outlines the recommended architecture for exposing a client-facing dashboard that reads from the Gold layer of an ETL pipeline, with Snowflake as the data warehouse. Security is paramount to prevent unauthorized access and ensure tenant isolation.

---

## Recommended Dashboard Technology

**Looker (Google Looker Studio)** or **Sigma Computing** — both are designed for enterprise BI with Snowflake and have native row-level security built-in.

Sigma is particularly strong since it's explicitly built for Snowflake and offers direct SQL-querying with security controls.

---

## Security Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT BROWSER                           │
└─────────────────┬───────────────────────────────────────────┘
                  │ HTTPS + OAuth2/SAML
                  ▼
┌─────────────────────────────────────────────────────────────┐
│         LOOKER / SIGMA (Dashboard Layer)                    │
│  - Embeds credentials server-side ONLY                      │
│  - Applies row-level security filters per user session      │
│  - NO credentials exposed to frontend                       │
└─────────────────┬───────────────────────────────────────────┘
                  │ Filtered queries (client_id parameter)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│              SNOWFLAKE (Gold Layer)                         │
│  - Network policy: only dashboard server IPs allowed        │
│  - Row access policy: enforced at DB level                  │
│  - Separate reader role per client tenant                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Security Measures

| Layer | Control |
|-------|---------|
| **Network** | Snowflake network policy allows ONLY Looker/Sigma server IPs |
| **Authentication** | OAuth/SAML via bank's IdP (Okta, Entra ID) — no local passwords |
| **Authorization** | Snowflake row access policies + RBAC scoped per client |
| **Credential isolation** | Service account per dashboard app, NOT per client |
| **Data masking** | Dynamic data masking on PII fields (SSN, account numbers) |
| **Audit** | Query tags + Snowflake audit logs for all access |

---

## Multi-Tenant Data Isolation

Each client is assigned a dedicated Snowflake role. Row access policies enforce tenant isolation at the database level, ensuring clients can only see their own data.

```sql
-- Row access policy enforced at Snowflake layer
CREATE ROW ACCESS POLICY client_isolation_policy
AS (client_id VARCHAR) RETURNS BOOLEAN -
USING (current_role() IN ('CLIENT_ABC_READER', 'CLIENT_XYZ_READER'));
```

---

## Implementation Summary

1. **Dashboard**: Looker or Sigma (not direct SQL access)
2. **Auth**: SAML SSO with your bank's IdP
3. **Isolation**: Snowflake row access policies + per-client roles
4. **Network lock**: Only dashboard IPs allowed to Snowflake
5. **No exposed credentials**: All proxied server-side

This ensures that even if the dashboard is compromised, hackers cannot extract data — the Snowflake connection is locked to filtered reads only.

---

## Looker vs Sigma Comparison

| Criteria | Looker (Google Looker Studio) | Sigma Computing |
|----------|------------------------------|-----------------|
| **Cost** | ~$50-80/user/month (tiered) | ~$95-130/user/month (simpler tiers) |
| **Embed extensibility** | Excellent — embedded analytics, APIs | Strong — embed + custom app dev |
| **Complexity** | Higher — Learn (Looker's language) learning curve | Lower — Excel-like, familiar UI |
| **Snowflake native** | Good (via Block) | Excellent — built for Snowflake |
| **Customization** | Extensive blocks, LookML | Spreadsheet-like, less code-heavy |

### Recommendation

**Sigma** is the better choice for a bank because:

1. **Lower complexity** — business users can use it without training (Excel-like)
2. **Faster time-to-value** — direct Snowflake queries, no LookML to maintain
3. **Better embedded options** — if you need to white-label into client portals later
4. **Row-level security** — native Snowflake integration is tighter

**Looker** if you:
- Already have Google ecosystem (BigQuery, etc.)
- Need complex data modeling layer managed in BI
- Have dedicated LookML developers

For a bank exposing Gold layer data to clients, **Sigma's simplicity and direct Snowflake integration** reduce security misconfiguration risk — simpler = more secure in this context.