"""Load the ESCO bulk CSV release into the `esco` schema (Step 14).

Reads `skills_en.csv`, `occupations_en.csv` and
`occupationSkillRelations_en.csv` (English release) from a directory the
user downloaded from the ESCO portal. Idempotent: upserts on the primary
keys, and replaces the label/relation rows of only the skills and
occupations present in the release, so re-loading a newer release drops
labels ESCO removed without touching anything else in the schema.

Column names are validated up front — if a future release renames one,
the loader fails naming the missing column rather than loading garbage.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, Engine, TextClause, text

from core.skills.normalise import normalise_skill

_SKILLS_FILE = "skills_en.csv"
_OCCUPATIONS_FILE = "occupations_en.csv"
_RELATIONS_FILE = "occupationSkillRelations_en.csv"

_REQUIRED_COLUMNS = {
    _SKILLS_FILE: {
        "conceptUri",
        "skillType",
        "reuseLevel",
        "preferredLabel",
        "altLabels",
        "hiddenLabels",
        "description",
    },
    _OCCUPATIONS_FILE: {"conceptUri", "preferredLabel", "description"},
    _RELATIONS_FILE: {"occupationUri", "relationType", "skillUri"},
}


class EscoLoadError(Exception):
    """Raised when the ESCO release directory is missing a file, a column or rows."""


@dataclass(frozen=True)
class EscoLoadCounts:
    """Row counts written by one `load_esco` run.

    Attributes:
        skills: Distinct skills upserted (a concept id repeated in the file
            counts once).
        skill_labels: Label rows written (preferred + alt + hidden).
        occupations: Occupations upserted.
        occupation_skills: Occupation-skill relations written.
        skipped_relations: Relations skipped because their skill or
            occupation is not in this release.
        duplicate_skill_ids: Concept ids that appear on more than one row of
            `skills_en.csv`, sorted. For each, the last row supplies the skill
            record and the labels of every row are merged.
        differing_duplicate_skill_ids: The subset of `duplicate_skill_ids`
            whose rows are not identical, so a real choice was made.
    """

    skills: int
    skill_labels: int
    occupations: int
    occupation_skills: int
    skipped_relations: int
    duplicate_skill_ids: tuple[str, ...] = ()
    differing_duplicate_skill_ids: tuple[str, ...] = ()


def concept_id(concept_uri: str) -> str:
    """Return the identifier part of an ESCO concept URI.

    Args:
        concept_uri: e.g. ``http://data.europa.eu/esco/skill/<uuid>``.

    Returns:
        The trailing path segment (the UUID).
    """
    return concept_uri.rstrip("/").rsplit("/", 1)[-1]


def _read_rows(directory: Path, filename: str) -> list[dict[str, str]]:
    """Read one release CSV, validating that it exists and has its columns.

    Args:
        directory: The release directory.
        filename: Which file to read.

    Returns:
        The rows as dicts keyed by header name.

    Raises:
        EscoLoadError: If the file is missing, lacks a required column, or has
            a header but no data rows (loading it would replace nothing and
            look like a success).
    """
    path = directory / filename
    if not path.is_file():
        raise EscoLoadError(f"missing ESCO file: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = _REQUIRED_COLUMNS[filename] - set(reader.fieldnames or [])
        if missing:
            columns = ", ".join(sorted(missing))
            raise EscoLoadError(f"{filename} is missing required column(s): {columns}")
        rows = list(reader)
    if not rows:
        raise EscoLoadError(f"{filename} has a header but no data rows: {path}")
    return rows


def _split_labels(cell: str) -> list[str]:
    """Split a newline-separated ESCO label cell.

    Args:
        cell: The raw `altLabels`/`hiddenLabels` cell.

    Returns:
        The non-empty, stripped labels.
    """
    return [line.strip() for line in cell.splitlines() if line.strip()]


def _labels_for(row: dict[str, str]) -> dict[str, tuple[str, bool]]:
    """Collect one skill's labels, keyed by normalised form.

    Args:
        row: A `skills_en.csv` row.

    Returns:
        Map of `label_norm` to `(original label, is_preferred)`, with
        duplicates (after normalisation) collapsed and `is_preferred`
        true if any duplicate was the preferred label.
    """
    candidates = [(row["preferredLabel"].strip(), True)]
    candidates += [(label, False) for label in _split_labels(row["altLabels"])]
    candidates += [(label, False) for label in _split_labels(row["hiddenLabels"])]
    collected: dict[str, tuple[str, bool]] = {}
    for label, is_preferred in candidates:
        norm = normalise_skill(label)
        if not norm:
            continue
        previous = collected.get(norm)
        collected[norm] = (
            previous[0] if previous else label,
            is_preferred or (previous[1] if previous else False),
        )
    return collected


def _skill_content_key(row: dict[str, str]) -> tuple:
    """Reduce a skills row to the fields that make two rows "the same".

    Args:
        row: A `skills_en.csv` row.

    Returns:
        A comparable key over the preferred label, type, reuse level,
        description and the sets of alternative and hidden labels.
    """
    return (
        row["preferredLabel"].strip(),
        row["skillType"],
        row["reuseLevel"],
        row["description"],
        frozenset(_split_labels(row["altLabels"])),
        frozenset(_split_labels(row["hiddenLabels"])),
    )


def _execute_many(conn: Connection, statement: TextClause, params: list[dict]) -> None:
    """Execute a statement once per parameter dict, skipping an empty list.

    Args:
        conn: An open connection.
        statement: The statement to run.
        params: Parameter dicts; nothing runs if empty.
    """
    if params:
        conn.execute(statement, params)


_UPSERT_SKILL = text(
    "INSERT INTO esco.skill (skill_id, concept_uri, preferred_label, skill_type, "
    "reuse_level, description) VALUES (:skill_id, :concept_uri, :preferred_label, "
    ":skill_type, :reuse_level, :description) "
    "ON CONFLICT (skill_id) DO UPDATE SET concept_uri = EXCLUDED.concept_uri, "
    "preferred_label = EXCLUDED.preferred_label, skill_type = EXCLUDED.skill_type, "
    "reuse_level = EXCLUDED.reuse_level, description = EXCLUDED.description"
)
_INSERT_LABEL = text(
    "INSERT INTO esco.skill_label (skill_id, label, label_norm, is_preferred) "
    "VALUES (:skill_id, :label, :label_norm, :is_preferred) "
    "ON CONFLICT (label_norm, skill_id) DO NOTHING"
)
_UPSERT_OCCUPATION = text(
    "INSERT INTO esco.occupation (occupation_id, concept_uri, preferred_label, "
    "description) VALUES (:occupation_id, :concept_uri, :preferred_label, "
    ":description) ON CONFLICT (occupation_id) DO UPDATE SET "
    "concept_uri = EXCLUDED.concept_uri, "
    "preferred_label = EXCLUDED.preferred_label, "
    "description = EXCLUDED.description"
)
_INSERT_RELATION = text(
    "INSERT INTO esco.occupation_skill (occupation_id, skill_id, relation_type) "
    "VALUES (:occupation_id, :skill_id, :relation_type) ON CONFLICT DO NOTHING"
)


def load_esco(engine: Engine, directory: Path) -> EscoLoadCounts:
    """Load an ESCO English CSV release into the `esco` schema.

    Args:
        engine: The owner-role engine (the `esco` schema is loader-owned).
        directory: Directory containing the release's CSV files.

    Returns:
        The row counts written, and the skill concept ids the file repeats.

    Raises:
        EscoLoadError: If a required file or column is missing, or a file has
            no data rows.
    """
    skills = _read_rows(directory, _SKILLS_FILE)
    occupations = _read_rows(directory, _OCCUPATIONS_FILE)
    relations = _read_rows(directory, _RELATIONS_FILE)

    # A concept id may repeat in the release. The last row supplies the skill
    # record; the labels of every row are merged, with only the last row's
    # preferred label flagged preferred. Repeats are reported, not dropped
    # silently.
    rows_by_skill: dict[str, list[dict[str, str]]] = {}
    for row in skills:
        rows_by_skill.setdefault(concept_id(row["conceptUri"]), []).append(row)
    duplicate_ids = tuple(
        sorted(i for i, rows in rows_by_skill.items() if len(rows) > 1)
    )
    differing_ids = tuple(
        i
        for i in duplicate_ids
        if len({_skill_content_key(r) for r in rows_by_skill[i]}) > 1
    )

    skill_params: list[dict] = []
    label_params: list[dict] = []
    for skill_id, rows in rows_by_skill.items():
        row = rows[-1]
        skill_params.append(
            {
                "skill_id": skill_id,
                "concept_uri": row["conceptUri"],
                "preferred_label": row["preferredLabel"].strip(),
                "skill_type": row["skillType"] or None,
                "reuse_level": row["reuseLevel"] or None,
                "description": row["description"] or None,
            }
        )
        merged: dict[str, tuple[str, bool]] = {}
        for earlier in rows[:-1]:
            for norm, (label, _) in _labels_for(earlier).items():
                merged.setdefault(norm, (label, False))
        merged.update(_labels_for(row))
        for norm, (label, is_preferred) in merged.items():
            label_params.append(
                {
                    "skill_id": skill_id,
                    "label": label,
                    "label_norm": norm,
                    "is_preferred": is_preferred,
                }
            )

    occupation_params: list[dict] = [
        {
            "occupation_id": concept_id(row["conceptUri"]),
            "concept_uri": row["conceptUri"],
            "preferred_label": row["preferredLabel"].strip(),
            "description": row["description"] or None,
        }
        for row in occupations
    ]
    skill_ids = {p["skill_id"] for p in skill_params}
    occupation_ids = {p["occupation_id"] for p in occupation_params}

    relation_params: list[dict] = []
    skipped = 0
    for row in relations:
        occupation_id = concept_id(row["occupationUri"])
        skill_id = concept_id(row["skillUri"])
        if occupation_id not in occupation_ids or skill_id not in skill_ids:
            skipped += 1
            continue
        relation_params.append(
            {
                "occupation_id": occupation_id,
                "skill_id": skill_id,
                "relation_type": row["relationType"],
            }
        )

    with engine.begin() as conn:
        _execute_many(conn, _UPSERT_SKILL, skill_params)
        conn.execute(
            text("DELETE FROM esco.skill_label WHERE skill_id = ANY(:ids)"),
            {"ids": sorted(skill_ids)},
        )
        _execute_many(conn, _INSERT_LABEL, label_params)
        _execute_many(conn, _UPSERT_OCCUPATION, occupation_params)
        conn.execute(
            text("DELETE FROM esco.occupation_skill WHERE occupation_id = ANY(:ids)"),
            {"ids": sorted(occupation_ids)},
        )
        _execute_many(conn, _INSERT_RELATION, relation_params)

    return EscoLoadCounts(
        skills=len(skill_params),
        skill_labels=len(label_params),
        occupations=len(occupation_params),
        occupation_skills=len(relation_params),
        skipped_relations=skipped,
        duplicate_skill_ids=duplicate_ids,
        differing_duplicate_skill_ids=differing_ids,
    )
