from __future__ import annotations

import unittest

from core.evals.metrics import exact_match, field_f1


class TestExactMatch(unittest.TestCase):
    def test_identical_dicts_score_one(self) -> None:
        self.assertEqual(
            exact_match({"category": "data_engineer"}, {"category": "data_engineer"}),
            1.0,
        )

    def test_differing_dicts_score_zero(self) -> None:
        self.assertEqual(
            exact_match({"category": "data_engineer"}, {"category": "other"}), 0.0
        )

    def test_extra_predicted_keys_are_ignored(self) -> None:
        # Only the fields present in `expected` are compared — a
        # predictor returning extra metadata fields must not be
        # penalised for it.
        predicted = {"category": "data_engineer", "confidence": 0.9}
        expected = {"category": "data_engineer"}
        self.assertEqual(exact_match(predicted, expected), 1.0)

    def test_missing_expected_key_scores_zero(self) -> None:
        self.assertEqual(exact_match({}, {"category": "data_engineer"}), 0.0)


class TestFieldF1(unittest.TestCase):
    def test_all_fields_match_scores_one(self) -> None:
        predicted = {"skill": "python", "years": 5}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 1.0)

    def test_no_fields_match_scores_zero(self) -> None:
        predicted = {"skill": "java", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertEqual(field_f1(predicted, expected), 0.0)

    def test_partial_match_scores_between_zero_and_one(self) -> None:
        # 1 of 2 fields correct, no extra predicted fields:
        # precision = 1/2, recall = 1/2, f1 = 0.5
        predicted = {"skill": "python", "years": 2}
        expected = {"skill": "python", "years": 5}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.5)

    def test_extra_predicted_fields_reduce_precision(self) -> None:
        # 1 correct field out of 2 predicted, 1 expected field total:
        # precision = 1/2, recall = 1/1, f1 = 2*(0.5*1)/(0.5+1) = 0.667
        predicted = {"skill": "python", "spurious": "x"}
        expected = {"skill": "python"}
        self.assertAlmostEqual(field_f1(predicted, expected), 0.6666666666666666)

    def test_both_empty_scores_one(self) -> None:
        # No fields expected, none predicted — trivially correct rather
        # than a division-by-zero.
        self.assertEqual(field_f1({}, {}), 1.0)


if __name__ == "__main__":
    unittest.main()
