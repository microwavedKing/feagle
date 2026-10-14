"""Tests for the environment checks."""

from __future__ import annotations

import json

import pytest

from feagle import config, doctor
from feagle.cli.__main__ import main


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return tmp_path


def test_check_python_statuses():
    assert doctor.check_python((3, 11, 0)).status == "fail"
    assert doctor.check_python((3, 12, 0)).status == "ok"
    assert doctor.check_python((3, 14, 8)).status == "ok"


def test_check_fts5_ok():
    assert doctor.check_fts5().status == "ok"


def test_check_sqlite_vec_ok():
    assert doctor.check_sqlite_vec().status == "ok"


def test_check_database_missing_warns(env):
    check = doctor.check_database()
    assert check.status == "warn"
    assert "feagle index" in check.hint


def test_check_model_missing_warns(env):
    assert doctor.check_model(config.default_config()).status == "warn"


def test_check_model_explicit_path_without_preset_ok(env, tmp_path):
    model = tmp_path / "custom.gguf"
    model.write_bytes(b"weights")
    cfg = config.default_config()
    cfg["embeddings"]["model"] = str(model)
    check = doctor.check_model(cfg)
    assert check.status == "ok"
    assert "custom.gguf" in check.message


def test_check_model_size_mismatch_fails(env):
    from feagle import paths
    from feagle.embeddings import presets

    cfg = config.default_config()
    cfg["embeddings"]["model"] = "embeddinggemma-2-q8"  # pin it, independent of the default
    spec = presets.get_preset("embeddinggemma-2-q8")
    target = paths.models_dir() / spec.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"tiny")

    check = doctor.check_model(cfg)
    assert check.status == "fail"
    assert "size" in check.message


def test_check_config_roots_status(env):
    cfg = config.default_config()
    assert doctor.check_config(cfg).status == "warn"
    cfg["index"]["roots"] = ["/some/dir"]
    assert doctor.check_config(cfg).status == "ok"


def test_check_inotify_reads_file(tmp_path):
    path = tmp_path / "max_user_watches"
    path.write_text("1024\n", encoding="utf-8")
    assert doctor.check_inotify(path).status == "warn"
    path.write_text("1048576\n", encoding="utf-8")
    assert doctor.check_inotify(path).status == "ok"


def test_check_power_detects_battery_by_type(tmp_path):
    supply = tmp_path / "power_supply"
    battery = supply / "CMB0"  # not a "BAT*" name, like some laptops
    battery.mkdir(parents=True)
    (battery / "type").write_text("Battery\n", encoding="utf-8")
    (battery / "status").write_text("Discharging\n", encoding="utf-8")
    ac = supply / "AC"
    ac.mkdir()
    (ac / "type").write_text("Mains\n", encoding="utf-8")

    check = doctor.check_power(supply)
    assert check.status == "info"
    assert "present" in check.message
    assert "on battery" in check.message


def test_check_power_without_a_battery(tmp_path):
    supply = tmp_path / "power_supply"
    (supply / "AC").mkdir(parents=True)
    (supply / "AC" / "type").write_text("Mains\n", encoding="utf-8")
    assert "none" in doctor.check_power(supply).message


def test_run_checks_returns_all_sections(env):
    checks = doctor.run_checks(config.default_config(), verify_model_hash=False)
    names = [c.name for c in checks]
    for expected in ("python", "config", "xdg", "fts5", "sqlite-vec", "database",
                     "units", "queryd", "llama-server", "model", "pdftotext", "ocr",
                     "launcher", "battery", "inotify", "cgroup", "disk"):
        assert expected in names
    assert all(c.status in ("ok", "warn", "fail", "info") for c in checks)


def test_doctor_cli_json(env, capsys):
    rc = main(["doctor", "--json", "--fast"])
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert any(item["name"] == "python" for item in payload)
    assert rc in (0, 1)


def test_doctor_cli_human(env, capsys):
    rc = main(["doctor", "--fast"])
    out = capsys.readouterr().out
    assert "python:" in out
    assert rc in (0, 1)
