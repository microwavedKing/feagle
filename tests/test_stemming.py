"""Tests for Russian stemming."""

from __future__ import annotations

from feagle import stemming


def test_stemmer_is_available():
    assert stemming.available() is True


def test_cyrillic_word_forms_share_a_stem():
    forms = ["книга", "книги", "книге", "книгами"]
    stems = {stemming.stem_token(form) for form in forms}
    assert len(stems) == 1


def test_latin_tokens_are_untouched():
    assert stemming.stem_token("Running") == "Running"
    assert stemming.stem_token("get_user") == "get_user"


def test_stem_text_keeps_only_cyrillic_stems():
    text = stemming.stem_text("Книга про Python и другие книги")
    tokens = text.split()
    assert "Python" not in tokens
    assert any(token.startswith("книг") for token in tokens)


def test_stem_query_terms_drops_latin():
    assert stemming.stem_query_terms(["книги", "python"]) == [stemming.stem_token("книги")]


def test_stem_query_terms_dedupes_forms():
    assert len(stemming.stem_query_terms(["книга", "книги", "книге"])) == 1


def test_stem_query_terms_without_cyrillic_is_empty():
    assert stemming.stem_query_terms(["hello", "world"]) == []
