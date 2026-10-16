# Step 21c — Salary and rate negotiation support: design

Status: approved in conversation · 2026-10-16 · PLAN.md "Step 21c"

## Purpose

When an offer arrives, say where it sits in the market, honestly. Given an
amount, basis, engagement type, IR35 status, category, region and seniority:
return the offer's percentile within the matching slice, comparable postings
with disclosed rates, and whether the slice is rising or falling over six
months, never as a bare percentile and never when the slice is too thin. Make
two offers comparable inside vs outside IR35 with a transparent net estimate.

### Done when

Given an offer, the system returns its percentile within the right slice with
`n_disclosed` and `disclosure_rate` stated every time, and refuses a number
when the slice is too thin.

## Decisions taken

| Question | Decision |
|---|---|
| IR35 net | A transparent estimate from a configurable UK tax-year config, labelled approximate, not tax advice |
| Surface | API + Offer Check page + stored offers (per user, RLS) |
| `fct_application` | Does not exist yet (Steps 20a / 21); offers get their own table with a nullable link to it |

## Data

`gold.offer_benchmark_pool` (dbt): one row per targeted-corpus posting
(contract or permanent) with category, region, seniority, contract type, IR35
status, a figure (GBP per day for contract: stated daily/hourly rate; GBP per
year for permanent: stated annual salary) or NULL, and the month first seen.
Undisclosed postings stay in so the disclosure rate is real.

## Percentile (`core/offers/benchmark.py`, pure)

- Offer normalised to the pool's unit: contract = GBP per day (hourly x
  configured hours per day), permanent = GBP per year; a mismatched basis is
  refused.
- Slice ladder: category + region + seniority + contract type (+ IR35 when
  given), then drop IR35, seniority, region, then contract type only
  (market-wide, flagged). Narrowest slice with >= `min_disclosed` (10)
  disclosed figures is used; every step's n is shown. If none qualifies: no
  percentile ("too thin"), still showing n, rate and comparables.
- Percentile = mid-rank position among disclosed figures. Also P25, median, P75,
  `n_disclosed`, `n_postings`, `disclosure_rate`, up to 8 comparable postings
  nearest the offer, and a warning when under 30% of postings disclose pay.
- Direction: median of the last 3 months vs 6 months earlier, only if both
  windows have >= `min_disclosed` figures; otherwise "insufficient history".

## IR35 net (`core/offers/tax.py`, `config/uk_tax.yml`)

Take-home per year and per day for a day rate inside IR35 (umbrella/PAYE:
umbrella fee, employer NI and levy taken from the invoice, then income tax
and employee NI) and outside (limited company: accountant costs, optimal
director salary, employer NI, corporation tax with marginal relief, dividend
tax). Personal-allowance taper included. Config values for 2025/26 are marked
`verified: false`; every assumption is printed; pension, student loan, VAT and
expenses are ignored and stated as such.

## Stored offers

`offers.offer_received` (RLS): amount, basis, engagement, IR35, category,
region, seniority, company, title, received_at, notes, the benchmark snapshot,
the tax-config year, and nullable `job_group_id` / `application_id`.

## Interfaces

`POST /offers/benchmark`, `POST /offers` (save), `GET /offers`,
`POST /offers/net-compare` (two day rates with their IR35 status); page
**Offer Check**; `scripts/demo_offers_ui.sh`.

## Out of scope

The outcome loop, counter-offer drafting, salary history, recording against
`fct_application` (the column exists, the link comes with that table).

## Risks

Disclosed data is thin (16 contract, 20 permanent figures in the whole corpus
today), so most real slices will refuse a percentile; that is the correct
answer. Tax figures are assumptions to verify each April.
