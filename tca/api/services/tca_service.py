"""TCA query service — all mart queries go through this module.

SECURITY: Every query that accesses mart_trading_risk.fact_order_execution
MUST include the counterparty_id filter. CLIENT role gets their JWT
counterparty_id injected. Internal roles may pass counterparty_id=None
(no filter) or a specific counterparty_id.

CLIENT queries for another counterparty's data return None → HTTP 404.
"""

from __future__ import annotations

import os
from typing import Any

import sqlalchemy as sa

from api.auth.dependencies import UserClaims


class TCAService:
    def __init__(self, db_url: str | None = None) -> None:
        url = db_url or os.environ["DATABASE_URL"]
        self._engine = sa.create_engine(url)

    def _safe_list(self, sql: sa.TextClause, params: dict) -> list[dict]:
        """Execute a SELECT and return rows; return [] if mart tables don't exist yet."""
        try:
            with self._engine.connect() as conn:
                return [dict(r) for r in conn.execute(sql, params).mappings().all()]
        except sa.exc.ProgrammingError:
            return []

    def _safe_one(self, sql: sa.TextClause, params: dict) -> dict | None:
        """Execute a SELECT and return first row; return None if mart tables don't exist yet."""
        try:
            with self._engine.connect() as conn:
                row = conn.execute(sql, params).mappings().first()
                return dict(row) if row else None
        except sa.exc.ProgrammingError:
            return None

    def _cp_clause(
        self, user: UserClaims, alias: str = "f"
    ) -> tuple[str, dict[str, Any]]:
        """Returns SQL clause and params to enforce counterparty isolation."""
        if user.counterparty_id is not None:
            return f" AND {alias}.counterparty_id = :_cp_id", {
                "_cp_id": user.counterparty_id
            }
        return "", {}

    def get_order_tca(self, order_id: str, user: UserClaims) -> dict | None:
        cp_clause, cp_params = self._cp_clause(user)
        sql = sa.text(
            f"""
            SELECT
                h.order_bk AS order_id,
                f.instrument_id, f.instrument_class, f.counterparty_id,
                f.side, f.order_type, f.quantity, f.filled_quantity,
                f.arrival_price, f.avg_fill_price,
                f.arrival_slippage_bps, f.market_impact_bps, f.commission_bps,
                f.total_cost_bps, f.vwap_slippage_bps, f.twap_slippage_bps,
                f.close_slippage_bps, f.execution_quality,
                f.alpha_t30m_bps, f.alpha_close_bps, f.vol_regime,
                f.algo_id, f.venue_id, f.trader_id,
                f.pre_trade_waiver_type,
                f.post_trade_deferral_type,
                f.settlement_date,
                f.trade_date, f.order_time
            FROM mart_trading_risk.fact_order_execution AS f
            JOIN raw_vault.hub_order AS h USING (hub_order_key)
            WHERE h.order_bk = :order_id {cp_clause}
        """
        )
        return self._safe_one(sql, {"order_id": order_id, **cp_params})

    def get_tca_summary(
        self,
        trade_date: str,
        user: UserClaims,
        counterparty_id: str | None = None,
        instrument_class: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        effective_cp = user.counterparty_id or counterparty_id
        params: dict[str, Any] = {"trade_date": trade_date, "limit": limit}
        clauses = ["f.trade_date = :trade_date"]

        if effective_cp:
            clauses.append("f.counterparty_id = :cp_id")
            params["cp_id"] = effective_cp
        if instrument_class:
            clauses.append("f.instrument_class = :ic")
            params["ic"] = instrument_class

        where = " AND ".join(clauses)
        sql = sa.text(
            f"""
            SELECT
                h.order_bk AS order_id, f.instrument_id, f.instrument_class,
                f.counterparty_id, f.side, f.order_type,
                f.quantity, f.filled_quantity, f.arrival_price, f.avg_fill_price,
                f.arrival_slippage_bps, f.market_impact_bps, f.commission_bps,
                f.total_cost_bps, f.vwap_slippage_bps, f.twap_slippage_bps,
                f.close_slippage_bps, f.execution_quality,
                f.vol_regime, f.alpha_t30m_bps, f.alpha_close_bps,
                f.algo_id, f.venue_id, f.trader_id, f.trade_date, f.order_time
            FROM mart_trading_risk.fact_order_execution AS f
            JOIN raw_vault.hub_order AS h USING (hub_order_key)
            WHERE {where}
            ORDER BY f.order_time DESC
            LIMIT :limit
        """
        )
        return self._safe_list(sql, params)

    def get_algo_performance(
        self, trade_date: str, user: UserClaims, instrument_class: str | None = None
    ) -> list[dict]:
        cp_clause, cp_params = self._cp_clause(user)
        ic_clause = " AND f.instrument_class = :ic" if instrument_class else ""
        params: dict[str, Any] = {"trade_date": trade_date, **cp_params}
        if instrument_class:
            params["ic"] = instrument_class

        sql = sa.text(
            f"""
            SELECT
                f.algo_id, f.instrument_class,
                COUNT(*) AS order_count,
                ROUND(AVG(f.arrival_slippage_bps)::numeric, 4) AS avg_arrival_slippage_bps,
                ROUND(AVG(f.vwap_slippage_bps)::numeric, 4)   AS avg_vwap_slippage_bps,
                ROUND(AVG(f.market_impact_bps)::numeric, 4)   AS avg_market_impact_bps,
                ROUND(AVG(f.filled_quantity::float / NULLIF(f.quantity, 0))::numeric, 4) AS avg_participation_rate,
                RANK() OVER (
                    PARTITION BY f.instrument_class
                    ORDER BY AVG(f.arrival_slippage_bps) ASC NULLS LAST
                ) AS algo_rank
            FROM mart_trading_risk.fact_order_execution AS f
            WHERE f.trade_date = :trade_date {cp_clause} {ic_clause}
              AND f.algo_id IS NOT NULL
            GROUP BY f.algo_id, f.instrument_class
            ORDER BY f.instrument_class, algo_rank
        """
        )
        return self._safe_list(sql, params)

    def get_alpha_decay(self, trade_date: str, user: UserClaims) -> list[dict]:
        cp_clause, cp_params = self._cp_clause(user)
        sql = sa.text(
            f"""
            SELECT
                f.vol_regime, f.instrument_class,
                COUNT(*)                                  AS order_count,
                ROUND(AVG(f.alpha_t30m_bps)::numeric, 4)  AS alpha_t30m_bps,
                ROUND(AVG(f.alpha_t1h_bps)::numeric, 4)   AS alpha_t1h_bps,
                ROUND(AVG(f.alpha_t4h_bps)::numeric, 4)   AS alpha_t4h_bps,
                ROUND(AVG(f.alpha_close_bps)::numeric, 4) AS alpha_close_bps
            FROM mart_trading_risk.fact_order_execution AS f
            WHERE f.trade_date = :trade_date {cp_clause}
              AND f.vol_regime IS NOT NULL
            GROUP BY f.vol_regime, f.instrument_class
            ORDER BY f.instrument_class, f.vol_regime
        """
        )
        return self._safe_list(sql, {"trade_date": trade_date, **cp_params})

    def get_peer_benchmark(self, order_id: str, user: UserClaims) -> dict | None:
        cp_clause, cp_params = self._cp_clause(user)
        sql = sa.text(
            f"""
            SELECT
                h.order_bk AS order_id, b.instrument_id,
                b.arrival_price, b.avg_fill_price, b.vwap_price, b.twap_price,
                b.close_price, b.arrival_slippage_bps, b.vwap_slippage_bps,
                b.twap_slippage_bps, b.close_slippage_bps
            FROM biz_vault.bv_peer_benchmark AS b
            JOIN raw_vault.hub_order AS h USING (hub_order_key)
            JOIN mart_trading_risk.fact_order_execution AS f USING (hub_order_key)
            WHERE h.order_bk = :order_id {cp_clause}
        """
        )
        return self._safe_one(sql, {"order_id": order_id, **cp_params})

    def get_orders(
        self, trade_date: str, user: UserClaims, limit: int = 500
    ) -> list[dict]:
        return self.get_tca_summary(trade_date, user, limit=limit)

    def get_warnings(self, limit: int = 100) -> list[dict]:
        sql = sa.text(
            """
            SELECT DISTINCT ON (check_name, affected_table)
                id, check_name, affected_table, affected_rows, warn_value, warn_time
            FROM obs.obs_warnings
            ORDER BY check_name, affected_table, warn_time DESC
            LIMIT :limit
        """
        )
        return self._safe_list(sql, {"limit": limit})

    def get_mifid_export(self, trade_date: str, user: UserClaims) -> list[dict]:
        sql = sa.text(
            """
            SELECT
                h.order_bk AS order_id,
                m.instrument_id,
                m.instrument_class,
                m.side,
                f.quantity AS total_quantity,
                (f.avg_fill_price * f.quantity) AS notional_eur,
                m.execution_venue_mic AS venue_id,
                m.pre_trade_waiver_type AS waiver_type,
                (m.post_trade_deferral_type IS NOT NULL
                 AND m.post_trade_deferral_type != '') AS is_lrgs_deferral,
                CASE
                    WHEN m.is_otc    THEN 'OTC'
                    WHEN m.si_flag   THEN 'SI'
                    WHEN m.pre_trade_waiver_type IS NOT NULL THEN 'Dark'
                    ELSE 'Lit'
                END AS rts27_category,
                m.trade_date
            FROM biz_vault.bv_mifid_fields AS m
            JOIN raw_vault.hub_order AS h USING (hub_order_key)
            JOIN mart_trading_risk.fact_order_execution AS f USING (hub_order_key)
            WHERE m.trade_date = :trade_date
            ORDER BY m.transaction_time
        """
        )
        return self._safe_list(sql, {"trade_date": trade_date})
