from __future__ import annotations

from pathlib import Path

from flashtext import KeywordProcessor


def build_keyword_processor(keywords_path: str) -> KeywordProcessor:
    processor = KeywordProcessor(case_sensitive=False)
    path = Path(keywords_path)
    if path.exists():
        for line in path.read_text().splitlines():
            kw = line.strip()
            if kw:
                processor.add_keyword(kw)
    return processor


def extract_keywords(text: str, processor: KeywordProcessor) -> list[str]:
    return list(dict.fromkeys(processor.extract_keywords(text)))
