# ML Use Cases — PrivateBank TCA Platform

Given the data available in this TCA platform, here are the most compelling ML use cases, ranked by business value and feasibility, organised by purpose.

---

## 1. Market Impact Prediction (Pre-Trade) *(highest value)*

The most immediate ML application. The Almgren-Chriss model used in the analytics engine is a parametric formula — it assumes a fixed functional form. An ML model (gradient boosted trees or a neural network) trained on historical order/fill/market data can predict execution cost more accurately by learning non-linear relationships between participation rate, intraday volatility regime, time-of-day, order book depth, and actual realised slippage. This flips TCA from post-trade audit to pre-trade decision support — the PM can see "this order is expected to cost 12 bps, consider splitting it or using IS instead of VWAP."

- **Model:** gradient boosting (XGBoost/LightGBM) or neural network
- **Features:** already exist in `fact_order_execution` — instrument class, side, quantity, vol regime, time of day, participation rate, quantity/ADV
- **Target:** `arrival_slippage_bps` / `market_impact_bps`
- **Data source:** `biz_vault.bv_tca_costs`
- **Minimum data:** ~2,000–5,000 samples per instrument class (extend seed to 60 days)
- **Business case:** better pre-trade cost estimates → better algo selection → lower IS costs for clients

---

## 2. Optimal Execution Scheduling (Reinforcement Learning)

Rather than using fixed VWAP or TWAP schedules, a reinforcement learning agent learns when to trade aggressively versus passively within an execution window. The agent observes the order book state, spread, volume profile, and remaining quantity, and chooses a participation rate for each time step. The reward function is negative slippage. This directly replaces the static algo bucket approach and is the most sophisticated ML application in execution — firms like Goldman Sachs and JP Morgan have deployed this in production.

- **Model:** reinforcement learning (deep Q-network or PPO)
- **Features:** order book state, spread, volume profile, remaining quantity, vol regime
- **Data source:** `stg_raw.tick_bars`, `fact_order_execution`
- **Minimum data:** 1,000–2,000 / class (extend seed to 60 days)
- **Complement:** extends the existing Optimal Algorithm Selection (multi-class classifier on `algo_id` / `total_cost_bps`) with dynamic intra-order scheduling

---

## 3. Adverse Selection Detection (Classification)

Every fill carries a risk of being adversely selected — the other side of the trade knew something you didn't. An ML classifier trained on fill price vs mid at time of fill, post-fill reversion at 5/15/30 minutes, venue type, time of day, and order book imbalance can predict in near-real-time whether a fill is likely to be adversely selected. This allows the SOR to route away from venues or liquidity sources that are consistently flagging as high-adverse-selection.

- **Model:** gradient boosting classifier
- **Features:** fill price vs mid, venue type, time of day, order book imbalance
- **Target/Label:** `adverse_sel_5min_bps` — already computed and stored in the TCA system
- **Data source:** `biz_vault.bv_adverse_selection`
- **Minimum data:** 1,000–2,000 / class (extend seed to 60 days)

---

## 4. Alpha Decay Curve Forecasting (Regression / Time Series)

The TCA system already produces alpha decay curves per fill. An ML model can forecast the expected shape of the alpha decay curve for a new order before it is traded, based on the signal's estimated half-life, current volatility regime, asset class, typical ADV participation, and time of day. This enables the pre-trade analytics module to recommend not just which algo to use, but the optimal trading horizon — turning the existing Alpha Curves capability into a predictive rather than descriptive tool.

- **Model:** regression / LSTM
- **Features:** signal half-life, vol regime, asset class, ADV participation, time of day
- **Data source:** `biz_vault.bv_alpha_decay`
- **Minimum data:** 10,000+ ticks (extend seed + tick data)

---

## 5. Regime Detection (Unsupervised — Clustering)

The TCA system tags orders with volatility regime buckets (low/medium/high) using simple threshold rules on daily vol. An ML clustering model (Hidden Markov Model or k-means on intraday microstructure features) can identify more granular and accurate market regimes — for example: trending/low-spread, choppy/wide-spread, momentum/block-print, illiquid/news-driven. The detected regime becomes a feature for every other ML model and for the algo selection recommendation engine.

- **Model:** HMM or k-means on intraday microstructure features
- **Data source:** `stg_raw.tick_bars`
- **Output:** predicted vol regime (LOW / MEDIUM / HIGH) at order submission time — more granular than the current threshold-based assignment
- **Minimum data:** 10,000+ ticks (extend seed + tick data)
- **Value:** execution quality varies enormously across regimes; better regime detection lifts all other models

---

## 6. Smart Order Router Optimisation (Multi-Armed Bandit)

The venue/SOR analysis module already scores venues by slippage and maker percentage. A multi-armed bandit model can dynamically learn the optimal venue routing policy in real time, treating each venue as an "arm" and continuously updating fill quality estimates. The bandit balances exploration (occasionally routing to underused venues to gather data) against exploitation (concentrating flow to consistently good venues). This directly improves the SOR quality score without requiring manual review of the monthly venue scorecard.

- **Model:** multi-armed bandit (Thompson sampling or UCB)
- **Data source:** `mart_trading_risk.fact_order_execution`, `dim_venue`
- **Minimum data:** 500–1,000 fills / venue (extend seed to 30 days)
- **Value:** replaces the manual monthly venue scorecard with real-time adaptive routing

---

## 7. Broker / Counterparty Quality Prediction (Ranking Model)

When PrivateBank operates as an agency broker routing to external venues or when trading FI via RFQ, the quality of the dealer's quote varies. A learning-to-rank model trained on historical RFQ response data — quote spread vs mid, response latency, fill probability at quoted price — can rank dealers before requesting quotes, prioritising dealers likely to offer the best price. This reduces the cost of price discovery in fixed income and FX derivative execution.

- **Model:** learning-to-rank (LambdaMART or pairwise ranking)
- **Data source:** `biz_vault.bv_tca_costs` (fixed income and FX derivative fills)
- **Minimum data:** 500–1,000 / class (extend seed to 60 days)
- **Scope:** most impactful for `fixed_income` and `fx_derivative` instrument classes

---

## 8. Transaction Cost Anomaly Detection (Autoencoder / Isolation Forest)

Beyond the rule-based observability checks (Z-score on slippage, volume checks), an unsupervised autoencoder or Isolation Forest trained on normal TCA metrics can detect subtle multi-variate anomalies that no single threshold would catch — for example: slippage is within range, impact is within range, but their combination with venue, time, and participation rate is structurally unusual compared to historical norms. This feeds the quarantine queue with higher-quality anomaly signals, reducing false negatives in data quality monitoring.

- **Model:** isolation forest or autoencoder
- **Data source:** `biz_vault.bv_tca_costs`, `obs.obs_warnings`
- **Minimum data:** 200–500 samples per instrument class (extend seed to 30 days)
- **Advantage:** catches multi-dimensional anomalies invisible to univariate Z-score checks; reduces false negatives in `obs.quarantine_queue`

---

## 9. Client Behaviour Segmentation (Clustering + Churn Prediction)

Using the `mart_corporate` data (client activity aggregates) combined with TCA execution quality metrics, a clustering model segments institutional clients by order frequency, typical order size vs ADV, sensitivity to slippage (do they complain when IS is high?), and preference for dark vs lit execution. A separate churn prediction model flags clients whose trading flow is declining, giving the sales team early warning.

- **Model:** clustering (k-means / DBSCAN) + survival analysis / gradient boosting for churn
- **Data source:** `mart_corporate.fact_client_activity`, `dim_client`
- **Minimum data:** 50–100 clients with sufficient history (extend seed + client diversity)
- **Value:** directly supports institutional flow attraction; counterparty-scoped so consistent with the data isolation model

---

## 10. Real-Time Fill Price Prediction (Micro-Price Model)

A micro-price model predicts the short-term fair value of an asset using order book features — bid-ask imbalance, trade flow imbalance, queue position estimates. This is used by the algo engine to decide whether the current market mid is a "true" fair price or temporarily distorted by order flow. Fills executed closer to the predicted micro-price rather than the quoted mid systematically reduce adverse selection.

- **Model:** online regression (Kalman filter or lightweight neural net)
- **Data source:** `stg_raw.tick_bars` (TimescaleDB hypertable)
- **Minimum data:** 100,000+ ticks (extend seed + tick data)
- **Integration point:** feeds real-time slippage estimate into `analytics/modules/pre_trade.py`

---

## Data Requirements Summary

| Use Case | Min. Samples | Current Status | Action Needed |
|---|---|---|---|
| Market Impact Prediction | 2,000–5,000 / class | ~100 / class (1 day) | Extend seed to 60 days |
| Optimal Execution Scheduling | 1,000–2,000 / class | ~100 / class (1 day) | Extend seed to 60 days |
| Adverse Selection Detection | 1,000–2,000 / class | ~100 / class (1 day) | Extend seed to 60 days |
| Alpha Decay Forecasting | 10,000+ ticks | Depends on tick_bars volume | Extend seed + tick data |
| Regime Detection | 10,000+ ticks | Depends on tick_bars volume | Extend seed + tick data |
| SOR Optimisation | 500–1,000 / venue | ~100 / class (1 day) | Extend seed to 30 days |
| Broker Quality Prediction | 500–1,000 / class | ~100 / class (1 day) | Extend seed to 60 days |
| Anomaly Detection | 200–500 / class | ~100 / class (1 day) | Extend seed to 30 days |
| Client Segmentation & Churn | 50–100 clients | Depends on client count | Extend seed + client diversity |
| Micro-Price Model | 100,000+ ticks | Depends on tick_bars volume | Extend seed + tick data |

---

## Summary — Use Cases by Business Driver

| ML Use Case | Driver | Technique | Horizon |
|---|---|---|---|
| Market impact prediction | 1 — Slippage | GBT / Neural net | Pre-trade |
| Optimal execution scheduling | 2 — Algo | Reinforcement learning | Intra-order |
| Adverse selection detection | 1, 4 — Slippage, Alpha | Classification | Real-time |
| Alpha decay forecasting | 3 — Alpha curves | Regression / LSTM | Pre-trade |
| Regime detection | 2, 3 — Algo, Alpha | HMM / k-means | Intraday |
| SOR optimisation | 2 — Algo | Multi-armed bandit | Real-time |
| Broker/dealer ranking | 1 — Slippage | Learning-to-rank | Pre-RFQ |
| Anomaly detection | 5 — Efficiency | Autoencoder / IF | Post-trade |
| Client segmentation & churn | 4 — Flow | Clustering + survival | Weekly |
| Micro-price model | 1, 2 — Slippage, Algo | Online regression | Real-time |

---

## Recommended Starting Point

**#1 — Market Impact Prediction**, because:
- The feature set is complete and already in `fact_order_execution`
- The label is clean (`arrival_slippage_bps`)
- The training data already exists in the `bv_tca_costs` Business Vault table
- The output plugs directly into the existing Angular UI as a pre-trade estimate panel
- No new data sources required — only more synthetic days needed
- Immediately measurable P&L effect: better predictions → better algo selection → lower IS costs for clients

**Prerequisite for all use cases:** extend `ingestion/seed.py` to generate 60 days of synthetic data (~24,000 orders total).
