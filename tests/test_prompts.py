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


# ---- families ------------------------------------------------------------


def test_qwen3_query_uses_the_instruct_prefix():
    family = prompts.get_family("qwen3")
    assert family.format_query("capital of China") == (
        "Instruct: Given a web search query, retrieve relevant passages that answer the query\n"
        "Query:capital of China"
    )


def test_qwen3_documents_take_no_instruction():
    family = prompts.get_family("qwen3")
    assert family.format_doc("Beijing is the capital", "cities") == "Beijing is the capital"


def test_qwen3_code_and_qa_tasks():
    family = prompts.get_family("qwen3")
    assert "retrieve relevant code passages" in family.format_query("def f", task="code")
    assert "retrieve passages that answer it" in family.format_query("why?", task="qa")


def test_unknown_family_lists_options():
    with pytest.raises(KeyError, match="available:"):
        prompts.get_family("nope")


def test_default_family_is_gemma():
    assert prompts.get_family().name == "gemma"
    assert prompts.format_query("cats") == "task: search result | query: cats"


def test_family_knows_its_tasks():
    with pytest.raises(KeyError, match="available:"):
        prompts.get_family("qwen3").task("summarize")
