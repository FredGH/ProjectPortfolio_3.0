from __future__ import annotations

import unittest

from core.dedup.identity_assignment import (
    ClusterEdge,
    assign_new_job_groups,
    build_edges_from_exact_duplicates,
    build_edges_from_similarity_scores,
    build_manual_match_edges,
    compute_representatives,
    exclude_labeled_pairs,
)


class TestBuildEdgesFromExactDuplicates(unittest.TestCase):
    def test_chains_a_three_way_group_into_two_edges(self) -> None:
        rows = [
            ("a", "url_canonical", "same-url"),
            ("b", "url_canonical", "same-url"),
            ("c", "url_canonical", "same-url"),
        ]
        edges = build_edges_from_exact_duplicates(rows)
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(e.match_method == "exact" for e in edges))
        self.assertTrue(all(e.confidence == 1.0 for e in edges))

    def test_singleton_group_produces_no_edge(self) -> None:
        rows = [("a", "url_canonical", "unique-url")]
        self.assertEqual(build_edges_from_exact_duplicates(rows), [])

    def test_different_duplicate_types_do_not_cross_link(self) -> None:
        rows = [
            ("a", "url_canonical", "x"),
            ("b", "content_hash", "x"),
        ]
        self.assertEqual(build_edges_from_exact_duplicates(rows), [])


class TestBuildEdgesFromSimilarityScores(unittest.TestCase):
    def test_row_at_or_above_threshold_becomes_an_edge(self) -> None:
        rows = [("a", "b", 0.9, False)]
        edges = build_edges_from_similarity_scores(rows, threshold=0.8)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].match_method, "fuzzy")
        self.assertEqual(edges[0].confidence, 0.9)

    def test_row_below_threshold_is_dropped(self) -> None:
        rows = [("a", "b", 0.5, False)]
        self.assertEqual(build_edges_from_similarity_scores(rows, threshold=0.8), [])

    def test_hard_veto_row_is_dropped_even_above_threshold(self) -> None:
        rows = [("a", "b", 0.95, True)]
        self.assertEqual(build_edges_from_similarity_scores(rows, threshold=0.8), [])


class TestBuildManualMatchEdges(unittest.TestCase):
    def test_match_label_becomes_a_manual_edge(self) -> None:
        rows = [("a", "b", "match")]
        edges = build_manual_match_edges(rows)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].match_method, "manual")
        self.assertEqual(edges[0].confidence, 1.0)

    def test_not_match_label_produces_no_edge(self) -> None:
        rows = [("a", "b", "not_match")]
        self.assertEqual(build_manual_match_edges(rows), [])


class TestExcludeLabeledPairs(unittest.TestCase):
    def test_labeled_pair_is_removed_regardless_of_score(self) -> None:
        edges = [
            ClusterEdge("a", "b", 0.95, "fuzzy"),
            ClusterEdge("c", "d", 0.95, "fuzzy"),
        ]
        # A human said "not_match" for a/b despite its high score — Step
        # 9's review queue exists precisely to catch cases like this.
        result = exclude_labeled_pairs(edges, labeled_pairs={("a", "b")})
        self.assertEqual(result, [edges[1]])

    def test_pair_order_does_not_matter(self) -> None:
        edges = [ClusterEdge("a", "b", 0.95, "fuzzy")]
        result = exclude_labeled_pairs(edges, labeled_pairs={("b", "a")})
        self.assertEqual(result, [])

    def test_unlabeled_pairs_pass_through_unchanged(self) -> None:
        edges = [ClusterEdge("a", "b", 0.95, "fuzzy")]
        self.assertEqual(exclude_labeled_pairs(edges, labeled_pairs=set()), edges)


class TestComputeRepresentatives(unittest.TestCase):
    def test_picks_the_lexicographically_smallest_member(self) -> None:
        assigned = {"bravo": "group-1", "alpha": "group-1", "zulu": "group-2"}
        self.assertEqual(
            compute_representatives(assigned),
            {"group-1": "alpha", "group-2": "zulu"},
        )


class TestAssignNewJobGroups(unittest.TestCase):
    def _ids(self) -> object:
        counter = iter(f"new-group-{i}" for i in range(100))
        return lambda: next(counter)

    def test_singleton_with_no_edges_gets_its_own_group(self) -> None:
        result = assign_new_job_groups(
            all_job_keys=["a"], edges=[], assigned={}, new_group_id=self._ids()
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job_key, "a")
        self.assertEqual(result[0].match_method, "singleton")
        self.assertEqual(result[0].confidence, 1.0)
        self.assertFalse(result[0].is_manual_override)

    def test_two_new_jobs_matching_each_other_share_a_fresh_group(self) -> None:
        edges = [ClusterEdge("a", "b", 0.9, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned={}, new_group_id=self._ids()
        )
        group_ids = {r.job_key: r.job_group_id for r in result}
        self.assertEqual(group_ids["a"], group_ids["b"])

    def test_new_job_matching_an_existing_representative_joins_that_group(self) -> None:
        assigned = {"rep": "existing-group"}
        edges = [ClusterEdge("new", "rep", 0.85, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["new"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].job_group_id, "existing-group")
        self.assertEqual(result[0].match_method, "fuzzy")
        self.assertEqual(result[0].confidence, 0.85)

    def test_manual_edge_wins_even_below_typical_thresholds_and_flags_override(
        self,
    ) -> None:
        # A human confirmed this pair matches even though its own
        # blended_score (not modeled here) may sit under the auto-match
        # threshold — the manual edge is the only signal this function
        # ever sees for this pair, at confidence 1.0.
        edges = [ClusterEdge("a", "b", 1.0, "manual")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned={}, new_group_id=self._ids()
        )
        self.assertTrue(all(r.match_method == "manual" for r in result))
        self.assertTrue(all(r.is_manual_override for r in result))

    def test_already_assigned_job_is_never_reassigned(self) -> None:
        assigned = {"old": "existing-group"}
        result = assign_new_job_groups(
            all_job_keys=["old"], edges=[], assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(result, [])

    def test_new_component_bridging_to_one_existing_group_all_join_it(self) -> None:
        # a-b are new and match each other; a also matches an existing
        # representative. Both a and b must land in the existing group.
        assigned = {"rep": "existing-group"}
        edges = [
            ClusterEdge("a", "b", 0.9, "fuzzy"),
            ClusterEdge("a", "rep", 0.85, "fuzzy"),
        ]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        group_ids = {r.job_key: r.job_group_id for r in result}
        self.assertEqual(group_ids["a"], "existing-group")
        self.assertEqual(group_ids["b"], "existing-group")

    def test_rerun_with_everything_already_assigned_is_a_no_op(self) -> None:
        assigned = {"a": "group-1", "b": "group-1"}
        edges = [ClusterEdge("a", "b", 0.9, "fuzzy")]
        result = assign_new_job_groups(
            all_job_keys=["a", "b"], edges=edges, assigned=assigned, new_group_id=self._ids()
        )
        self.assertEqual(result, [])

    def test_manual_edge_beats_equal_confidence_exact_edge_in_tie_break(
        self,
    ) -> None:
        # Reproduces a live-data bug: job_key "b" has an exact
        # content-hash duplicate edge to "c" AND is the manually-labeled
        # partner of "a" (a human confirmed a/b as 'match' in
        # dedup.pair_labels). Both edges carry confidence=1.0. Because
        # 'manual' means a human vouched for this specific job's
        # assignment, it must win the tie over an automatic 'exact'
        # edge — is_manual_override must be True so an audit query
        # never silently misses this row.
        edges = [
            ClusterEdge("b", "c", 1.0, "exact"),
            ClusterEdge("a", "b", 1.0, "manual"),
        ]
        result = assign_new_job_groups(
            all_job_keys=["a", "b", "c"],
            edges=edges,
            assigned={},
            new_group_id=self._ids(),
        )
        by_key = {r.job_key: r for r in result}
        self.assertEqual(by_key["b"].match_method, "manual")
        self.assertTrue(by_key["b"].is_manual_override)
