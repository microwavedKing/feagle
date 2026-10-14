"""EmbeddingGemma task prompts.

The model card requires task prefixes; using the wrong one (or none at all)
"works" but degrades retrieval quality. Query and document prompts differ.
"""

from __future__ import annotations

from dataclasses import dataclass

TITLE_FALLBACK = "none"

#: Minimum number of characters we send to the server. Very short inputs crashed
#: old llama.cpp builds (issue #6722), and they carry no useful signal.
MIN_INPUT_CHARS = 8


@dataclass(frozen=True)
class TaskPrompts:
    query: str
    doc: str


TASKS: dict[str, TaskPrompts] = {
    # Default: general document search.
    "search": TaskPrompts(
        query="task: search result | query: {q}",
        doc="title: {title} | text: {text}",
    ),
    "code": TaskPrompts(
        query="task: code retrieval | query: {q}",
        doc="title: {title} | text: {text}",
    ),
    "qa": TaskPrompts(
        query="task: question answering | query: {q}",
        doc="title: {title} | text: {text}",
    ),
}

DEFAULT_TASK = "search"


def get_task(name: str) -> TaskPrompts:
    try:
        return TASKS[name]
    except KeyError:
        options = ", ".join(sorted(TASKS))
        raise KeyError(f"unknown embedding task {name!r}; available: {options}") from None


def format_query(text: str, task: str = DEFAULT_TASK) -> str:
    return get_task(task).query.format(q=text)


def format_doc(text: str, title: str | None = None, task: str = DEFAULT_TASK) -> str:
    return get_task(task).doc.format(title=title or TITLE_FALLBACK, text=text)


def is_too_short(text: str) -> bool:
    """Whether ``text`` (after prompts) is too short to send safely."""
    return len(text.strip()) < MIN_INPUT_CHARS
