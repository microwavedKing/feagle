"""Benchmarks and the synthetic corpus generator.

Everything here works on synthetic data in a dedicated directory and a separate
database: personal files are never read (AGENTS.md §9). Used by ``feagle bench``
and by the thin scripts in ``bench/``.
"""

from __future__ import annotations

import contextlib
import json
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import db, paths
from .embeddings.client import EmbeddingError, ServerUnavailable
from .embeddings.service import EmbeddingService, ModelMissingError
from .indexer import pipeline
from .log import get_logger
from .metrics import current_rss_mb, llama_server_pid, peak_rss_mb, summarise
from .search import hybrid

log = get_logger("bench")

_DEGRADE_ERRORS = (ModelMissingError, ServerUnavailable, EmbeddingError, OSError)

# concept -> (document words, query phrase). Queries paraphrase the concept
# rather than repeating its words, so retrieval must work by meaning.
CONCEPTS: list[dict[str, str | list[str]]] = [
    {
        "en": ["sleep", "night", "rest", "recovery", "hours", "adults"],
        "en_query": "how much nightly rest does a grown person need",
        "ru": ["сон", "ночь", "отдых", "восстановление", "часы", "взрослые"],
        "ru_query": "сколько ночного покоя требуется зрелому человеку",
    },
    {
        "en": ["gravity", "black", "hole", "light", "escape", "horizon"],
        "en_query": "region where nothing can leave, not even a photon",
        "ru": ["гравитация", "чёрная", "дыра", "свет", "побег", "горизонт"],
        "ru_query": "область, которую не покидает даже фотон",
    },
    {
        "en": ["bread", "flour", "dough", "yeast", "oven", "bake"],
        "en_query": "kneading and proving before putting it in the oven",
        "ru": ["хлеб", "мука", "тесто", "дрожжи", "печь", "выпечка"],
        "ru_query": "замесить и дать подняться перед посадкой в печь",
    },
    {
        "en": ["memory", "ownership", "borrow", "pointer", "compiler", "safety"],
        "en_query": "how a language prevents dangling references without a collector",
        "ru": ["память", "владение", "заимствование", "указатель", "компилятор", "безопасность"],
        "ru_query": "как язык не даёт возникнуть висячим ссылкам без сборщика",
    },
    {
        "en": ["camera", "aperture", "lens", "shutter", "exposure", "focus"],
        "en_query": "blurring the background while keeping the subject sharp",
        "ru": ["камера", "диафрагма", "объектив", "затвор", "экспозиция", "фокус"],
        "ru_query": "размыть фон, оставив объект резким",
    },
    {
        "en": ["bicycle", "chain", "gear", "brake", "tyre", "maintenance"],
        "en_query": "keeping the drivetrain clean to reduce friction",
        "ru": ["велосипед", "цепь", "передача", "тормоз", "шина", "обслуживание"],
        "ru_query": "содержать привод чистым, чтобы меньше терять на трение",
    },
    {
        "en": ["database", "index", "query", "table", "transaction", "commit"],
        "en_query": "speeding up lookups and keeping writes atomic",
        "ru": ["база", "индекс", "запрос", "таблица", "транзакция", "фиксация"],
        "ru_query": "ускорить выборки и не потерять атомарность записи",
    },
    {
        "en": ["garden", "seedling", "soil", "frost", "watering", "harvest"],
        "en_query": "planting after the last cold spell and watering deeply",
        "ru": ["сад", "рассада", "почва", "заморозок", "полив", "урожай"],
        "ru_query": "высаживать после последних холодов и поливать обильно",
    },
    {
        "en": ["compound", "interest", "growth", "savings", "return", "capital"],
        "en_query": "earning returns on previously earned returns",
        "ru": ["процент", "сложный", "рост", "накопления", "доходность", "капитал"],
        "ru_query": "получать доход на ранее полученный доход",
    },
    {
        "en": ["antibiotic", "bacteria", "virus", "infection", "immune", "treatment"],
        "en_query": "medicines that work on germs but not on viral illness",
        "ru": ["антибиотик", "бактерия", "вирус", "инфекция", "иммунитет", "лечение"],
        "ru_query": "средства, действующие на микробы, но не на вирусы",
    },
    {
        "en": ["espresso", "pressure", "grind", "crema", "water", "temperature"],
        "en_query": "forcing hot water through finely ground beans",
        "ru": ["эспрессо", "давление", "помол", "крема", "вода", "температура"],
        "ru_query": "продавить горячую воду через мелко смолотые зёрна",
    },
    {
        "en": ["contract", "offer", "acceptance", "consideration", "party", "obligation"],
        "en_query": "elements required for an enforceable agreement",
        "ru": ["договор", "оферта", "акцепт", "встречное", "сторона", "обязанность"],
        "ru_query": "что нужно, чтобы соглашение имело силу",
    },
]

_CONNECTIVES = {
    "en": ["and", "with", "about", "near", "plus"],
    "ru": ["и", "с", "про", "возле", "при"],
}
#: Russian suffixes used to inflect document words (the query keeps the base
#: form), which exercises the stemmed FTS column.
_RU_SUFFIXES = ["", "ы", "и", "а", "ом", "ами", "ов", "е"]


def _inflect(rng: random.Random, word: str, lang: str) -> str:
    if lang != "ru":
        return word
    suffix = rng.choice(_RU_SUFFIXES)
    return word + suffix if len(word) > 4 else word


def _sentence(rng: random.Random, words: list[str], lang: str) -> str:
    """A concept-dense sentence: mostly topic words, little shared wording."""
    picked = rng.sample(words, min(rng.randint(3, 4), len(words)))
    inflected = [_inflect(rng, word, lang) for word in picked]
    connective = rng.choice(_CONNECTIVES[lang])
    if len(inflected) >= 3:
        return f"{inflected[0]} {connective} {inflected[1]}: {inflected[2]}."
    return f"{inflected[0]} {connective} {inflected[1]}."


def _paragraph(rng: random.Random, words: list[str], lang: str, sentences: int) -> str:
    return " ".join(_sentence(rng, words, lang) for _ in range(sentences))


def generate_corpus(out_dir: Path, files: int, seed: int = 1) -> dict[str, Any]:
    """Write ``files`` synthetic documents into ``out_dir/docs`` + queries.json.

    A previous run's documents are removed first: the default corpus directory is
    shared, and a leftover corpus with more files would silently become noise for
    the queries of this run (a 60-document report used to index 1200 documents).
    """
    rng = random.Random(seed)
    docs_dir = out_dir / "docs"
    if docs_dir.is_dir():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"seed": seed, "files": [], "queries": []}
    for index in range(files):
        concept = CONCEPTS[index % len(CONCEPTS)]
        lang = "ru" if index % 2 else "en"
        words = list(concept[lang])
        title = f"{words[0].capitalize()} {words[1]} {index}"

        kind = index % 3
        if kind == 0:
            name = f"doc{index:04d}.txt"
            body = title + "\n\n" + "\n\n".join(_paragraph(rng, words, lang, 6) for _ in range(6))
        elif kind == 1:
            name = f"doc{index:04d}.md"
            body = f"# {title}\n\n" + "\n\n".join(
                f"## {_inflect(rng, words[1], lang)}\n\n{_paragraph(rng, words, lang, 6)}"
                for _ in range(4)
            )
        else:
            name = f"doc{index:04d}.py"
            comment = "\n# ".join(_sentence(rng, words, lang) for _ in range(8))
            body = (
                f"# {title}\n# {comment}\n\n"
                f"def {words[0]}_{index}():\n"
                f'    """About {words[1]} and {words[2]}."""\n'
                f"    return {index}\n\n"
                f"def {words[1]}_{words[2]}(value):\n"
                f"    # {words[3]} of the {words[0]}\n"
                f"    # {_sentence(rng, words, lang)}\n"
                f"    return value\n"
            )
        (docs_dir / name).write_text(body + "\n", encoding="utf-8")
        manifest["files"].append({"name": name, "lang": lang, "concept": index % len(CONCEPTS)})
        manifest["queries"].append(
            {
                "query": concept[f"{lang}_query"],
                "expected": name,
                "lang": lang,
                "concept": index % len(CONCEPTS),
            }
        )

    (out_dir / "queries.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def default_corpus_dir() -> Path:
    return paths.cache_dir() / "bench"


def _clean_db(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        with_suffix = db_path.with_name(db_path.name + suffix)
        with_suffix.unlink(missing_ok=True)


def _load_queries(corpus_dir: Path) -> dict[str, Any]:
    return json.loads((corpus_dir / "queries.json").read_text(encoding="utf-8"))


def measure_queries(
    conn: Any,
    embedder: Any | None,
    queries: list[dict[str, Any]],
    *,
    repeats: int,
    limit: int,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Measure query latency for full-text and (if possible) hybrid search.

    ``hybrid`` is the end-to-end latency (query embedding + search); the
    breakdown is reported separately so the bottleneck is visible.
    """
    search_cfg = cfg["search"]
    fts_lat: list[float] = []
    hybrid_lat: list[float] = []
    embed_lat: list[float] = []
    vector_search_lat: list[float] = []
    for _ in range(max(1, repeats)):
        for item in queries:
            query = item["query"]
            start = time.perf_counter()
            hybrid.search_text(
                conn, query, limit=limit,
                candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"],
                stem=True, max_per_file=search_cfg["max_per_file"],
            )
            fts_lat.append((time.perf_counter() - start) * 1000)

            if embedder is None:
                continue
            start = time.perf_counter()
            try:
                vector = embedder.embed_queries([query])[0]
            except _DEGRADE_ERRORS:
                continue
            embed_lat.append((time.perf_counter() - start) * 1000)

            start = time.perf_counter()
            hybrid.search_hybrid(
                conn, query, query_vector=vector, limit=limit,
                candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"], stem=True,
                max_distance=hybrid.effective_max_distance(cfg),
                max_per_file=search_cfg["max_per_file"],
            )
            vector_search_lat.append((time.perf_counter() - start) * 1000)
            hybrid_lat.append(embed_lat[-1] + vector_search_lat[-1])

    return {
        "fts": summarise(fts_lat),
        "hybrid": summarise(hybrid_lat),
        "hybrid_embed": summarise(embed_lat),
        "hybrid_search": summarise(vector_search_lat),
    }


def _db_size(db_path: Path) -> int:
    """Index size on disk, including a write-ahead log that has not been merged.

    The main file alone undercounts a freshly indexed database (part of it can
    still live in `-wal`), which once made `mrl_dim = 256` look like "half the
    size" instead of the real quarter off.
    """
    parts = (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"))
    return sum(part.stat().st_size for part in parts if part.exists())


def _db_size_after_checkpoint(conn: Any, db_path: Path) -> int:
    """Merge the write-ahead log, then return the whole size on disk."""
    with contextlib.suppress(Exception):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return _db_size(db_path)


def evaluate_quality(
    conn: Any,
    embedder: Any | None,
    queries: list[dict[str, Any]],
    *,
    cfg: dict[str, Any],
    limit: int = 20,
) -> dict[str, Any]:
    """MRR@10 and Recall@10/20 for full-text and hybrid search."""
    search_cfg = cfg["search"]
    # Documents are generated from shared concepts (several languages/documents
    # per concept), so any document of the same concept counts as a hit.
    expected_by_concept: dict[Any, set[str]] = {}
    for item in queries:
        expected_by_concept.setdefault(item["concept"], set()).add(item["expected"])

    results: dict[str, Any] = {}
    for mode in ("fts", "hybrid"):
        if mode == "hybrid" and embedder is None:
            continue
        ranks: list[int | None] = []
        langs: list[str | None] = []
        misses: list[str] = []
        for item in queries:
            query = item["query"]
            if mode == "fts":
                hits = hybrid.search_text(
                    conn, query, limit=limit, candidates=search_cfg["candidates"],
                    rrf_k=search_cfg["rrf_k"], stem=True, max_per_file=search_cfg["max_per_file"],
                )
            else:
                vector = embedder.embed_queries([query])[0]  # type: ignore[union-attr]
                hits = hybrid.search_hybrid(
                    conn, query, query_vector=vector, limit=limit,
                    candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"], stem=True,
                    max_distance=hybrid.effective_max_distance(cfg),
                    max_per_file=search_cfg["max_per_file"],
                )
            accepted = expected_by_concept[item["concept"]]
            rank = next(
                (i for i, hit in enumerate(hits, 1) if Path(hit.path).name in accepted),
                None,
            )
            ranks.append(rank)
            langs.append(item.get("lang"))
            if rank is None or rank > 10:
                misses.append(f"{item['concept']}#{rank or '-'}")

        def _mrr(selected: list[int | None]) -> float:
            reciprocal = [1.0 / r for r in selected if r]
            return round(sum(reciprocal) / len(selected), 4) if selected else 0.0

        def _recall(selected: list[int | None], k: int) -> float:
            if not selected:
                return 0.0
            return round(sum(1 for r in selected if r and r <= k) / len(selected), 4)

        entry: dict[str, Any] = {
            "mrr@10": _mrr(ranks),
            "recall@10": _recall(ranks, 10),
            "recall@20": _recall(ranks, 20),
            "misses": misses[:10],
        }
        for lang in ("en", "ru"):
            subset = [r for r, code in zip(ranks, langs, strict=True) if code == lang]
            if subset:
                entry[f"mrr@10_{lang}"] = _mrr(subset)
        results[mode] = entry
    return results


def quality_report(
    cfg: dict[str, Any],
    *,
    corpus_dir: Path | None = None,
    files: int = 60,
    seed: int = 1,
    limit: int = 20,
    keep_db: bool = False,
) -> dict[str, Any]:
    """Index a synthetic corpus and report MRR@10 / Recall@10/20."""
    corpus_dir = Path(corpus_dir) if corpus_dir else default_corpus_dir()
    manifest = generate_corpus(corpus_dir, files=files, seed=seed)
    db_path = corpus_dir / "quality.sqlite"
    _clean_db(db_path)
    runtime_dir = Path(tempfile.mkdtemp(prefix="feagle-bench-"))

    service = EmbeddingService(cfg, runtime_dir=runtime_dir)
    embedder = None
    try:
        try:
            embedder = service.start()
        except _DEGRADE_ERRORS as exc:
            log.warning("quality evaluation running without embeddings: %s", exc)
        conn = db.open_database(db_path)
        try:
            pipeline.index_roots(conn, cfg, roots=[str(corpus_dir / "docs")], embedder=embedder)
            chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            db_size = _db_size_after_checkpoint(conn, db_path)
            quality = evaluate_quality(conn, embedder, manifest["queries"], cfg=cfg, limit=limit)
        finally:
            conn.close()
    finally:
        service.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)
    if not keep_db:
        _clean_db(db_path)

    return {
        "corpus": str(corpus_dir),
        "files": len(manifest["files"]),
        "seed": seed,
        "queries": len(manifest["queries"]),
        "model": cfg["embeddings"]["model"],
        "mrl_dim": cfg["embeddings"]["mrl_dim"],
        "chunks": chunks,
        "db_size_bytes": db_size,
        "db_size_per_chunk": round(db_size / chunks) if chunks else 0,
        "quality": quality,
    }


def run_bench(
    cfg: dict[str, Any],
    *,
    corpus_dir: Path | None = None,
    files: int = 60,
    seed: int = 1,
    query_limit: int = 0,
    repeats: int = 3,
    limit: int = 10,
    embed: bool = True,
    keep_db: bool = False,
) -> dict[str, Any]:
    """Index a synthetic corpus and measure speed, latency, memory and size."""
    corpus_dir = Path(corpus_dir) if corpus_dir else default_corpus_dir()
    manifest = generate_corpus(corpus_dir, files=files, seed=seed)
    docs_dir = corpus_dir / "docs"

    db_path = corpus_dir / "bench.sqlite"
    _clean_db(db_path)
    # An isolated runtime dir keeps the benchmark from reusing (and then
    # measuring) a llama-server that another process already owns with a
    # different model.
    runtime_dir = Path(tempfile.mkdtemp(prefix="feagle-bench-"))

    service: EmbeddingService | None = None
    embedder = None
    cold_start_s = 0.0
    conn = None
    try:
        if embed:
            service = EmbeddingService(cfg, runtime_dir=runtime_dir)
            started = time.perf_counter()
            try:
                embedder = service.start()
                cold_start_s = time.perf_counter() - started
            except _DEGRADE_ERRORS as exc:
                log.warning("benchmark running without embeddings: %s", exc)
                embedder = None

        conn = db.open_database(db_path)
        peak_before = _self_peak_rss()
        started = time.perf_counter()
        stats = pipeline.index_roots(conn, cfg, roots=[str(docs_dir)], embedder=embedder)
        index_s = time.perf_counter() - started
        peak_after = _self_peak_rss()

        db_size = _db_size_after_checkpoint(conn, db_path)
        chunk_count = stats.chunks
        result: dict[str, Any] = {
            "corpus": str(corpus_dir),
            "model": cfg["embeddings"]["model"],
            "files": len(manifest["files"]),
            "seed": seed,
            "indexing": {
                "seconds": round(index_s, 2),
                "files_per_second": round(stats.reindexed / index_s, 2) if index_s else 0.0,
                "chunks": chunk_count,
                "chunks_per_second": round(chunk_count / index_s, 2) if index_s else 0.0,
                "vectors": stats.vectors,
                "coverage": round(stats.vectors / chunk_count, 4) if chunk_count else 0.0,
                "db_size_bytes": db_size,
                "db_size_per_chunk": round(db_size / chunk_count) if chunk_count else 0,
                "cold_start_s": round(cold_start_s, 2),
                "peak_rss_self_mb": _delta(peak_before, peak_after),
            },
        }

        queries = manifest["queries"]
        if query_limit:
            queries = queries[:query_limit]
        result["queries"] = measure_queries(
            conn, embedder, queries, repeats=repeats, limit=limit, cfg=cfg
        )
        result["quality"] = evaluate_quality(conn, embedder, queries, cfg=cfg)

        pid = _llama_pid(runtime_dir)
        if pid is not None:
            result["llama_server_rss_mb"] = round(current_rss_mb(pid) or 0.0, 1)
    finally:
        if conn is not None:
            conn.close()
        if service is not None:
            service.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)
        if not keep_db:
            _clean_db(db_path)
    return result


def _self_peak_rss() -> float | None:
    return peak_rss_mb()


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 1)


def _llama_pid(runtime_dir: Path) -> int | None:
    return llama_server_pid(runtime_dir)
