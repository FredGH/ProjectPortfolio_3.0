"""Integration tests for market_data_source — validates OHLCV bar generation."""

from __future__ import annotations

import unittest
from datetime import date

from ingestion.sources.market_data_source import _generate_bars, _N_BARS

_TRADE_DATE = date(2025, 1, 15)


class TestMarketDataSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bars = _generate_bars(
            trade_date=_TRADE_DATE,
            instrument_id="EQTY-001",
            instrument_class="equity",
            start_price=50.0,
            vol_daily=0.015,
        )

    def test_generates_correct_number_of_bars(self) -> None:
        self.assertEqual(len(self.bars), _N_BARS)

    def test_ohlcv_fields_present(self) -> None:
        required = {
            "instrument_id",
            "bar_start",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
        }
        for bar in self.bars:
            missing = required - set(bar.keys())
            self.assertFalse(missing)

    def test_high_gte_low(self) -> None:
        for bar in self.bars:
            self.assertGreaterEqual(bar["high"], bar["low"], f"bar {bar['bar_start']}")

    def test_close_within_high_low(self) -> None:
        for bar in self.bars:
            self.assertGreaterEqual(bar["close"], bar["low"])
            self.assertLessEqual(bar["close"], bar["high"])

    def test_prices_positive(self) -> None:
        for bar in self.bars:
            for field in ("open", "high", "low", "close", "vwap"):
                self.assertGreater(
                    bar[field], 0, f"{field} non-positive at {bar['bar_start']}"
                )

    def test_volume_positive(self) -> None:
        for bar in self.bars:
            self.assertGreater(bar["volume"], 0)

    def test_bars_chronological(self) -> None:
        timestamps = [b["bar_start"] for b in self.bars]
        self.assertEqual(timestamps, sorted(timestamps))


if __name__ == "__main__":
    unittest.main()
