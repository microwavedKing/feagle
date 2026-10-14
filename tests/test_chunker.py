"""Tests for chunking."""

from __future__ import annotations

from feagle.indexer.chunker import Chunk, _spans, chunk_extracted
from feagle.indexer.extract import Extracted


def test_empty_text_yields_no_chunks():
    assert chunk_extracted(Extracted(pages=("",), kind="text")) == []


def test_short_text_is_a_single_chunk():
    text = "Just one short paragraph."
    chunks = chunk_extracted(Extracted(pages=(text,), kind="text"))
    assert chunks == [Chunk(seq=0, page=None, line=1, body=text)]


def test_seq_is_contiguous():
    text = "\n".join(f"line-{i}-{'x' * 40}" for i in range(200))
    chunks = chunk_extracted(Extracted(pages=(text,), kind="text"))
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert len(chunks) > 1


def test_chunks_respect_max_size():
    text = "y" * 10000  # single huge line, no natural breaks
    chunks = chunk_extracted(Extracted(pages=(text,), kind="text"))
    assert all(len(c.body) <= 2200 for c in chunks)
    assert chunks[0].body == text[:1400]
    assert chunks[-1].body.endswith("y" * 100)


def test_spans_cover_text_without_gaps():
    text = "\n".join(f"line-{i}-{'x' * 40}" for i in range(200))
    spans = _spans(text, target=1400, overlap=150, min_chars=200, max_chars=2200)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    assert all(end - start <= 2200 for start, end in spans)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert next_start <= prev_end  # overlap or adjacency, never a gap


def test_line_numbers_point_at_chunk_start():
    text = "\n".join(f"line-{i}-{'x' * 40}" for i in range(200))
    chunks = chunk_extracted(Extracted(pages=(text,), kind="text"))
    assert chunks[0].line == 1
    for chunk in chunks:
        # the reported line must be the first line of the chunk body
        first_line = chunk.body.split("\n", 1)[0]
        expected = text.split("\n")[chunk.line - 1]
        assert first_line == expected


def test_consecutive_chunks_overlap():
    text = "\n".join(f"line-{i}-{'x' * 40}" for i in range(200))
    chunks = chunk_extracted(Extracted(pages=(text,), kind="text"))
    assert chunks[1].body[:50] in chunks[0].body


def test_overlap_never_crosses_pdf_page():
    page1 = "page one " * 300
    page2 = "page two " * 300
    chunks = chunk_extracted(Extracted(pages=(page1, page2), kind="pdf"))
    assert {c.page for c in chunks} == {1, 2}
    for chunk in chunks:
        source = page1 if chunk.page == 1 else page2
        assert chunk.body in source
        assert chunk.line is None


def test_whitespace_only_text():
    chunks = chunk_extracted(Extracted(pages=("   \n\n  ",), kind="text"))
    assert len(chunks) == 1
