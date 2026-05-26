from __future__ import annotations

import spacy
from fastapi import FastAPI
from pydantic import BaseModel

from agentic_triage.preprocessing.ner import extract_entities

app = FastAPI(title="spaCy NER Service")

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_lg")
    return _nlp


class NERRequest(BaseModel):
    text: str
    labels: list[str]


class NERResponse(BaseModel):
    entities: dict[str, list[str]]


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/ner", response_model=NERResponse)
def ner(request: NERRequest) -> NERResponse:
    entities = extract_entities(request.text, request.labels, _get_nlp())
    return NERResponse(entities=entities)
