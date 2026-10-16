"""A clearly labelled FAKE candidate so the review UI can be tried (Step 21b).

Real detection finds no candidate on a young corpus, so there is nothing to
click. `seed_fake_candidate` inserts one synthetic cluster run, cluster and
named candidate, every field marked FAKE, as the newest run; `remove_fake`
deletes it (and any review made on it), so the real run is the latest again.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import Engine, text

FAKE_MARK = "[FAKE - TEST ONLY]"
_PARAMS = {"fake": True, "note": "synthetic test data, not a finding"}


def remove_fake(engine: Engine) -> int:
    """Delete every fake run and what hangs off it.

    Args:
        engine: The OWNER engine (the app role cannot delete reviews).

    Returns:
        How many fake runs were removed.
    """
    with engine.begin() as conn:
        runs = [
            r.id
            for r in conn.execute(
                text(
                    "SELECT id FROM emergent.cluster_run WHERE params ->> 'fake' = "
                    "'true'"
                )
            )
        ]
        for run_id in runs:
            conn.execute(
                text(
                    "DELETE FROM emergent.emergent_role_review WHERE candidate_id IN "
                    "(SELECT id FROM emergent.role_candidate WHERE run_id = :r)"
                ),
                {"r": run_id},
            )
            for table in ("role_candidate", "title_cluster"):
                conn.execute(
                    text(f"DELETE FROM emergent.{table} WHERE run_id = :r"),
                    {"r": run_id},
                )
            conn.execute(
                text("DELETE FROM emergent.cluster_run WHERE id = :r"), {"r": run_id}
            )
    return len(runs)


def seed_fake_candidate(engine: Engine) -> uuid.UUID:
    """Insert one fake named candidate as the newest cluster run.

    Args:
        engine: The OWNER engine.

    Returns:
        The fake candidate's id. Any earlier fake run is replaced.
    """
    remove_fake(engine)
    titles = [
        f"{FAKE_MARK} forward deployed engineer",
        f"{FAKE_MARK} field deployed engineer",
        f"{FAKE_MARK} deployed solutions engineer",
    ]
    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                "INSERT INTO emergent.cluster_run (params, n_titles, n_clusters, "
                "n_noise, "
                "history_days, mature, maturity_note) VALUES (CAST(:p AS jsonb), 3, "
                "1, 0, "
                "0, false, 'FAKE run for trying the review screen') RETURNING id"
            ),
            {"p": json.dumps(_PARAMS)},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO emergent.title_cluster (run_id, cluster_id, "
                "canonical_titles, "
                "job_group_ids, n_titles, n_employers, it_share, centroid, "
                "nearest_category, "
                "nearest_cosine, is_candidate, candidate_note, hybrid_a, "
                "hybrid_weight_a, "
                "hybrid_b, hybrid_weight_b, hybrid_gain, novelty, novelty_note) VALUES "
                "(:r, 1, :t, '{}', 3, 7, 1.0, '{0}', 'software_engineer', 0.41, true, "
                "'FAKE test candidate - not real data', 'software_engineer', 0.6, "
                "'data_engineer', 0.4, 0.09, NULL, 'withheld: history too short')"
            ),
            {"r": run_id, "t": titles},
        )
        return conn.execute(
            text(
                "INSERT INTO emergent.role_candidate (run_id, cluster_id, name, "
                "characterisation, skill_signature, model, prompt_version) VALUES "
                "(:r, 1, :n, :c, '{Python,AWS,Kubernetes}', 'fake', 'fake') "
                "RETURNING id"
            ),
            {
                "r": run_id,
                "n": f"{FAKE_MARK} Forward deployed engineer",
                "c": (
                    f"{FAKE_MARK} This candidate is fabricated so you can try the "
                    "confirm and dismiss buttons. It says nothing about the market."
                ),
            },
        ).scalar_one()
