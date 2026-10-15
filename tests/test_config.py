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


@pytest.mark.parametrize(
    ("snippet", "expected_key"),
    [
        ("[search]\nmode = 'fuzzy'\n", "search.mode"),
        ("[search]\nlimit = 0\n", "search.limit"),
        ("[embeddings]\nmrl_dim = 300\n", "embeddings.mrl_dim"),
        ("[launcher]\nprogram = 'dmenu'\n", "launcher.program"),
        ("[index]\nmax_file_size_mb = -1\n", "index.max_file_size_mb"),
        ("[index]\nocr_images = 'yes'\n", "ocr_images"),
        ("[media]\nimages = 'yes'\n", "images"),
        ("[media]\nimage_max_dim = -1\n", "media.image_max_dim"),
        ("[media]\nvideo_max_frames = 0\n", "media.video_max_frames"),
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
