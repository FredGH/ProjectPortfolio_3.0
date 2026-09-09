from __future__ import annotations

import unittest

from core.dedup.clustering import UnionFind


class TestUnionFind(unittest.TestCase):
    def test_unseen_key_is_its_own_representative(self) -> None:
        uf = UnionFind()
        self.assertEqual(uf.find("a"), "a")

    def test_union_makes_two_keys_share_a_representative(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        self.assertEqual(uf.find("a"), uf.find("b"))

    def test_transitive_union_forms_one_component(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.union("b", "c")
        self.assertEqual(uf.find("a"), uf.find("c"))

    def test_union_is_deterministic_regardless_of_argument_order(self) -> None:
        uf_ab = UnionFind()
        uf_ab.union("a", "b")
        uf_ba = UnionFind()
        uf_ba.union("b", "a")
        self.assertEqual(uf_ab.find("a"), uf_ba.find("a"))

    def test_components_groups_every_seen_key(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.find("c")  # seen but never unioned — its own singleton
        components = uf.components()
        self.assertEqual(len(components), 2)
        group_sizes = sorted(len(members) for members in components.values())
        self.assertEqual(group_sizes, [1, 2])

    def test_union_of_already_joined_keys_is_a_no_op(self) -> None:
        uf = UnionFind()
        uf.union("a", "b")
        uf.union("a", "b")
        self.assertEqual(len(uf.components()), 1)
