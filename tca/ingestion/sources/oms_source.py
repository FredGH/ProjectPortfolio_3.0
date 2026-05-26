from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import dlt
import numpy as np
from faker import Faker

fake = Faker("en_GB")

COUNTERPARTIES = ["CP_ABCD", "CP_EFGH", "CP_IJKL", "CP_MNOP", "CP_QRST"]
TRADERS = [f"TRD-{i:03d}" for i in range(1, 11)]
ALGOS = ["VWAP", "TWAP", "IS", "POV", "SNIPER", "ARRIVAL", None]
ORDER_TYPES = ["MARKET", "LIMIT", "TWAP", "VWAP", "IS"]

INSTRUMENT_CONFIG: dict[str, dict] = {
    "equity": {
        "prefix": "EQTY",
        "count": 20,
        "price_range": (10.0, 150.0),
        "vol_daily": 0.015,
        "qty_range": (500, 50_000),
        "venues": ["XLON", "XETR", "XPAR", "XAMS", "BATE"],
        "currency": "EUR",
    },
    "equity_future": {
        "prefix": "FUTS",
        "count": 10,
        "price_range": (3_000.0, 6_000.0),
        "vol_daily": 0.012,
        "qty_range": (1, 500),
        "venues": ["XEUR"],
        "currency": "EUR",
    },
    "fixed_income": {
        "prefix": "BOND",
        "count": 10,
        "price_range": (95.0, 106.0),
        "vol_daily": 0.003,
        "qty_range": (1_000_000, 50_000_000),
        "venues": ["BLTX", "MFTR", "TRAX"],
        "currency": "EUR",
    },
    "fx_derivative": {
        "prefix": "FXFW",
        "count": 10,
        "price_range": (1.05, 1.25),
        "vol_daily": 0.005,
        "qty_range": (1_000_000, 20_000_000),
        "venues": ["MFTR", "GLMX", "FXALL"],
        "currency": "USD",
    },
}

# European session 08:00–16:30 CET = 07:00–15:30 UTC
_SESSION_START_MINUTES = 7 * 60       # 420 minutes from midnight
_SESSION_END_MINUTES = 15 * 60 + 30  # 930 minutes from midnight
_SESSION_DURATION = _SESSION_END_MINUTES - _SESSION_START_MINUTES  # 510 min


def _session_time(trade_date: date, rng: np.random.Generator) -> datetime:
    offset_min = int(rng.integers(0, _SESSION_DURATION))
    return datetime(
        trade_date.year, trade_date.month, trade_date.day,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=_SESSION_START_MINUTES + offset_min)


def _gbm_step(price: float, vol_daily: float, rng: np.random.Generator) -> float:
    dt = 1 / (252 * _SESSION_DURATION / 30)  # 30-second step
    drift = -0.5 * vol_daily**2 * dt
    diffusion = vol_daily * np.sqrt(dt) * rng.standard_normal()
    return float(price * np.exp(drift + diffusion))


def _split_quantity(total: int, n: int, rng: np.random.Generator) -> list[int]:
    fractions = rng.dirichlet(np.ones(n))
    parts = (fractions * total).astype(int)
    parts[-1] = total - int(parts[:-1].sum())
    return [int(q) for q in parts if q > 0]


def _generate_oms_data(
    trade_date: date, orders_per_class: int = 100
) -> tuple[list[dict], list[dict]]:
    date_seed = trade_date.toordinal()
    rng = np.random.default_rng(seed=date_seed)
    Faker.seed(date_seed)

    orders: list[dict] = []
    fills: list[dict] = []
    loaded_at = datetime.now(tz=timezone.utc)

    for asset_class, cfg in INSTRUMENT_CONFIG.items():
        instrument_ids = [f"{cfg['prefix']}-{i:03d}" for i in range(1, cfg["count"] + 1)]
        qty_low, qty_high = cfg["qty_range"]
        p_low, p_high = cfg["price_range"]

        for _ in range(orders_per_class):
            order_id = str(uuid.uuid4())
            instrument_id = str(rng.choice(instrument_ids))
            side = str(rng.choice(["BUY", "SELL"]))
            order_type = str(rng.choice(ORDER_TYPES))
            quantity = int(rng.integers(qty_low, qty_high))
            arrival_price = round(float(rng.uniform(p_low, p_high)), 6)
            limit_price: float | None = (
                round(arrival_price * float(1 + rng.uniform(-0.005, 0.005)), 6)
                if order_type == "LIMIT"
                else None
            )
            order_time = _session_time(trade_date, rng)
            counterparty_id = str(rng.choice(COUNTERPARTIES))
            trader_id = str(rng.choice(TRADERS))
            algo_choice = ALGOS[int(rng.integers(0, len(ALGOS)))]
            algo_id: str | None = algo_choice
            venue_id = str(rng.choice(cfg["venues"]))

            orders.append({
                "order_id": order_id,
                "instrument_id": instrument_id,
                "instrument_class": asset_class,
                "side": side,
                "order_type": order_type,
                "quantity": quantity,
                "arrival_price": arrival_price,
                "limit_price": limit_price,
                "order_time": order_time,
                "counterparty_id": counterparty_id,
                "trader_id": trader_id,
                "algo_id": algo_id,
                "venue_id": venue_id,
                "currency": cfg["currency"],
                "status": "FILLED",
                "client_order_id": fake.bothify(text="ORD-########"),
                "_loaded_at": loaded_at,
            })

            n_fills = int(rng.integers(1, 6))
            fill_quantities = _split_quantity(quantity, n_fills, rng)
            current_price = arrival_price
            filled_qty = 0

            for fill_qty in fill_quantities:
                if fill_qty <= 0:
                    continue
                current_price = _gbm_step(current_price, cfg["vol_daily"], rng)
                fill_time = order_time + timedelta(seconds=int(rng.integers(30, 1800)))
                market_impact = abs(current_price - arrival_price) / arrival_price * 10_000

                fills.append({
                    "fill_id": str(uuid.uuid4()),
                    "order_id": order_id,
                    "counterparty_id": counterparty_id,
                    "instrument_id": instrument_id,
                    "instrument_class": asset_class,
                    "venue_id": venue_id,
                    "fill_time": fill_time,
                    "fill_price": round(current_price, 6),
                    "fill_quantity": fill_qty,
                    "side": side,
                    "market_impact_bps": round(market_impact, 4),
                    "commission_bps": round(float(rng.uniform(0.5, 3.0)), 2),
                    "currency": cfg["currency"],
                    "_loaded_at": loaded_at,
                })
                filled_qty += fill_qty

    return orders, fills


@dlt.source(name="oms")
def oms_source(trade_date: date | None = None) -> Iterator:
    if trade_date is None:
        trade_date = date(2025, 1, 15)

    raw_orders, raw_fills = _generate_oms_data(trade_date)

    @dlt.resource(name="orders", write_disposition="merge", primary_key="order_id")
    def orders() -> Iterator[dict]:
        yield from raw_orders

    @dlt.resource(name="fills", write_disposition="merge", primary_key="fill_id")
    def fills() -> Iterator[dict]:
        yield from raw_fills

    return orders, fills
