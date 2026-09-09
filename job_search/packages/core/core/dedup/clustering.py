"""Generic union-find (disjoint-set) over string keys — the connected
-components primitive PLAN.md Step 10 needs for transitive dedup
grouping (A~B, B~C => one cluster), which dbt cannot express directly.
"""

from __future__ import annotations


class UnionFind:
    """Disjoint-set over arbitrary string keys, with path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        """Return `key`'s current set representative, seeding it if new.

        Args:
            key: Any string identifier.

        Returns:
            The representative (root) of `key`'s set. A key seen for the
            first time is its own representative.
        """
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        """Merge `a` and `b`'s sets, if not already the same set.

        Args:
            a: One key.
            b: The other key.
        """
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Deterministic merge direction (lexicographically smaller root
        # wins) so `components()` never depends on call order — required
        # for the "run twice, byte-identical job_group_id" guarantee.
        if root_b < root_a:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a

    def components(self) -> dict[str, list[str]]:
        """Group every key seen so far (via `find` or `union`) by root.

        Returns:
            representative -> every key in its set, including
            singletons.
        """
        groups: dict[str, list[str]] = {}
        for key in self._parent:
            groups.setdefault(self.find(key), []).append(key)
        return groups
