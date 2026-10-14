"""Benchmarks and the synthetic corpus generator.

Everything here works on synthetic data in a dedicated directory and a separate
database: personal files are never read (AGENTS.md §9). Used by ``feagle bench``
and by the thin scripts in ``bench/``.
"""

from __future__ import annotations

import json
import random
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

_FILLER = {
    "en": ["however", "in practice", "for example", "finally"],
    "ru": ["однако", "на практике", "например", "наконец"],
}
_TEMPLATES = {
    "en": ["{a}, {b} and {c} are usually considered together.",
           "The main point concerns {a} and {c}.",
           "In practice, {b} matters as much as {a}."],
    "ru": ["{a}, {b} и {c} обычно рассматривают вместе.",
           "Главная мысль касается {a} и {c}.",
           "На практике {b} важно не меньше, чем {a}."],
}


def _paragraph(rng: random.Random, words: list[str], lang: str, sentences: int) -> str:
    out = []
    for _ in range(sentences):
        a, b, c = (rng.choice(words) for _ in range(3))
        sentence = rng.choice(_TEMPLATES[lang]).format(a=a, b=b, c=c)
        out.append(f"{sentence} {rng.choice(_FILLER[lang])}.")
    return " ".join(out)


def generate_corpus(out_dir: Path, files: int, seed: int = 1) -> dict[str, Any]:
    """Write ``files`` synthetic documents into ``out_dir/docs`` + queries.json."""
    rng = random.Random(seed)
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"seed": seed, "files": [], "queries": []}
    for index in range(files):
        concept = CONCEPTS[index % len(CONCEPTS)]
        lang = "ru" if index % 2 else "en"
        words = list(concept[lang])  # type: ignore[arg-type]
        title = f"{words[0].capitalize()} {words[1]} {index}"

        kind = index % 3
        if kind == 0:
            name = f"doc{index:04d}.txt"
            body = title + "\n\n" + "\n\n".join(_paragraph(rng, words, lang, 6) for _ in range(3))
        elif kind == 1:
            name = f"doc{index:04d}.md"
            body = f"# {title}\n\n" + "\n\n".join(
                f"## {words[1]}\n\n{_paragraph(rng, words, lang, 5)}" for _ in range(3)
            )
        else:
            name = f"doc{index:04d}.py"
            comment = _paragraph(rng, words, lang, 2).replace(". ", ".\n# ")
            body = (
                f"# {title}\n# {comment}\n\n"
                f"def {words[0]}_{index}():\n"
                f'    """About {words[1]} and {words[2]}."""\n'
                f"    return {index}\n\n"
                f"def {words[1]}_{words[2]}(value):\n"
                f"    # {words[3]} of the {words[0]}\n"
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
    """Measure query latency for full-text and (if possible) hybrid search."""
    search_cfg = cfg["search"]
    fts_lat: list[float] = []
    hybrid_lat: list[float] = []
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
            try:
                vector = embedder.embed_queries([query])[0]
            except _DEGRADE_ERRORS:
                continue
            start = time.perf_counter()
            hybrid.search_hybrid(
                conn, query, query_vector=vector, limit=limit,
                candidates=search_cfg["candidates"], rrf_k=search_cfg["rrf_k"], stem=True,
                max_distance=search_cfg["max_distance"], max_per_file=search_cfg["max_per_file"],
            )
            hybrid_lat.append((time.perf_counter() - start) * 1000)

    return {"fts": summarise(fts_lat), "hybrid": summarise(hybrid_lat)}


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
                    max_distance=search_cfg["max_distance"],
                    max_per_file=search_cfg["max_per_file"],
                )
            rank = next(
                (i for i, hit in enumerate(hits, 1) if Path(hit.path).name == item["expected"]),
                None,
            )
            ranks.append(rank)
            langs.append(item.get("lang"))
            if rank is None or rank > 10:
                misses.append(f"{item['expected']}#{rank or '-'}")

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

    service = EmbeddingService(cfg)
    embedder = None
    try:
        try:
            embedder = service.start()
        except _DEGRADE_ERRORS as exc:
            log.warning("quality evaluation running without embeddings: %s", exc)
        conn = db.open_database(db_path)
        try:
            pipeline.index_roots(conn, cfg, roots=[str(corpus_dir / "docs")], embedder=embedder)
            quality = evaluate_quality(conn, embedder, manifest["queries"], cfg=cfg, limit=limit)
        finally:
            conn.close()
    finally:
        service.close()
    if not keep_db:
        _clean_db(db_path)

    return {
        "corpus": str(corpus_dir),
        "files": len(manifest["files"]),
        "seed": seed,
        "queries": len(manifest["queries"]),
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

    # --- indexing ---------------------------------------------------------
    service: EmbeddingService | None = None
    embedder = None
    cold_start_s = 0.0
    if embed:
        service = EmbeddingService(cfg)
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

    db_size = db_path.stat().st_size
    chunk_count = stats.chunks
    result: dict[str, Any] = {
        "corpus": str(corpus_dir),
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

    # --- queries ----------------------------------------------------------
    queries = manifest["queries"]
    if query_limit:
        queries = queries[:query_limit]
    result["queries"] = measure_queries(
        conn, embedder, queries, repeats=repeats, limit=limit, cfg=cfg
    )
    result["quality"] = evaluate_quality(conn, embedder, queries, cfg=cfg)

    pid = _llama_pid()
    if pid is not None:
        result["llama_server_rss_mb"] = round(current_rss_mb(pid) or 0.0, 1)

    conn.close()
    if service is not None:
        service.close()
    if not keep_db:
        _clean_db(db_path)
    return result


def _self_peak_rss() -> float | None:
    return peak_rss_mb()


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 1)


def _llama_pid() -> int | None:
    return llama_server_pid(paths.runtime_dir())
