"""Tests for model presets."""

from __future__ import annotations

import pytest

from feagle.embeddings import presets


def test_q8_preset_matches_published_pin():
    spec = presets.get_preset("embeddinggemma-2-q8")
    assert spec.filename == "embeddinggemma-2-Q8_0.gguf"
    assert spec.size == 309_855_456
    assert spec.sha256 == "2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135"
    assert spec.dim == 768
    assert spec.context == 8192


def test_pooling_is_never_forced():
    for spec in presets.PRESETS.values():
        assert spec.pooling is None  # mean pooling lives in the GGUF


def test_urls_are_https_hf():
    for spec in presets.PRESETS.values():
        assert spec.url.startswith("https://huggingface.co/")
        assert spec.url.endswith(spec.filename)


def test_unknown_preset_lists_options():
    with pytest.raises(KeyError, match="available:"):
        presets.get_preset("does-not-exist")


def test_spec_for_path_returns_none():
    assert presets.spec_for("/abs/path/model.gguf") is None
    assert presets.spec_for("embeddinggemma-2-q8") is not None


def test_mrl_and_mmproj_presets_exist():
    assert presets.MRL_DIMS == (128, 256, 512, 768)
    assert presets.MMPROJ_PRESETS["embeddinggemma-2-mmproj-q8"].size == 554_821_024
