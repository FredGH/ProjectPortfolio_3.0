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
| Competitors | **Top 5**, proposed by the LLM from the company's pages plus its general knowledge, each tagged `site` or `model_knowledge` (unverified) and shown with that badge; no competitor crawling in v1 |
| Competitive angle | A **per-job analysis** of how this job, and the company transformation it implies, could put the company ahead of those competitors; labelled as analysis, not fact |

Follow-ups, not in this step: Companies House (UK), GitHub public repos, news
and sentiment, verifying competitors against their own sites or a search
source, a batch research stage, progress polling for long runs.

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
  `markdown text`, `links jsonb` (the page's internal links, needed to pick pages
  from a cached homepage), `error text`; primary key `(url, fetched_on)`. A cache entry younger than
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

**Saved research is always reused.** Research is paid for (Claude), so it only runs
when there is none saved for the company or the user asks for it (`--force`, or
Refresh after a confirmation). Age never triggers a re-research: `researched_at`
older than `refresh_after_days` only turns the "Fresh" badge into "Stale" with a
note to use Refresh. A forced run re-extracts with the previous version passed as
context and appends a new version; it does not rebuild from scratch. Saved
research belongs to the company, so any job at that company shows it (found by the
resolved end client, or for a never-resolved job by the company named on the
posting) without researching again; only the per-job competitive angle is written
for a new job, on request (`POST /intel/jobs/{id}/angle`, one small call) or when
the user researches that job.

### Intel content (JSON in `content`)

Each field is a list of `{ "text": ..., "source_url": ... }` items, or empty.

- `business_model`, `size_signals` (headcount, offices, growth wording),
  `tech_stack`, `recent_announcements` (dated where the page gives a date),
  `notes` (anything else useful for a cover letter, still cited).
- `competitors` — up to 5 items `{ "name", "why", "basis", "source_url" }`.
  `basis` is `site` (the company's own pages name or clearly imply the
  competitor; `source_url` is a crawled page) or `model_knowledge` (the model's
  general knowledge; `source_url` is null). Competitors are always shown as
  unverified unless `basis = site`.

### Company snapshot (`content.snapshot`, model knowledge)

A six-row table, stored in the same intel version: industry/sector, position or
ranking in the industry, market share, growth over the past five years, number of
employees, employee satisfaction. None of it can be crawled, so one extra LLM task
(`company_snapshot`, prompt `prompts/company_snapshot/claude.v1.md`) fills it from
the model's general knowledge, with the crawled business/size facts as context. Each
row is `{key, value, as_of}`; the model may answer `not known` (common for market
share and satisfaction) and gives the year a figure refers to. A guard always returns
exactly six rows in a fixed order, cuts long values and turns anything blank,
non-text or phrased as "unknown" into `not known`. The snapshot is never cited and
always shown as unverified; it does not count as crawled content (an empty crawl is
still `no_content`), and a failed snapshot call never fails the research. Intel stored
before the snapshot existed has none until it is refreshed.

### Competitive angle (per job, not part of the shared intel)

The competitors are company-level, but "how this job could set the company
ahead" depends on the job, so it is stored separately:

- **`intel.job_competitive_angle`** — append-only: `id uuid pk`,
  `job_group_id text`, `company_key text`, `intel_version int` (the
  `company_intel` version it was built from), `content jsonb`, `model text`,
  `prompt_version text`, `usage jsonb`, `created_at timestamptz`.
- `content`: `transformation` (what change the job description implies for the
  company), `advantages` (a list of `{ "competitor", "point" }` — how that
  change could put the company ahead of each listed competitor, or of the field
  generally), and `caveats`.
- It is **analysis, not fact**: the prompt may use only the job description and
  the stored intel, must not invent figures or events about any competitor, and
  every output is shown with an "analysis — not verified" label. A new angle is
  generated when none exists for the job's current intel version, or on
  `--force`.

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
   crawled is dropped; fields with no evidence stay empty. The one exception is
   a competitor with `basis = model_knowledge`, which must have a null
   `source_url` and is kept, flagged unverified (a `site` competitor citing an
   uncrawled URL is dropped like any other fact). At most 5 competitors are
   kept.
5. **`store.py`** — read the latest/any version; append a version.
6. **`angle.py`** — LLM task `competitive_angle`: from the job description and
   the stored intel (competitors included), write the per-job analysis above.
   Stored in `intel.job_competitive_angle` against the intel version used.
7. **`service.py`** — `get_or_refresh_intel(engine, job_group_id, *, force=False)`:
   resolve → return the latest saved version unless forced → else crawl,
   extract, append. `get_or_create_angle(engine, job_group_id, *, force=False)`
   returns the job's angle for the current intel version, generating it when
   missing. Steps 19 and 20a call these two functions.

**Failure rules.** If every page fails, nothing is written and the previous
version stays current. If pages load but nothing relevant is found, a
`no_content` version is stored so it is not retried on every call.

## Interfaces

- **CLI** (pipeline container): `research-company --job-group-id …
  [--force] [--end-client NAME] [--website URL]`; prints the version used and
  whether it was reused or refreshed. It also generates the competitive angle
  for the job unless `--no-angle` is given.
- **API:** `GET /intel/jobs/{job_group_id}` (resolution + latest intel + version
  list + the job's competitive angle), `POST /intel/jobs/{job_group_id}/research` (runs research; synchronous
  in v1 with a long client timeout), `PUT /intel/jobs/{job_group_id}/resolution`
  (manual end client / website).
- **UI:** new page **Company Intel** — job picker (the existing candidates
  list), the resolution with an override form, the intel facts with source
  links, a **Competitors** section (each with its `site` / `model knowledge —
  unverified` badge), a **Competitive angle** section for the selected job
  (labelled "analysis — not verified"), a version picker, a freshness badge and a
  Research/Refresh button. The
  `job_spec.md` written by `render-cv` gains a link to this page.
- **Infrastructure:** (verified live against `unclecode/crawl4ai:0.9.4`: the service
  listens only on the container's loopback unless `CRAWL4AI_API_TOKEN` is set, and
  then requires `Authorization: Bearer <token>`; the compose service takes the token
  from `.env`, publishes its port on `127.0.0.1` only, and `Crawl4AIFetcher` sends
  `CRAWL4AI_API_TOKEN` from settings.) `crawl4ai` as a Docker Compose service (the official
  image, REST API), not started by default; `Crawl4AIFetcher` reads its base URL
  from `CRAWL4AI_URL`. The `api` and `pipeline` images stay free of Chromium.
- **LLM tasks:** `company_resolution`, `company_intel_extraction` and
  `competitive_angle` in `config/llm_tasks.yml` (Claude by default), prompts
  under `prompts/company_resolution/`, `prompts/company_intel_extraction/` and
  `prompts/competitive_angle/`; token
  use and cost are logged like the other tasks.

## Testing

- Unit tests with a fake `Fetcher` and a fake LLM adapter: resolution (employer,
  agency with and without an end client, manual precedence, website
  verification), page discovery, the cited-URL guard, the competitor rules (max 5, `model_knowledge` needs a
  null URL, a `site` competitor with an uncrawled URL is dropped), versioning,
  freshness and forced refresh, the failure rules, `company_key` equal to
  `dim_company`'s, and the angle (generated once per intel version, regenerated
  after a refresh, reused otherwise).
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
- Model-knowledge competitors can be wrong or out of date; they are always
  shown as unverified, and the angle is labelled analysis. Verifying competitors
  (crawling each one, or a web-search source) is a follow-up.
- Some sites block crawlers or hide content behind scripts; those companies end
  up `no_content` or `failed`, with the reason recorded in `pages`.
- Crawl4AI needs Docker memory for a headless browser; it is started only when
  researching.
