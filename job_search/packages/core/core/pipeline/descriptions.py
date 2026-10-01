"""Plain-language explanations of each pipeline stage and phase, shown
by the dashboard's Explain button. Keys match `registry.STAGES` /
`registry.REVIEW_STAGES` exactly; `test_pipeline_descriptions.py` fails
if a stage is added without an entry here.

Kept out of `registry.py` so the stage catalog stays about behaviour
(dependencies, run wrappers) and this file stays about prose.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Description:
    """One Explain-modal entry.

    Attributes:
        summary: What the stage or phase does, in plain language.
        input: What it reads.
        output: What it produces, and where it is stored.
    """

    summary: str
    input: str
    output: str


PHASE_DESCRIPTIONS: dict[str, Description] = {
    "Ingestion & Dedup": Description(
        summary=(
            "Turns raw job postings pulled from several sources into one clean "
            "record per real-world job. The same role is often posted on more "
            "than one board, so this phase finds the duplicates and merges them."
        ),
        input="Raw postings from every ingestion source (unioned in the warehouse).",
        output=(
            "One job group per distinct job, with a single winning title, "
            "description and apply URL (`silver.job_identity_map`, "
            "`silver.job_survivorship`)."
        ),
    ),
    "Categorisation": Description(
        summary=(
            "Labels each distinct job with a category, using rules first and "
            "an LLM only for the titles the rules can't settle."
        ),
        input="The de-duplicated jobs from the previous phase.",
        output=(
            "A category per job group (`silver.job_category`), plus a human "
            "review queue."
        ),
    ),
    "CV & Skills": Description(
        summary=(
            "Maps the skills that appear in job postings and in your CV onto "
            "one shared skill vocabulary (ESCO, plus custom skills), so jobs "
            "and CVs can be compared skill by skill."
        ),
        input="The ESCO skills release, extracted job skills, and each user's CV.",
        output=(
            "A shared skill mapping (`silver.skill_mapping`) and a CV whose "
            "skills carry canonical skill ids."
        ),
    ),
    "Scoring": Description(
        summary=(
            "Ranks jobs for one user in a funnel: cheap hard filters first, "
            "then similarity, skill coverage and an LLM re-rank of the best "
            "candidates, blended into one final score."
        ),
        input="Categorised jobs, the user's CV and skills, and the user's preferences.",
        output="A final score per eligible job for that user (`scoring.job_score`).",
    ),
}


STAGE_DESCRIPTIONS: dict[str, Description] = {
    # --- Ingestion & Dedup ---------------------------------------------
    "enrich-engagement-terms": Description(
        summary=(
            "Reads each posting's free text and pulls out how the work is "
            "engaged: permanent, fixed-term, interim or contract, IR35 "
            "status and rate terms. Rule-based; no LLM."
        ),
        input="Every unioned job posting (`intermediate.int_jobs__unioned`).",
        output=(
            "One row of engagement terms per posting "
            "(`silver.job_engagement_terms`)."
        ),
    ),
    "compute-blocking-keys": Description(
        summary=(
            "Computes cheap normalised keys per posting (for example a "
            "cleaned title and company). Postings sharing a key become "
            "candidate duplicates, which avoids comparing every pair of jobs."
        ),
        input="Cleaned postings (`silver.silver__job_posting`).",
        output="Blocking keys per posting (`dedup.job_blocking_keys`).",
    ),
    "compute-similarity-features": Description(
        summary=(
            "Builds a SimHash fingerprint of each posting's text, so two "
            "postings can later be compared by how alike their wording is."
        ),
        input="Cleaned postings (`silver.silver__job_posting`).",
        output=(
            "A fingerprint and related features per posting "
            "(`dedup.job_similarity_features`)."
        ),
    ),
    "compute-title-similarity-scores": Description(
        summary=(
            "For every candidate duplicate pair, scores how similar the two "
            "titles are (fuzzy token matching). This is one of the signals "
            "blended into the final duplicate score."
        ),
        input=(
            "Candidate pairs (`dedup.dedup__candidate_pairs`) and their blocking "
            "keys."
        ),
        output="A title-similarity score per pair (`dedup.pair_title_scores`).",
    ),
    "cluster-jobs": Description(
        summary=(
            "Groups postings that are the same job into one `job_group_id`, "
            "using exact duplicates, confident similarity matches and the "
            "labels you gave in Dedup Review. Existing group ids are never "
            "rewritten."
        ),
        input=(
            "Exact duplicates, blended pair scores, your pair labels and the "
            "latest calibrated auto-match threshold."
        ),
        output="The posting-to-group mapping (`silver.job_identity_map`).",
    ),
    "compute-survivorship": Description(
        summary=(
            "For each job group, picks the best value per field: the longest "
            "description, and the title and apply URL from the "
            "highest-ranked source."
        ),
        input="Job groups (`silver.job_identity_map`) and their postings.",
        output="One surviving record per job group (`silver.job_survivorship`).",
    ),
    "dedup-review": Description(
        summary=(
            "A human step. Pairs the system can't confidently call match or "
            "non-match are shown to you to label. Your labels feed "
            "cluster-jobs and help calibrate the thresholds."
        ),
        input=(
            "Scored pairs between the auto-reject and auto-match thresholds, not "
            "yet labelled."
        ),
        output="Your match / not-a-match decisions (`dedup.pair_labels`).",
    ),
    # --- Categorisation ------------------------------------------------
    "classify-jobs": Description(
        summary=(
            "Assigns each job group a category. Rules and embeddings decide "
            "the clear cases; only the ambiguous remainder goes to the LLM "
            "(a paid Anthropic call). Jobs already classified are skipped."
        ),
        input="Surviving job records not yet in `silver.job_category`.",
        output="A category per job group (`silver.job_category`).",
    ),
    "categorisation-review": Description(
        summary=(
            "A human step. You check the assigned categories and correct "
            "the wrong ones."
        ),
        input="Categorised jobs that have no review label yet.",
        output="Your review labels (`classification.category_review_labels`).",
    ),
    # --- CV & Skills ---------------------------------------------------
    "load-esco": Description(
        summary=(
            "Loads the ESCO skills and occupations release into the "
            "database. Safe to re-run: it upserts, and drops only labels "
            "ESCO itself removed."
        ),
        input="The ESCO English CSV release folder (`/data/esco`).",
        output=(
            "ESCO skills, labels, occupations and their relations (the `esco` "
            "schema)."
        ),
    ),
    "embed-esco": Description(
        summary=(
            "Computes a vector embedding of each ESCO skill's preferred "
            "label, so a skill string that matches no label exactly can "
            "still be matched by meaning. Only missing or outdated "
            "embeddings are computed, so an interrupted run resumes."
        ),
        input="ESCO skill labels and the embedding model (Ollama).",
        output="One vector per skill (`esco.skill_embedding`).",
    ),
    "extract-job-skills": Description(
        summary=(
            "Uses an LLM to read each job description and list the skills "
            "it asks for. Runs in sub-batches with live progress and can be "
            "cancelled mid-run."
        ),
        input="Surviving job descriptions that have no skill extraction yet.",
        output="Extracted skills per job (`silver.job_skill_extraction`).",
    ),
    "map-skills": Description(
        summary=(
            "Resolves each raw skill string to an ESCO or custom skill, "
            "deterministically: curated alias, then exact ESCO label, then "
            "nearest embedding above a threshold. Anything else is left "
            "open for review. Existing decisions are never overwritten."
        ),
        input="Raw skill strings from jobs and CVs, ESCO labels and embeddings.",
        output="One mapping row per distinct string (`silver.skill_mapping`).",
    ),
    "llm-map-skills": Description(
        summary=(
            "Asks the LLM (a paid Anthropic call) to settle skill strings "
            "the mapper left open: pick one of the 5 nearest ESCO skills, "
            "say none fits, or say it is unsure. Confident matches wait in "
            "'Auto-matches — verify' for you to confirm."
        ),
        input="Open (unmapped) skill strings, not yet checked by the LLM.",
        output="Proposed or applied mappings in `silver.skill_mapping`.",
    ),
    "skill-review": Description(
        summary=(
            "A human step. You resolve skill strings that are still "
            "unmapped, verify the LLM's auto-matches, and can reopen past "
            "decisions."
        ),
        input="Unmapped skill strings and LLM auto-matches awaiting confirmation.",
        output="Your decisions, written back to `silver.skill_mapping` (and aliases).",
    ),
    "map-cv-skills": Description(
        summary=(
            "Fills in the canonical skill id on each skill in the selected "
            "user's CV, using the shared mapping. Saved as a new, "
            "reversible CV version. Per-user."
        ),
        input="The selected user's CV skills and `silver.skill_mapping`.",
        output="A new CV truth-base version with canonical skill ids filled in.",
    ),
    # --- Scoring -------------------------------------------------------
    "score-filter-jobs": Description(
        summary=(
            "Stage 1 of the funnel: hard filters. Drops jobs that break the "
            "user's preferences. A user with no saved preferences keeps "
            "every job. Per-user."
        ),
        input="Categorised jobs (`gold.dim_job`) and the user's preferences.",
        output="A score row per surviving job (`scoring.job_score`).",
    ),
    "chunk-embed-jobs": Description(
        summary=(
            "Splits each eligible job description into sections and embeds "
            "each chunk. Shared across users, computed once per job."
        ),
        input="Job descriptions that passed the hard filters.",
        output="Job chunk embeddings (`scoring.job_chunk_embedding`).",
    ),
    "chunk-embed-cv": Description(
        summary=(
            "Chunks the selected user's CV along its known structure and "
            "embeds each chunk. Per-user and never shared."
        ),
        input="The user's CV truth base.",
        output="CV chunk embeddings (`scoring.cv_chunk_embedding`).",
    ),
    "score-similarity": Description(
        summary=(
            "Stage 2: compares CV chunks with job chunks by vector "
            "similarity, then re-ranks the top candidates with a "
            "cross-encoder model. Refuses to compare vectors made with "
            "different embedding models."
        ),
        input="CV and job chunk embeddings, and the current job scores.",
        output="A similarity score per job (updates `scoring.job_score`).",
    ),
    "score-skill-coverage": Description(
        summary=(
            "Stage 3: how much of the job's required skills the CV covers. "
            "Must-have skills weigh more than nice-to-have, and a skill "
            "counts for less the longer since it was last used."
        ),
        input=(
            "The job-skill bridge (`silver.silver__bridge_job_skill`) and the "
            "CV's skills."
        ),
        output="A skill-coverage score per job (updates `scoring.job_score`).",
    ),
    "score-llm-rerank": Description(
        summary=(
            "Stage 4: the LLM (a paid Anthropic call) re-ranks the user's "
            "top 50 jobs. Never sends more than 50 per run."
        ),
        input="The top-scoring jobs and the user's CV.",
        output="An LLM rerank score per job (updates `scoring.job_score`).",
    ),
    "score-blend": Description(
        summary=(
            "Combines the component scores into one final score with "
            "configured weights. Until you calibrate, every available "
            "component is weighted equally."
        ),
        input=(
            "All component scores (`scoring.job_score`) and weights "
            "(`scoring.weight`)."
        ),
        output="A final score per job (updates `scoring.job_score`).",
    ),
    "scoring-calibration": Description(
        summary=(
            "A human step. You label jobs as good or bad matches; once you "
            "have enough labels, the blend weights are tuned from them."
        ),
        input="Scored jobs presented for labelling (30 labels needed).",
        output="Your labels (`scoring.job_label`) and the calibrated weights.",
    ),
}
