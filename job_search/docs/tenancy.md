# Tenancy — the two-zone rule

Authoritative reference for `user_id` scoping decisions. Full reasoning:
`PLAN.md` Step 1a, `DECISIONS.md` §7.

| Zone | Contents | Grain |
|---|---|---|
| **Shared** | Job postings, dedup identity map, `dim_job`, `dim_company`, company intel, all market marts, taxonomy, emergent detection, question text, `shared_api_quota`, `target_company` | collected once, identical for everyone |
| **Per-user** | Truth base, scores, artefacts, applications, review progress, offers, preferences, alerts, `app_user`, `user_quota` | scoped to `user_id` |

## Adding a new per-user table

Every per-user table follows exactly this pattern (see
`db/migrations/versions/0001_create_app_user.py` and `0002_create_quota_tables.py`
for worked examples):

1. `user_id` column: `NOT NULL`, `FOREIGN KEY REFERENCES app_user (id)`.
2. An index leading on `user_id`.
3. `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY`.
4. `CREATE POLICY <table>_isolation ON <table> USING (user_id = current_setting('app.current_user_id', true)::uuid)` —
   the GUC name is fixed project-wide; never invent a different one.
5. `GRANT SELECT, INSERT, UPDATE ON <table> TO job_search_app` (add `DELETE`
   only if the table is genuinely meant to support row deletion by the app
   role).
6. A negative test in `packages/core/tests/integration/`, following the
   pattern in `test_rls_isolation.py`: insert rows for two users as the
   migration role, query with no `WHERE` as the app role scoped to user A,
   assert zero rows belonging to user B.

## Adding a new shared table

No `user_id`, no RLS policy. Note in the table's migration comment *why*
it's shared, so a future edit doesn't add `user_id` by reflex (see the
`fct_market_demand` selection-bias precedent in PLAN.md Step 11).
