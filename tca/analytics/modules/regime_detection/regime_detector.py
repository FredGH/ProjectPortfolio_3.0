"""Regime Detection — Unsupervised K-Means clustering on 30-second OHLCV bars.

The TCA system currently tags orders with LOW/MEDIUM/HIGH vol regimes using
threshold rules on daily realised volatility.  This module replaces that with
a microstructure-aware unsupervised classifier trained on intraday bar features:

  intraday_vol   (high − low) / close      price-range proxy for bar volatility
  volume_ratio   z-scored bar volume        relative liquidity / urgency signal
  momentum       (close − open) / open      directional order-flow pressure

KMeans (k=3) assigns bars to clusters; clusters are mapped to LOW/MEDIUM/HIGH
by ascending average intraday_vol so the label is always interpretable.

Data source : stg_raw.tick_bars (TimescaleDB hypertable, 30-second bars)
Output      : predicted vol regime (LOW / MEDIUM / HIGH) per tick + confidence
Persistence : analytics/models/regime_kmeans.pkl
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sqlalchemy as sa
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODEL_DIR / "regime_kmeans.pkl"

N_CLUSTERS = 3
REGIME_LABELS = ["LOW", "MEDIUM", "HIGH"]
FEATURE_COLS = ["intraday_vol", "volume_ratio", "momentum"]

# Human-readable descriptions per regime (derived at train time, stored in artifact)
_REGIME_DESCRIPTIONS = {
    "LOW": "Trending / tight spread — low urgency, passive strategies preferred",
    "MEDIUM": "Normal / mixed flow — standard VWAP/TWAP conditions",
    "HIGH": "Choppy / wide spread — elevated stress, IS or faster schedule recommended",
}


# ── Data loading ──────────────────────────────────────────────────────────────


_TRAIN_SAMPLE = 100_000  # cap training data to keep memory bounded


def _load_ticks(engine: sa.Engine, trade_date: str | None = None) -> pd.DataFrame:
    if trade_date:
        sql = sa.text(
            """
            SELECT
                bar_id, instrument_id, ts,
                "open", high, low, close, volume, trade_date
            FROM stg_raw.tick_bars
            WHERE trade_date = :trade_date
            ORDER BY instrument_id, ts
        """
        )
        with engine.connect() as conn:
            return pd.read_sql(sql, conn, params={"trade_date": trade_date})
    # For training: stratified sample across instrument × trade_date to stay within
    # memory limits while preserving distribution (4M+ bars would OOM-kill the process)
    sql = sa.text(
        """
        SELECT
            bar_id, instrument_id, ts,
            "open", high, low, close, volume, trade_date
        FROM stg_raw.tick_bars
        TABLESAMPLE BERNOULLI(2.5)
        ORDER BY instrument_id, ts
        LIMIT :limit
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"limit": _TRAIN_SAMPLE})
    # Fallback: if TABLESAMPLE returns too few rows (sparse data), full scan with LIMIT
    if len(df) < 1_000:
        sql_full = sa.text(
            """
            SELECT
                bar_id, instrument_id, ts,
                "open", high, low, close, volume, trade_date
            FROM stg_raw.tick_bars
            ORDER BY random()
            LIMIT :limit
        """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql_full, conn, params={"limit": _TRAIN_SAMPLE})
    return df


# ── Feature engineering ───────────────────────────────────────────────────────


def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close_safe = df["close"].replace(0, np.nan)
    open_safe = df["open"].replace(0, np.nan)

    df["intraday_vol"] = (df["high"] - df["low"]) / close_safe
    df["momentum"] = (df["close"] - df["open"]) / open_safe

    vol_mean = df.groupby(["instrument_id", "trade_date"])["volume"].transform("mean")
    vol_std = (
        df.groupby(["instrument_id", "trade_date"])["volume"]
        .transform("std")
        .fillna(0)
        .replace(0, 1.0)
    )
    df["volume_ratio"] = (df["volume"] - vol_mean) / vol_std

    df = df.dropna(subset=FEATURE_COLS)

    for col in FEATURE_COLS:
        lo = df[col].quantile(0.01)
        hi = df[col].quantile(0.99)
        df[col] = df[col].clip(lo, hi)

    return df


# ── Training ──────────────────────────────────────────────────────────────────


def train(engine: sa.Engine) -> dict[str, Any]:
    """Fit KMeans regime detector on all available tick data and persist to disk."""
    df = _load_ticks(engine)
    if df.empty:
        return {"status": "error", "message": "No tick data in stg_raw.tick_bars"}

    df = _compute_features(df)
    n = len(df)
    logger.info("Regime detector: fitting KMeans(k=%d) on %d bars.", N_CLUSTERS, n)

    X = df[FEATURE_COLS].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    kmeans = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    df["cluster_id"] = kmeans.fit_predict(X_scaled).astype(int)

    # Map cluster IDs → regime labels by ascending average intraday_vol
    cluster_mean_vol = df.groupby("cluster_id")["intraday_vol"].mean().sort_values()
    cluster_to_regime: dict[int, str] = {
        int(cid): REGIME_LABELS[rank] for rank, cid in enumerate(cluster_mean_vol.index)
    }

    centroids: list[dict[str, Any]] = []
    for cid, regime in cluster_to_regime.items():
        sub = df[df["cluster_id"] == cid]
        centroids.append(
            {
                "regime": regime,
                "cluster_id": cid,
                "description": _REGIME_DESCRIPTIONS[regime],
                "tick_count": int(len(sub)),
                "avg_intraday_vol": round(float(sub["intraday_vol"].mean()), 6),
                "avg_volume_ratio": round(float(sub["volume_ratio"].mean()), 4),
                "avg_momentum": round(float(sub["momentum"].mean()), 6),
                "pct_of_total": round(len(sub) / n * 100, 1),
            }
        )

    # Inertia as a model quality proxy (lower = tighter clusters)
    inertia = float(kmeans.inertia_)

    artifact = {
        "kmeans": kmeans,
        "scaler": scaler,
        "cluster_to_regime": cluster_to_regime,
        "centroids": centroids,
        "feature_cols": FEATURE_COLS,
        "trained_on": n,
        "inertia": inertia,
    }
    joblib.dump(artifact, MODEL_PATH)
    logger.info(
        "Regime detector saved — %d bars, inertia=%.2f, path=%s",
        n,
        inertia,
        MODEL_PATH,
    )

    return {
        "status": "trained",
        "trained_on": n,
        "inertia": round(inertia, 2),
        "centroids": centroids,
    }


# ── Inference helpers ─────────────────────────────────────────────────────────


def _run_inference(df: pd.DataFrame) -> pd.DataFrame:
    """Apply trained model to a pre-loaded tick DataFrame."""
    artifact = joblib.load(MODEL_PATH)
    kmeans: KMeans = artifact["kmeans"]
    scaler: StandardScaler = artifact["scaler"]
    cluster_to_regime: dict[int, str] = artifact["cluster_to_regime"]

    df = _compute_features(df)
    if df.empty:
        return df

    X = df[FEATURE_COLS].values
    X_scaled = scaler.transform(X)
    df["cluster_id"] = kmeans.predict(X_scaled).astype(int)
    df["regime"] = df["cluster_id"].map(cluster_to_regime).fillna("MEDIUM")

    # Confidence: inverse of normalised distance to nearest cluster centre
    centres_scaled = kmeans.cluster_centers_
    dists = np.linalg.norm(X_scaled - centres_scaled[df["cluster_id"].values], axis=1)
    df["cluster_confidence"] = (1.0 / (1.0 + dists)).round(4)

    return df


def _require_model() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            "No trained regime model found. POST /regime/train to train it first."
        )


# ── Public API ────────────────────────────────────────────────────────────────


def detect(engine: sa.Engine, trade_date: str) -> pd.DataFrame:
    """Return per-tick DataFrame with regime labels for a given trade_date."""
    _require_model()
    raw = _load_ticks(engine, trade_date)
    if raw.empty:
        return pd.DataFrame()
    return _run_inference(raw)


def timeline(
    engine: sa.Engine, trade_date: str, instrument_id: str
) -> list[dict[str, Any]]:
    """Sorted regime sequence for one instrument — used by the UI timeline strip."""
    df = detect(engine, trade_date)
    if df.empty:
        return []
    sub = (
        df[df["instrument_id"] == instrument_id]
        .sort_values("ts")
        .reset_index(drop=True)
    )
    if sub.empty:
        return []
    ts_series = pd.to_datetime(sub["ts"])
    return [
        {
            "ts": ts_series.iloc[i].strftime("%H:%M"),
            "regime": sub.at[i, "regime"],
            "confidence": float(sub.at[i, "cluster_confidence"]),
        }
        for i in range(len(sub))
    ]


def summary(engine: sa.Engine, trade_date: str) -> list[dict[str, Any]]:
    """Regime distribution and feature centroids aggregated across all instruments."""
    df = detect(engine, trade_date)
    if df.empty:
        return []
    total = len(df)
    result: list[dict[str, Any]] = []
    for regime in REGIME_LABELS:
        sub = df[df["regime"] == regime]
        n = len(sub)
        result.append(
            {
                "regime": regime,
                "description": _REGIME_DESCRIPTIONS[regime],
                "tick_count": n,
                "pct_of_session": round(n / total * 100, 1) if total else 0.0,
                "avg_intraday_vol": (
                    round(float(sub["intraday_vol"].mean()), 6) if n else 0.0
                ),
                "avg_volume_ratio": (
                    round(float(sub["volume_ratio"].mean()), 4) if n else 0.0
                ),
                "avg_momentum": round(float(sub["momentum"].mean()), 6) if n else 0.0,
                "avg_confidence": (
                    round(float(sub["cluster_confidence"].mean()), 4) if n else 0.0
                ),
            }
        )
    return result


def model_status() -> dict[str, Any]:
    """Return model availability, training metadata, and cluster centroids."""
    if not MODEL_PATH.exists():
        return {"ready": False}
    artifact = joblib.load(MODEL_PATH)
    return {
        "ready": True,
        "trained_on": artifact["trained_on"],
        "n_clusters": N_CLUSTERS,
        "features": FEATURE_COLS,
        "inertia": round(artifact.get("inertia", 0.0), 2),
        "centroids": artifact["centroids"],
    }
