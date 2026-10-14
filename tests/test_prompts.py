"""Tests for embedding task prompts."""

from __future__ import annotations

import pytest

from feagle.embeddings import prompts


def test_query_and_doc_prompts_differ():
    assert prompts.format_query("cats") == "task: search result | query: cats"
    assert prompts.format_doc("cats are nice", "animals") == "title: animals | text: cats are nice"


def test_missing_title_uses_fallback():
    assert prompts.format_doc("body only") == "title: none | text: body only"


def test_code_and_qa_tasks():
    assert prompts.format_query("def f", task="code") == "task: code retrieval | query: def f"
    assert prompts.format_query("why?", task="qa") == "task: question answering | query: why?"


def test_unknown_task_lists_options():
    with pytest.raises(KeyError, match="available:"):
        prompts.get_task("summarize")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", True), ("  ", True), ("ok", True), ("long enough", False)],
)
def test_is_too_short(text, expected):
    assert prompts.is_too_short(text) is expected
