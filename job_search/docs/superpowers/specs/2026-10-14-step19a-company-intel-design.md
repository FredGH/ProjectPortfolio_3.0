# Step 19a — Persisted company intelligence: design

Status: draft for review · 2026-10-14 · PLAN.md "Step 19a"

## Purpose

Research a company once, keep the result (versioned, with a freshness date),
and let anything that needs it — Step 19's cover letter and pitch, Step 20a's
interview prep — read it instead of researching again. The research is
attached to the **end client**, not the recruitment agency that posted the job.

### Order change

19a is built **before** Step 19. Step 19's Researcher agent therefore does not
fetch anything: it reads the intel this step stores, so the cover letter and the
pitch draw on the same research. `PLAN.md` is updated accordingly (see
"Plan changes").

### Done when

A second `research-company` run for the same company reuses the stored version
instead of crawling and extracting again, and the Company Intel page shows it.
The interview-prep view named in `PLAN.md` belongs to Step 20a and is out of
scope here.

## Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Crawler | **Crawl4AI**, run as its own Docker Compose service and called over HTTP |
| Trigger | **On demand**, per company (CLI + UI button). No batch stage |
| End client behind an agency | **LLM reads the posting**, plus a manual override in the UI |
| Sources in v1 | **Company website only** (about, careers, engineering/blog pages) |
| Architecture | **Fixed pipeline** (resolve → crawl → extract → store), not a free-roaming agent |

Follow-ups, not in this step: Companies House (UK), GitHub public repos, news
and sentiment, a batch research stage, progress polling for long runs.

## Data (new `intel` schema, Alembic migration, shared across users — no RLS)

Company intel is shared data (PLAN.md, "Shared" row), like `dim_job`.

- **`intel.company_intel`** — append-only.
  - `id uuid pk`, `company_key text`, `version int`, `display_name text`,
    `status text` (`ok` | `no_content` | `failed`), `researched_at timestamptz`,
    `content jsonb`, `pages jsonb`, `resolved_from_job text`, `model text`,
    `prompt_version text`, `usage jsonb`.
  - Unique `(company_key, version)`. The current intel is the highest version.
    Rows are never updated, so a cover letter sent in March can be checked
    against the intel as it stood in March.
  - `content` holds the structured facts (below); every fact carries
    `source_url`. `pages` lists each crawled URL with its fetch time.
- **`intel.crawl_cache`** — `url text`, `fetched_on date`, `status text`,
  `markdown text`; primary key `(url, fetched_on)`. A cache entry younger than
  `crawl_cache_days` is reused, so a re-research does not re-crawl.
- **`intel.job_company`** — one row per `job_group_id`: `posted_company`,
  `poster_kind` (`employer` | `agency` | `unknown`), `end_client`, `website_url`,
  `method` (`employer` | `llm` | `manual`), `resolved_at`. A UI override writes
  `method = 'manual'`, and a manual row is never overwritten by the LLM.

**Key.** `company_key` is the md5 of `normalise_company(name)`, computed exactly
as `gold.dim_company.company_key` (`dbt_utils.generate_surrogate_key` over the
normalised name). It joins to `dim_company` when the company exists there. End
clients behind agencies often are not in `dim_company`, so there is no foreign
key.

**Config** (`config/company_intel.yml`): `refresh_after_days: 90`,
`crawl_cache_days: 7`, `max_pages: 8`.

**Staleness.** Fresh = `researched_at` within `refresh_after_days`. A stale (or
forced) run re-extracts with the previous version passed as context and appends
a new version; it does not rebuild from scratch.

### Intel content (JSON in `content`)

Each field is a list of `{ "text": ..., "source_url": ... }` items, or empty.

- `business_model`, `size_signals` (headcount, offices, growth wording),
  `tech_stack`, `recent_announcements` (dated where the page gives a date),
  `notes` (anything else useful for a cover letter, still cited).

## Pipeline (`packages/core/core/company_intel/`)

1. **`resolve.py`** — decide which company to research for a job.
   - A `manual` row in `intel.job_company` wins.
   - Otherwise the LLM task `company_resolution` reads title, company and
     description and returns: employer vs agency, the end-client name if the
     description names one, and a candidate official website. The result is saved.
   - Agency with no end client → status "end client unknown": nothing is
     crawled until the user sets one, or chooses to research the agency itself.
   - The candidate website is **verified**: the homepage is crawled and the
     company name must appear in its text. A failed check rejects the guess; the
     user can then type the correct website.
2. **`fetch.py`** — `Fetcher` protocol (`fetch(url) -> Page(markdown, status)`),
   `Crawl4AIFetcher` (HTTP to the compose service), and `CachedFetcher`
   (reads/writes `intel.crawl_cache`). Same-domain only, `robots.txt` respected,
   `max_pages` cap.
3. **`discover.py`** — homepage first, then links whose path looks like about,
   careers/jobs, engineering, blog or technology, up to the cap.
4. **`extract.py`** — LLM task `company_intel_extraction` over the labelled page
   text. **Fabrication guard:** any fact whose `source_url` was not actually
   crawled is dropped; fields with no evidence stay empty.
5. **`store.py`** — read the latest/any version; append a version.
6. **`service.py`** — `get_or_refresh_intel(engine, job_group_id, *, force=False)`:
   resolve → return the latest version if fresh and not forced → else crawl,
   extract, append. Steps 19 and 20a call this one function.

**Failure rules.** If every page fails, nothing is written and the previous
version stays current. If pages load but nothing relevant is found, a
`no_content` version is stored so it is not retried on every call.

## Interfaces

- **CLI** (pipeline container): `research-company --user-id … --job-group-id …
  [--force] [--end-client NAME] [--website URL]`; prints the version used and
  whether it was reused or refreshed.
- **API:** `GET /intel/jobs/{job_group_id}` (resolution + latest intel + version
  list), `POST /intel/jobs/{job_group_id}/research` (runs research; synchronous
  in v1 with a long client timeout), `PUT /intel/jobs/{job_group_id}/resolution`
  (manual end client / website).
- **UI:** new page **Company Intel** — job picker (the existing candidates
  list), the resolution with an override form, the intel facts with source
  links, a version picker, a freshness badge and a Research/Refresh button. The
  `job_spec.md` written by `render-cv` gains a link to this page.
- **Infrastructure:** `crawl4ai` as a Docker Compose service (the official
  image, REST API), not started by default; `Crawl4AIFetcher` reads its base URL
  from `CRAWL4AI_URL`. The `api` and `pipeline` images stay free of Chromium.
- **LLM tasks:** `company_resolution` and `company_intel_extraction` in
  `config/llm_tasks.yml` (Claude by default), prompts under
  `prompts/company_resolution/` and `prompts/company_intel_extraction/`; token
  use and cost are logged like the other tasks.

## Testing

- Unit tests with a fake `Fetcher` and a fake LLM adapter: resolution (employer,
  agency with and without an end client, manual precedence, website
  verification), page discovery, the cited-URL guard, versioning, freshness and
  forced refresh, the failure rules, `company_key` equal to `dim_company`'s.
- A migration test, API tests, Streamlit AppTest tests for the page, a CLI test.
- A live check by hand after the build: research one real employer already in
  the database, then run it again and confirm the stored version is reused.

## Plan changes

- `PLAN.md`: Step 19a moves before Step 19; Step 19's Researcher is rewritten as
  a reader of `dim_company_intel` (the table is named `intel.company_intel`);
  the 19a "Done when" notes the Company Intel page stands in for the
  interview-prep view until Step 20a.
- Companies House, GitHub, news and sentiment stay listed under 19a as
  follow-ups.

## Open risks

- Job postings do not carry the company's website, so the LLM proposes one; the
  verification rule limits the damage but a company with a generic homepage can
  still be mis-verified. The manual override is the backstop.
- Some sites block crawlers or hide content behind scripts; those companies end
  up `no_content` or `failed`, with the reason recorded in `pages`.
- Crawl4AI needs Docker memory for a headless browser; it is started only when
  researching.
