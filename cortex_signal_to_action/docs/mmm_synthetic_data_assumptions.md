# MMM Synthetic Data Assumptions

Documents the calibration choices, adstock parameters, seasonality adjustments, noise levels,
and reproducibility notes for `seeds/olist_mmm_weekly_spend.csv`.

---

## Scope

The file covers ISO weeks **2016-W01 → 2018-W34** (138 weeks, Jan 2016 – Aug 2018), aligned
to the publicly available Olist e-commerce dataset date range. The generator script is
`seeds/generate_mmm_spend.py`.

---

## Revenue

| Mode | Source | Notes |
|---|---|---|
| Real | `data/olist_orders_dataset.csv` + `data/olist_order_items_dataset.csv` | revenue = `price + freight_value` per order, summed to ISO week |
| Synthetic (fallback) | Generator script | Used when Olist CSVs are absent (dev / CI without raw data) |

**Synthetic revenue calibration:**

| Parameter | Value | Rationale |
|---|---|---|
| Starting baseline | BRL 60 000 / week | Approximate Olist revenue in early 2016 |
| Growth rate | 1.2% per week (exponential) | Implies ~80% annual growth, consistent with Olist's documented trajectory |
| Q4 uplift (Nov) | ×1.20 | Black Friday + pre-Christmas demand spike |
| Q4 uplift (Dec) | ×1.35 | Christmas shopping peak |
| Jan / Feb dip | ×0.80 / ×0.85 | Post-holiday lull + Carnival weekend slowdown |
| Gaussian noise | σ = 7% | Week-to-week demand variability |

Random seed: **42** (`numpy.random.default_rng(42)`).

---

## Spend Channels

Five channels are modelled, representing a typical Brazilian SME media mix:

| Channel | Weekly range (BRL) | Q4 scale | Adstock decay | Half-life (approx) |
|---|---|---|---|---|
| `tv_spend` | 15 000 – 35 000 | ×1.50 | **0.70** | ~2.6 weeks |
| `paid_search_spend` | 8 000 – 20 000 | ×1.30 | **0.15** | 0.4 weeks (same-week) |
| `social_spend` | 4 000 – 12 000 | ×1.40 | **0.40** | ~0.9 weeks |
| `email_spend` | 1 500 – 4 500 | none | **0.05** | negligible |
| `display_spend` | 3 000 – 9 000 | ×1.25 | **0.35** | ~0.8 weeks |

Values after adstock transformation are stored; raw spend is not retained.

### Adstock model

Geometric (infinite lag) adstock is applied column-wise:

```
adstocked[0] = raw[0]
adstocked[i] = raw[i] + decay × adstocked[i-1]
```

This is the standard one-parameter Koyck lag model. A Weibull or Beta-Binomial adstock
(two-parameter) could be fitted in Phase 6 during MMM regression but is intentionally
excluded here to keep the seed data minimal and interpretable.

### Channel budget rationale

TV dominates because Brazilian out-of-home and open TV have high reach. Paid search is
the second-largest channel, reflecting digital-first customer acquisition. Email is the
lowest absolute spend; its value comes from list-size leverage rather than media cost.

---

## Holiday and Event Flags

### `holiday_flag`

Set to `1` if any of the following dates fall within the ISO week (Monday–Sunday):

| Holiday | Date(s) |
|---|---|
| New Year | 1 Jan |
| Carnival | Mon + Tue, 48–47 days before Easter |
| Good Friday | Fri, 2 days before Easter |
| Easter | Calculated via Anonymous Gregorian algorithm |
| Tiradentes | 21 Apr |
| Labor Day | 1 May |
| Independence Day | 7 Sep |
| Nossa Senhora Aparecida | 12 Oct |
| All Souls' Day | 2 Nov |
| Republic Day | 15 Nov |
| Christmas | 25 Dec |
| Black Friday | See below |

26 holiday weeks are flagged across 2016–2018.

### `black_friday_flag`

Set independently to `1` for the week containing Black Friday (day after the 4th Thursday
in November). 2 weeks are flagged (2016-W47, 2017-W47). Kept separate from `holiday_flag`
to allow the MMM model to estimate a distinct Black Friday coefficient.

---

## Competitor Index

`competitor_index` is a synthetic measure of relative competitor activity on a [0.5, 1.5]
scale (1.0 = baseline parity).

Model: Ornstein-Uhlenbeck-style mean-reverting walk —

```
ci[0] = 1.0
ci[i] = ci[i-1] + N(0, 0.04) + 0.005 × (1.0 - ci[i-1])
```

The mean-reversion term prevents permanent drift. In Phase 6 MMM regression, this variable
acts as a control for competitive pressure effects on attribution.

---

## Average Temperature

`avg_temperature` reflects São Paulo long-term monthly averages (°C), sourced from INMET
climatological normals. All weeks in the same calendar month share the same value — no
inter-week interpolation. Temperature captures seasonality that is orthogonal to the
marketing calendar and helps separate demand-driven from spend-driven revenue variation.

| Month | °C |
|---|---|
| Jan | 23.5 |
| Feb | 24.0 |
| Mar | 23.0 |
| Apr | 21.0 |
| May | 18.5 |
| Jun | 17.0 |
| Jul | 16.5 |
| Aug | 17.5 |
| Sep | 19.0 |
| Oct | 21.0 |
| Nov | 22.0 |
| Dec | 23.0 |

---

## Reproducibility

| Control | Value |
|---|---|
| `numpy.random.default_rng` seed | 42 |
| Date range | Fixed constants in script (`DATE_START`, `DATE_END`) |
| Holiday logic | Deterministic algorithm, no randomness |
| Temperature | Lookup table, no randomness |

Re-running `python seeds/generate_mmm_spend.py` with the same inputs produces a
byte-identical `olist_mmm_weekly_spend.csv`. The only source of non-determinism would be
a change to Olist source CSV content, which should be treated as a data migration event
and documented separately.

---

## Known Limitations

- Revenue and spend are not causally linked in the seed data; the MMM regression in Phase 6
  will estimate these relationships.
- Display and social channels share a similar adstock decay and may exhibit multicollinearity
  in regression — VIF checks are recommended in Phase 6.
- Competitor index is entirely synthetic; no real competitive data was available.
- Temperature is monthly, not weekly; genuine weekly variation (heat waves, cold snaps) is
  not captured.
