"""Model-specific embedding prompts.

Different embedding models expect different inputs, and using the wrong one (or
none at all) "works" but degrades retrieval quality:

- **EmbeddingGemma 2** wants a task prefix on both sides, e.g.
  ``task: search result | query: …`` and ``title: … | text: …``.
- **Qwen3-Embedding** wants ``Instruct: <task>\\nQuery:<query>`` on the *query*
  only; documents are embedded as-is ("No need to add instruction for retrieval
  documents", see the model card).

Each model preset names a family here (``ModelSpec.prompt_family``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

TITLE_FALLBACK = "none"

#: Minimum number of characters we send to the server. Very short inputs crashed
#: old llama.cpp builds (issue #6722), and they carry no useful signal.
MIN_INPUT_CHARS = 8


@dataclass(frozen=True)
class TaskPrompts:
    query: str
    doc: str


def _gemma_tasks() -> dict[str, TaskPrompts]:
    return {
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


#: The Qwen3-Embedding task descriptions are free-form one-liners; these are the
#: wording from the model card (retrieval) and close variants for the other tasks.
_QWEN3_INSTRUCTIONS = {
    "search": "Given a web search query, retrieve relevant passages that answer the query",
    "code": "Given a code search query, retrieve relevant code passages that answer the query",
    "qa": "Given a question, retrieve passages that answer it",
}


def _qwen3_tasks() -> dict[str, TaskPrompts]:
    return {
        name: TaskPrompts(
            query=f"Instruct: {instruction}\nQuery:{{q}}",  # no space after "Query:"
            doc="{text}",  # documents take no instruction
        )
        for name, instruction in _QWEN3_INSTRUCTIONS.items()
    }


@dataclass(frozen=True)
class PromptFamily:
    """The prompts one model family expects."""

    name: str
    tasks: dict[str, TaskPrompts] = field(default_factory=dict)
    default_task: str = "search"

    def task(self, name: str | None = None) -> TaskPrompts:
        key = name or self.default_task
        try:
            return self.tasks[key]
        except KeyError:
            options = ", ".join(sorted(self.tasks))
            raise KeyError(f"unknown embedding task {key!r}; available: {options}") from None

    def format_query(self, text: str, task: str | None = None) -> str:
        return self.task(task).query.format(q=text)

    def format_doc(self, text: str, title: str | None = None, task: str | None = None) -> str:
        return self.task(task).doc.format(title=title or TITLE_FALLBACK, text=text)


GEMMA = PromptFamily("gemma", _gemma_tasks())
QWEN3 = PromptFamily("qwen3", _qwen3_tasks())

FAMILIES: dict[str, PromptFamily] = {GEMMA.name: GEMMA, QWEN3.name: QWEN3}

DEFAULT_FAMILY = GEMMA.name
DEFAULT_TASK = GEMMA.default_task

#: Kept for callers that only know about the default (EmbeddingGemma) family.
TASKS: dict[str, TaskPrompts] = GEMMA.tasks


def get_family(name: str = DEFAULT_FAMILY) -> PromptFamily:
    try:
        return FAMILIES[name]
    except KeyError:
        options = ", ".join(sorted(FAMILIES))
        raise KeyError(f"unknown prompt family {name!r}; available: {options}") from None


def get_task(name: str) -> TaskPrompts:
    return GEMMA.task(name)


def format_query(text: str, task: str = DEFAULT_TASK) -> str:
    return GEMMA.format_query(text, task)


def format_doc(text: str, title: str | None = None, task: str = DEFAULT_TASK) -> str:
    return GEMMA.format_doc(text, title, task)


def is_too_short(text: str) -> bool:
    """Whether ``text`` (after prompts) is too short to send safely."""
    return len(text.strip()) < MIN_INPUT_CHARS
