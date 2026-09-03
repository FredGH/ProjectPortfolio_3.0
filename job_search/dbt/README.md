# dbt project — placeholder

The real dbt project lands in Step 5 (`dbt project and staging models`).

Convention fixed now, in Step 1a, so Step 5 doesn't have to relitigate it:

- **Shared marts** (job postings, `dim_job`, `dim_company`, market marts,
  taxonomy) build once, with no per-user grain at all.
- **Per-user models** take `user_id` as part of the model's **grain** — a
  column selected and grouped on — never as a dbt `var`. A `var` is a
  build-time constant; a per-user model must return every user's rows in one
  build (Postgres RLS then scopes what each user's session can see), not be
  rebuilt once per user.
