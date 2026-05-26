from __future__ import annotations

from typing import Any


def extract_entities(
    text: str,
    labels: list[str],
    nlp: Any,
    gliner_model: Any | None = None,
) -> dict[str, list[str]]:
    """Return entities grouped by label.

    nlp: a loaded spaCy Language object.
    gliner_model: optional GLiNER model for custom labels not covered by spaCy.
    """
    label_set = set(labels)
    entities: dict[str, list[str]] = {}

    doc = nlp(text)
    for ent in doc.ents:
        if ent.label_ in label_set:
            entities.setdefault(ent.label_, []).append(ent.text)

    if gliner_model is not None:
        for ent in gliner_model.predict_entities(text, labels):
            label = ent["label"]
            if label in label_set:
                entities.setdefault(label, []).append(ent["text"])

    return entities
