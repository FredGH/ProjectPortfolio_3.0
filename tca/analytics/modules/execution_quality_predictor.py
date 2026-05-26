"""Execution Quality Predictor — pre-trade arrival slippage estimation.

Trains one GradientBoostingRegressor per instrument class on historical
fact_order_execution data. Models are persisted to analytics/models/.

Usage:
    from analytics.modules.execution_quality_predictor import train, predict

    # Train (run after seed_history has populated enough data)
    results = train(engine)

    # Predict
    est = predict("equity", "BUY", 10000, "HIGH", "VWAP", "XLON", 10, 2)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sqlalchemy as sa
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

INSTRUMENT_CLASSES = ["equity", "equity_future", "fixed_income", "fx_derivative"]

CAT_COLS = ["side", "vol_regime", "algo_id", "venue_id"]
NUM_COLS = ["quantity", "hour_of_day", "day_of_week"]
ALL_FEATURES = CAT_COLS + NUM_COLS
TARGET = "arrival_slippage_bps"

MIN_SAMPLES = 100  # minimum per class to attempt training


def _load_training_data(engine: sa.Engine) -> pd.DataFrame:
    sql = sa.text("""
        SELECT
            instrument_class,
            side,
            quantity,
            COALESCE(vol_regime, 'MEDIUM')                    AS vol_regime,
            COALESCE(algo_id,    'UNKNOWN')                   AS algo_id,
            COALESCE(venue_id,   'UNKNOWN')                   AS venue_id,
            EXTRACT(HOUR FROM order_time)::int                AS hour_of_day,
            EXTRACT(DOW  FROM order_time)::int                AS day_of_week,
            arrival_slippage_bps
        FROM mart_trading_risk.fact_order_execution
        WHERE arrival_slippage_bps IS NOT NULL
          AND order_time IS NOT NULL
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)


def train(engine: sa.Engine) -> dict[str, Any]:
    """Train per-class models. Returns a summary dict with sample counts and CV R²."""
    df = _load_training_data(engine)
    total = len(df)
    logger.info("Loaded %d training rows from fact_order_execution.", total)

    summary: dict[str, Any] = {"total_rows": total, "models": {}}

    for cls in INSTRUMENT_CLASSES:
        subset = df[df["instrument_class"] == cls].copy()
        n = len(subset)

        if n < MIN_SAMPLES:
            logger.warning("Skipping %s — only %d rows (need %d).", cls, n, MIN_SAMPLES)
            summary["models"][cls] = {"status": "skipped", "samples": n}
            continue

        encoders: dict[str, LabelEncoder] = {}
        for col in CAT_COLS:
            le = LabelEncoder()
            subset[col] = le.fit_transform(subset[col].astype(str))
            encoders[col] = le

        X = subset[ALL_FEATURES]
        y = subset[TARGET]


        #param_grid = {
        #    "n_estimators": [100, 300, 500],
        #    "max_depth": [3, 4, 6],
        #    "learning_rate": [0.01, 0.05, 0.1],
        #}
        
        #search = GridSearchCV(
        #    GradientBoostingRegressor(subsample=0.8, random_state=42),
        #    param_grid,
        #    cv=cv_folds,
        #    scoring="r2",
        #)

        model = GradientBoostingRegressor(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )

        #9000 records -> 5 folds -> 450 records per fold 
        cv_folds = min(5, n // 20)
        #do not cross validate if not enough data
        if cv_folds >= 2:
            scores = cross_val_score(model, X, y, cv=cv_folds, scoring="r2")
            cv_r2_mean = float(scores.mean())
            cv_r2_std = float(scores.std())
        else:
            cv_r2_mean, cv_r2_std = float("nan"), float("nan")

        model.fit(X, y)

        # Store residual IQR for confidence interval display
        residuals = y - model.predict(X)
        iqr_low = float(np.percentile(residuals, 25))
        iqr_high = float(np.percentile(residuals, 75))

        artifact = {
            "model": model,
            "encoders": encoders,
            "iqr_low": iqr_low,
            "iqr_high": iqr_high,
            "feature_importance": dict(zip(ALL_FEATURES, model.feature_importances_.tolist())),
            "trained_on": n,
        }
        path = MODEL_DIR / f"slippage_{cls}.pkl"
        joblib.dump(artifact, path)
        logger.info("Saved model for %s (%d rows, CV R²=%.3f ± %.3f).", cls, n, cv_r2_mean, cv_r2_std)

        summary["models"][cls] = {
            "status": "trained",
            "samples": n,
            "cv_r2_mean": round(cv_r2_mean, 4),
            "cv_r2_std": round(cv_r2_std, 4),
        }

    return summary


def predict(
    instrument_class: str,
    side: str,
    quantity: int,
    vol_regime: str,
    algo_id: str,
    venue_id: str,
    order_hour: int,
    order_dow: int,
) -> dict[str, Any]:
    """Return predicted arrival slippage (bps) with a confidence interval."""
    path = MODEL_DIR / f"slippage_{instrument_class}.pkl"
    if not path.exists():
        return {"error": f"No trained model for '{instrument_class}'. Call POST /predict/train first."}

    artifact = joblib.load(path)
    model: GradientBoostingRegressor = artifact["model"]
    encoders: dict[str, LabelEncoder] = artifact["encoders"]

    row: dict[str, Any] = {
        "side":        side,
        "vol_regime":  vol_regime or "MEDIUM",
        "algo_id":     algo_id or "UNKNOWN",
        "venue_id":    venue_id or "UNKNOWN",
        "quantity":    quantity,
        "hour_of_day": order_hour,
        "day_of_week": order_dow,
    }

    for col in CAT_COLS:
        le: LabelEncoder = encoders[col]
        val = str(row[col])
        if val not in le.classes_:
            val = le.classes_[0]
        row[col] = int(le.transform([val])[0])

    X = pd.DataFrame([row])[ALL_FEATURES]
    pred = float(model.predict(X)[0])

    return {
        "instrument_class": instrument_class,
        "predicted_slippage_bps": round(pred, 2),
        "ci_low_bps": round(pred + artifact["iqr_low"], 2),
        "ci_high_bps": round(pred + artifact["iqr_high"], 2),
        "trained_on": artifact["trained_on"],
        "feature_importance": artifact["feature_importance"],
    }


def model_status() -> dict[str, Any]:
    """Return availability and metadata for all class models."""
    status = {}
    for cls in INSTRUMENT_CLASSES:
        path = MODEL_DIR / f"slippage_{cls}.pkl"
        if path.exists():
            artifact = joblib.load(path)
            status[cls] = {
                "ready": True,
                "trained_on": artifact["trained_on"],
                "top_features": sorted(
                    artifact["feature_importance"].items(),
                    key=lambda x: x[1], reverse=True
                )[:3],
            }
        else:
            status[cls] = {"ready": False}
    return status
