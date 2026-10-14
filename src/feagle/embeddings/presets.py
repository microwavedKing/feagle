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

#: Matryoshka dimensions supported by EmbeddingGemma 2.
MRL_DIMS: tuple[int, ...] = (128, 256, 512, 768)


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
