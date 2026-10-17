"""Model presets: where to fetch the GGUF files and how to verify them.

Pins (size + SHA256) were read from the Hugging Face tree API on 2026-10-14 and
match the published ``lfs.oid`` values. ``feagle setup``/``doctor`` re-check the
pin against the live tree API before downloading.
"""

from __future__ import annotations

from dataclasses import dataclass

HF_BASE = "https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/main"


@dataclass(frozen=True)
class ModelSpec:
    """A downloadable GGUF embedding model (plus optional multimodal projector)."""

    name: str
    filename: str
    url: str
    sha256: str
    size: int
    dim: int
    context: int
    repo: str = "ggml-org/embeddinggemma-2-GGUF"
    pooling: str | None = None
    mmproj_filename: str | None = None
    mmproj_url: str | None = None
    mmproj_sha256: str | None = None
    mmproj_size: int | None = None
    #: Which prompt family the model expects (see ``embeddings/prompts.py``).
    prompt_family: str = "gemma"
    #: Suggested ``[search].max_distance`` for this model's distance scale. Used
    #: only while the config keeps the built-in default (see
    #: ``hybrid.effective_max_distance``).
    max_distance: float | None = None
    note: str = ""


PRESETS: dict[str, ModelSpec] = {
    "embeddinggemma-2-q8": ModelSpec(
        name="embeddinggemma-2-q8",
        filename="embeddinggemma-2-Q8_0.gguf",
        url=f"{HF_BASE}/embeddinggemma-2-Q8_0.gguf",
        sha256="2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135",
        size=309_855_456,
        dim=768,
        context=8192,
        pooling=None,  # mean pooling is baked into the GGUF; never pass --pooling
    ),
    "embeddinggemma-2-bf16": ModelSpec(
        name="embeddinggemma-2-bf16",
        filename="embeddinggemma-2-BF16.gguf",
        url=f"{HF_BASE}/embeddinggemma-2-BF16.gguf",
        sha256="68bae29d62fb8c7d23e98d21fd4662753ddd636e6b62b8a70dfe059a9844f216",
        size=557_950_176,
        dim=768,
        context=8192,
        pooling=None,
        note="BF16 weights; safe on CPU (llama.cpp computes in f32).",
    ),
    "embeddinggemma-2-q4": ModelSpec(
        name="embeddinggemma-2-q4",
        filename="embeddinggemma-2-UD-Q4_K_XL.gguf",
        url=(
            "https://huggingface.co/unsloth/embeddinggemma-2-GGUF/resolve/main/"
            "embeddinggemma-2-UD-Q4_K_XL.gguf"
        ),
        sha256="ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493",
        size=175_673_856,
        dim=768,
        context=8192,
        repo="unsloth/embeddinggemma-2-GGUF",
        pooling=None,
        note=(
            "4-bit dynamic quant from the unsloth mirror: about half the size and "
            "measurably faster to index, with the same retrieval quality as q8 on "
            "the benchmark corpus (see bench/results)."
        ),
    ),
    "qwen3-embedding-0.6b-q8": ModelSpec(
        name="qwen3-embedding-0.6b-q8",
        filename="Qwen3-Embedding-0.6B-Q8_0.gguf",
        url=(
            "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/"
            "Qwen3-Embedding-0.6B-Q8_0.gguf"
        ),
        sha256="06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439",
        size=639_150_592,
        dim=1024,
        context=32768,
        repo="Qwen/Qwen3-Embedding-0.6B-GGUF",
        pooling="last",  # the GGUF has no pooling baked in, unlike EmbeddingGemma
        prompt_family="qwen3",
        # Measured: correct pairs sit at L2 0.66-1.25 and unrelated ones at ~1.39,
        # so EmbeddingGemma's 0.8 cutoff would drop nearly every neighbour and
        # silently leave full-text-only results. The filter is close to a no-op
        # for this model - which is fine, RRF already handles the ranking.
        max_distance=1.3,
        note=(
            "Fallback model (0.6B, 1024-dim): queries need an "
            "'Instruct: …\\nQuery: …' prefix and documents none, and the server "
            "needs --pooling last. Measured against EmbeddingGemma in the README."
        ),
    ),
}

#: Multimodal projector presets (only used in the optional media phase).
MMPROJ_PRESETS: dict[str, ModelSpec] = {
    "embeddinggemma-2-mmproj-q8": ModelSpec(
        name="embeddinggemma-2-mmproj-q8",
        filename="mmproj-embeddinggemma-2-Q8_0.gguf",
        url=f"{HF_BASE}/mmproj-embeddinggemma-2-Q8_0.gguf",
        sha256="c4a8a52691ecef40618438928bdf9e68379b854e24166f292592353db0aab64f",
        size=554_821_024,
        dim=768,
        context=8192,
    ),
}

#: Matryoshka dimensions: the EmbeddingGemma sizes plus the Qwen3-Embedding
#: full size (a larger ``mrl_dim`` than the model's dimension is a no-op).
MRL_DIMS: tuple[int, ...] = (128, 256, 512, 768, 1024)

#: whisper.cpp speech-to-text models for audio transcription (not embeddings).
#: Keyed by filename so it matches ``[media].whisper_model`` (which is a
#: filename or an absolute path).
WHISPER_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
WHISPER_PRESETS: dict[str, ModelSpec] = {
    "ggml-base.bin": ModelSpec(
        name="ggml-base.bin",
        filename="ggml-base.bin",
        url=f"{WHISPER_BASE_URL}/ggml-base.bin",
        sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        size=147_951_465,
        dim=0,
        context=0,
        repo="ggerganov/whisper.cpp",
        note="multilingual whisper.cpp base model (~148 MB) for audio transcription.",
    ),
}


def whisper_spec(name_or_path: str) -> ModelSpec | None:
    """The whisper preset for ``name_or_path`` (a filename), or ``None``."""
    return WHISPER_PRESETS.get(name_or_path)


def get_preset(name: str) -> ModelSpec:
    """Return the preset by name, or raise :class:`KeyError` listing options."""
    try:
        return PRESETS[name]
    except KeyError:
        options = ", ".join(sorted(PRESETS))
        raise KeyError(f"unknown model preset {name!r}; available: {options}") from None


def is_preset(name_or_path: str) -> bool:
    return name_or_path in PRESETS


def spec_for(name_or_path: str) -> ModelSpec | None:
    """Return a spec for a preset name, or ``None`` for an explicit file path."""
    return PRESETS.get(name_or_path)
