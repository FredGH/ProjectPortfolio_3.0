"""Integration tests for oms_source — validates generated order/fill data."""

from __future__ import annotations

import unittest
from datetime import date

from ingestion.sources.oms_source import INSTRUMENT_CONFIG, _generate_oms_data

_TRADE_DATE = date(2025, 1, 15)
_ASSET_CLASSES = list(INSTRUMENT_CONFIG.keys())
_EXPECTED_ORDERS = 400


class TestOmsSourceGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.orders, cls.fills = _generate_oms_data(trade_date=_TRADE_DATE)

    def test_generates_exactly_400_orders(self) -> None:
        self.assertEqual(len(self.orders), _EXPECTED_ORDERS)

    def test_all_four_asset_classes_present(self) -> None:
        classes = {o["instrument_class"] for o in self.orders}
        self.assertEqual(classes, set(_ASSET_CLASSES))

    def test_each_asset_class_has_100_orders(self) -> None:
        from collections import Counter

        counts = Counter(o["instrument_class"] for o in self.orders)
        for cls_ in _ASSET_CLASSES:
            self.assertEqual(counts[cls_], 100, f"{cls_} != 100 orders")

    def test_order_required_fields_present(self) -> None:
        required = {
            "order_id",
            "instrument_id",
            "instrument_class",
            "counterparty_id",
            "side",
            "order_quantity",
            "arrival_price",
            "algo_id",
            "trader_id",
            "trade_date",
            "order_time",
        }
        for order in self.orders:
            missing = required - set(order.keys())
            self.assertFalse(
                missing, f"Order {order.get('order_id')} missing: {missing}"
            )

    def test_no_spot_fx_orders(self) -> None:
        classes = {o["instrument_class"] for o in self.orders}
        self.assertNotIn("fx_spot", classes)

    def test_fill_order_ids_are_subset_of_orders(self) -> None:
        order_ids = {o["order_id"] for o in self.orders}
        fill_order_ids = {f["order_id"] for f in self.fills}
        self.assertTrue(fill_order_ids.issubset(order_ids))

    def test_arrival_prices_positive(self) -> None:
        for order in self.orders:
            self.assertGreater(order["arrival_price"], 0, order["order_id"])

    def test_counterparty_ids_non_null(self) -> None:
        for order in self.orders:
            self.assertIsNotNone(order["counterparty_id"], order["order_id"])

    def test_sides_valid(self) -> None:
        for order in self.orders:
            self.assertIn(order["side"], ("BUY", "SELL"))


class TestOmsSourceReproducibility(unittest.TestCase):
    def test_same_seed_produces_same_orders(self) -> None:
        orders_a, _ = _generate_oms_data(trade_date=_TRADE_DATE)
        orders_b, _ = _generate_oms_data(trade_date=_TRADE_DATE)
        ids_a = [o["order_id"] for o in orders_a]
        ids_b = [o["order_id"] for o in orders_b]
        self.assertEqual(ids_a, ids_b)

    def test_different_dates_produce_different_data(self) -> None:
        orders_a, _ = _generate_oms_data(trade_date=date(2025, 1, 15))
        orders_b, _ = _generate_oms_data(trade_date=date(2025, 1, 16))
        prices_a = [o["arrival_price"] for o in orders_a]
        prices_b = [o["arrival_price"] for o in orders_b]
        self.assertNotEqual(prices_a, prices_b)


if __name__ == "__main__":
    unittest.main()
