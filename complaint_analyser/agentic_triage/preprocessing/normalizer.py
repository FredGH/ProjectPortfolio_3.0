from __future__ import annotations

from pathlib import Path

from symspellpy import SymSpell


def build_symspell(vocab_path: str | None = None) -> SymSpell:
    sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)

    import pkg_resources

    builtin = pkg_resources.resource_filename(
        "symspellpy", "frequency_dictionary_en_82_765.txt"
    )
    sym_spell.load_dictionary(builtin, term_index=0, count_index=1)

    if vocab_path:
        path = Path(vocab_path)
        if path.exists():
            sym_spell.load_dictionary(str(path), term_index=0, count_index=1)

    return sym_spell


def normalize(text: str, sym_spell: SymSpell) -> str:
    suggestions = sym_spell.lookup_compound(text, max_edit_distance=2)
    if suggestions:
        return suggestions[0].term
    return text
