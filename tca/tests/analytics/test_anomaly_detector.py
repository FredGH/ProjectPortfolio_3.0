"""Unit tests for the anomaly detector — Z-score logic and quarantine routing."""
from __future__ import annotations

import unittest


class TestAnomalyDetector(unittest.TestCase):
    def _make_detector(self) -> object:
        from analytics.observability.anomaly_detector import AnomalyDetector
        return AnomalyDetector(z_threshold=3.0, min_history=5)

    def test_no_anomaly_for_normal_values(self) -> None:
        detector = self._make_detector()
        values = [10.0, 11.0, 10.5, 9.8, 10.2, 10.1]  # last is normal
        result = detector.check_zscore(values)
        self.assertFalse(result.is_anomaly)

    def test_detects_extreme_outlier(self) -> None:
        detector = self._make_detector()
        values = [10.0, 10.1, 10.0, 9.9, 10.0, 500.0]  # last is extreme
        result = detector.check_zscore(values)
        self.assertTrue(result.is_anomaly)

    def test_requires_minimum_history(self) -> None:
        detector = self._make_detector()
        values = [10.0, 11.0, 10.5]  # only 3 points — below min_history=5
        result = detector.check_zscore(values)
        self.assertFalse(result.is_anomaly, "Should not flag with insufficient history")

    def test_anomaly_result_includes_zscore(self) -> None:
        detector = self._make_detector()
        values = [10.0, 10.1, 10.0, 9.9, 10.0, 500.0]
        result = detector.check_zscore(values)
        self.assertIsNotNone(result.z_score)
        self.assertGreater(result.z_score, 3.0)

    def test_negative_outlier_detected(self) -> None:
        detector = self._make_detector()
        values = [100.0, 99.0, 101.0, 100.5, 99.5, -200.0]
        result = detector.check_zscore(values)
        self.assertTrue(result.is_anomaly)


if __name__ == "__main__":
    unittest.main()
