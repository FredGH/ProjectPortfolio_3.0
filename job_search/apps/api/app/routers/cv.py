"""GET/PUT /cv/truth-base, POST /cv/extract — the request-serving layer
for PLAN.md Step 13's CV truth base. The first per-user-tenancy router
in this API: every handler resolves `user_id` via `get_current_user_id`
(501s until Step 22a's auth lands, same seam every other per-user
endpoint will use) and reads/writes exclusively through
`core.cv.store`, never raw SQL of its own against `cv_truth_base`.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from sqlalchemy import Engine

from core.cv.extract import docling_to_markdown, extract_truth_base
from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter

router = APIRouter(prefix="/cv")


class TruthBaseResponse(BaseModel):
    """One user's current CV truth base, over the wire.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The structured truth base.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase


class TruthBaseWriteRequest(BaseModel):
    """A correction-pass save, or any other direct truth-base replace.

    Attributes:
        extracted_markdown: The markdown to store alongside this version.
        truth_base: The truth base to store as the new current version.
    """

    extracted_markdown: str
    truth_base: CVTruthBase


class WriteResult(BaseModel):
    """The outcome of a truth-base write.

    Attributes:
        version: The new version number.
    """

    version: int


@router.get("/truth-base", response_model=TruthBaseResponse | None)
def get_truth_base(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> TruthBaseResponse | None:
    """Return the caller's current CV truth base.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The `TruthBaseResponse`, or None if this user has no CV yet.
    """
    stored = read_truth_base(engine, user_id)
    if stored is None:
        return None
    return TruthBaseResponse(
        version=stored.version,
        extracted_markdown=stored.extracted_markdown,
        truth_base=stored.truth_base,
    )


@router.put("/truth-base", response_model=WriteResult)
def put_truth_base(
    request: TruthBaseWriteRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> WriteResult:
    """Replace the caller's CV truth base with a new version.

    Used by both the correction UI's save action and any direct client
    that already has a `CVTruthBase` to store.

    Args:
        request: The new markdown/truth-base pair to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new version number.
    """
    version = write_truth_base(
        engine, user_id, request.extracted_markdown, request.truth_base
    )
    return WriteResult(version=version)


@router.post("/extract", response_model=WriteResult)
async def post_extract(
    file: UploadFile,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> WriteResult:
    """Extract a CV PDF and store the result as a new version.

    Args:
        file: The uploaded CV document.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The new version number.
    """
    file_bytes = await file.read()
    markdown = docling_to_markdown(file_bytes, file.filename or "cv.pdf")
    truth_base = extract_truth_base(markdown, adapters=adapters)
    version = write_truth_base(engine, user_id, markdown, truth_base)
    return WriteResult(version=version)
