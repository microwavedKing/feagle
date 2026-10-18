"""Tests for configuration loading, merging and validation."""

from __future__ import annotations

import logging
import tomllib

import pytest

from feagle import config


def test_missing_file_yields_defaults(tmp_path):
    cfg = config.load_config(tmp_path / "absent.toml")
    assert cfg == config.default_config()


def test_default_config_is_valid(tmp_path):
    config.validate(config.default_config(), source=tmp_path / "x.toml")


def test_merge_overrides_nested_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[index]\nroots = ['/home/user/docs']\nmax_file_size_mb = 5\n\n"
        "[search]\nlimit = 7\n",
        encoding="utf-8",
    )
    cfg = config.load_config(path)
    assert cfg["index"]["roots"] == ["/home/user/docs"]
    assert cfg["index"]["max_file_size_mb"] == 5
    assert cfg["search"]["limit"] == 7
    # untouched defaults survive the merge
    assert cfg["search"]["mode"] == "auto"


def test_unknown_key_warns(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text("[index]\nmystery = 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        cfg = config.load_config(path)
    assert cfg["index"]["mystery"] == 1  # preserved, but flagged
    assert any("unknown config key" in rec.message for rec in caplog.records)


def test_custom_opener_extensions_do_not_warn(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        "[openers]\ndefault = 'xdg-open {path}'\ntxt = 'nvim +{line} {path}'\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        cfg = config.load_config(path)
    assert cfg["openers"]["txt"] == "nvim +{line} {path}"
    assert not any("unknown config key" in rec.message for rec in caplog.records)


def _warned(caplog, needle: str) -> bool:
    return any(needle in rec.message for rec in caplog.records)


def test_remote_embeddings_server_warns(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nserver_url = "http://gpu-box.lan:8080"\n', encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert _warned(caplog, "server_url")
    assert _warned(caplog, "unencrypted")


def test_local_embeddings_server_does_not_warn(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text('[embeddings]\nserver_url = "http://127.0.0.1:8080"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert not _warned(caplog, "server_url")


def test_embeddings_host_normalisation():
    assert config.embeddings_host("") is None
    assert config.embeddings_host("http://localhost:8080") is None
    assert config.embeddings_host("http://127.0.0.1:8080") is None
    assert config.embeddings_host("https://box.lan") == "box.lan"
    assert config.embeddings_host("box.lan:8080") == "box.lan"


def test_max_distance_from_another_model_warns(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nmodel = "qwen3-embedding-0.6b-q8"\n\n[search]\nmax_distance = 0.5\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert _warned(caplog, "max_distance")


def test_max_distance_equal_to_the_default_does_not_warn(tmp_path, caplog):
    # 0.8 is the built-in default, so the preset's scale wins at query time.
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nmodel = "qwen3-embedding-0.6b-q8"\n\n[search]\nmax_distance = 0.8\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert not _warned(caplog, "max_distance")


def test_matching_max_distance_does_not_warn(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nmodel = "qwen3-embedding-0.6b-q8"\n\n[search]\nmax_distance = 1.3\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert not _warned(caplog, "max_distance")


def test_default_max_distance_with_a_preset_does_not_warn(tmp_path, caplog):
    # The built-in default is replaced by the preset's own scale at query time,
    # so it is not a mismatch.
    path = tmp_path / "config.toml"
    path.write_text('[embeddings]\nmodel = "qwen3-embedding-0.6b-q8"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert not _warned(caplog, "max_distance")


def test_gemma_has_no_suggested_cutoff_so_any_value_is_fine(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nmodel = "embeddinggemma-2-q8"\n\n[search]\nmax_distance = 0.5\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert not _warned(caplog, "max_distance")


def test_mrl_dim_above_the_model_dimension_warns(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        '[embeddings]\nmodel = "embeddinggemma-2-q8"\nmrl_dim = 1024\n', encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="feagle.config"):
        config.load_config(path)
    assert _warned(caplog, "mrl_dim")


@pytest.mark.parametrize(
    ("snippet", "expected_key"),
    [
        ("[search]\nmode = 'fuzzy'\n", "search.mode"),
        ("[search]\nlimit = 0\n", "search.limit"),
        ("[embeddings]\nmrl_dim = 300\n", "embeddings.mrl_dim"),
        ("[launcher]\nprogram = 'dmenu'\n", "launcher.program"),
        ("[index]\nmax_file_size_mb = -1\n", "index.max_file_size_mb"),
        ("[index]\nocr_images = 'yes'\n", "ocr_images"),
        ("[index]\nocr_workers = 9\n", "ocr_workers"),
        ("[index]\nocr_workers = 0\n", "ocr_workers"),
        ("[index]\nocr_psm = 'page'\n", "ocr_psm"),
        ("[index]\nocr_psm = 14\n", "ocr_psm"),
        ("[index]\nocr_osd = 'yes'\n", "ocr_osd"),
        ("[media]\nimages = 'yes'\n", "images"),
        ("[media]\nimage_max_dim = -1\n", "media.image_max_dim"),
        ("[media]\nvideo_max_frames = 0\n", "media.video_max_frames"),
        ("[media]\nvideo_ocr = 'yes'\n", "video_ocr"),
        ("[media]\naudio = 'yes'\n", "audio"),
    ],
)
def test_invalid_values_raise(tmp_path, snippet, expected_key):
    path = tmp_path / "config.toml"
    path.write_text(snippet, encoding="utf-8")
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert expected_key in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_openers_default_must_not_be_empty(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[openers]\ndefault = ''\n", encoding="utf-8")
    with pytest.raises(config.ConfigError) as excinfo:
        config.load_config(path)
    assert "openers.default" in str(excinfo.value)


def test_invalid_toml_raises_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is [not valid", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_bool_not_accepted_as_int(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[search]\nlimit = true\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_render_defaults_is_valid_documented_toml():
    data = tomllib.loads(config.render_defaults())
    assert data == config.DEFAULTS


def test_mrl_dim_1024_is_accepted(tmp_path):
    """Qwen3-Embedding is 1024-dim, so its full size must be allowed."""
    path = tmp_path / "config.toml"
    path.write_text("[embeddings]\nmrl_dim = 1024\n", encoding="utf-8")
    assert config.load_config(path)["embeddings"]["mrl_dim"] == 1024


def test_mrl_dims_come_from_the_presets():
    from feagle.embeddings import presets

    assert config.MRL_DIMS is presets.MRL_DIMS
