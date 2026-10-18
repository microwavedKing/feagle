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


def test_check_model_hints_a_mismatched_cutoff(env, tmp_path, monkeypatch):
    from dataclasses import replace

    from feagle.embeddings import presets

    model = tmp_path / "m.gguf"
    model.write_bytes(b"weights")
    spec = replace(presets.get_preset("qwen3-embedding-0.6b-q8"), size=len(b"weights"))
    monkeypatch.setattr(doctor, "resolve_model", lambda cfg: (model, spec))

    cfg = config.default_config()
    cfg["embeddings"]["model"] = spec.name
    cfg["search"]["max_distance"] = 0.5  # explicit, and too tight for this model
    check = doctor.check_model(cfg, verify_hash=False)
    assert check.status == "ok"
    assert "max_distance" in check.hint


def test_check_model_no_hint_when_the_cutoff_fits(env, tmp_path, monkeypatch):
    from dataclasses import replace

    from feagle.embeddings import presets

    model = tmp_path / "m.gguf"
    model.write_bytes(b"weights")
    spec = replace(presets.get_preset("qwen3-embedding-0.6b-q8"), size=len(b"weights"))
    monkeypatch.setattr(doctor, "resolve_model", lambda cfg: (model, spec))

    cfg = config.default_config()  # keeps the built-in default: the preset's 1.3 wins
    cfg["embeddings"]["model"] = spec.name
    check = doctor.check_model(cfg, verify_hash=False)
    assert check.status == "ok"
    assert check.hint == ""


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


def test_check_embed_batch_follows_the_model_and_the_batch(env):
    cfg = config.default_config()
    assert doctor.check_embed_batch(cfg).status == "info"  # 768-dim, batch 16

    cfg["embeddings"]["model"] = "qwen3-embedding-0.6b-q8"  # 1024-dim
    check = doctor.check_embed_batch(cfg)
    assert check.status == "warn"
    assert "embed_batch_size" in check.hint
    assert "4" in check.hint

    cfg["index"]["embed_batch_size"] = 4
    assert doctor.check_embed_batch(cfg).status == "info"


def test_check_embeddings_url_is_info_when_local(env):
    cfg = config.default_config()
    assert doctor.check_embeddings_url(cfg).status == "info"
    cfg["embeddings"]["server_url"] = "http://127.0.0.1:8080"
    assert doctor.check_embeddings_url(cfg).status == "info"


def test_check_embeddings_url_warns_for_a_remote_host(env):
    cfg = config.default_config()
    cfg["embeddings"]["server_url"] = "http://gpu-box.lan:8080"
    check = doctor.check_embeddings_url(cfg)
    assert check.status == "warn"
    assert "unencrypted" in check.hint

    cfg["embeddings"]["server_url"] = "https://gpu-box.lan"
    assert doctor.check_embeddings_url(cfg).status == "info"


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
                     "media", "launcher", "battery", "inotify", "cgroup", "disk"):
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


def _patch_ocr(monkeypatch, *, tesseract=True, pdftoppm=True, langs=("rus", "eng")):
    from feagle.indexer import ocr

    monkeypatch.setattr(ocr, "tesseract_available", lambda: tesseract)
    monkeypatch.setattr(ocr, "pdftoppm_available", lambda: pdftoppm)
    monkeypatch.setattr(ocr, "tesseract_languages", lambda: set(langs))


def test_check_ocr_disabled_by_default(monkeypatch):
    _patch_ocr(monkeypatch)
    check = doctor.check_ocr(config.default_config())
    assert check.status == "ok"
    assert "disabled in config" in check.message


def test_check_ocr_images_work_without_pdftoppm(monkeypatch):
    _patch_ocr(monkeypatch, pdftoppm=False)
    cfg = config.default_config()
    cfg["index"]["ocr_images"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "ok"  # images need only tesseract


def test_check_ocr_pdf_enabled_without_pdftoppm_warns(monkeypatch):
    _patch_ocr(monkeypatch, pdftoppm=False)
    cfg = config.default_config()
    cfg["index"]["ocr"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "warn"
    assert "pdftoppm" in check.message


def test_check_ocr_missing_tesseract_warns_when_enabled(monkeypatch):
    _patch_ocr(monkeypatch, tesseract=False)
    cfg = config.default_config()
    cfg["index"]["ocr_images"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "warn"
    assert "tesseract" in check.message


def test_check_ocr_missing_languages(monkeypatch):
    _patch_ocr(monkeypatch, langs=("eng",))
    cfg = config.default_config()
    cfg["index"]["ocr_images"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "warn"
    assert "missing languages" in check.message


def test_check_ocr_osd_without_the_data_warns(monkeypatch):
    _patch_ocr(monkeypatch, langs=("rus", "eng"))
    cfg = config.default_config()
    cfg["index"]["ocr_osd"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "warn"
    assert "osd" in check.message


def test_check_ocr_osd_with_the_data_is_ok(monkeypatch):
    _patch_ocr(monkeypatch, langs=("rus", "eng", "osd"))
    cfg = config.default_config()
    cfg["index"]["ocr_osd"] = True
    check = doctor.check_ocr(cfg)
    assert check.status == "ok"
    assert "osd on" in check.message


def test_check_media_disabled():
    check = doctor.check_media(config.default_config())
    assert check.status == "info"
    assert "disabled" in check.message


def test_check_media_missing_mmproj(env):
    cfg = config.default_config()
    cfg["media"]["images"] = True
    check = doctor.check_media(cfg)
    assert check.status == "warn"
    assert "mmproj" in check.message


def test_check_media_ok(env, monkeypatch):
    from feagle import paths
    from feagle.indexer import media

    cfg = config.default_config()
    cfg["media"]["images"] = True
    target = paths.models_dir() / "mmproj-embeddinggemma-2-Q8_0.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    check = doctor.check_media(cfg)
    assert check.status == "ok"
    assert "images" in check.message


def test_check_media_audio_missing_whisper(env, monkeypatch):
    from feagle.indexer import asr, media

    cfg = config.default_config()
    cfg["media"]["audio"] = True
    monkeypatch.setattr(asr, "whisper_available", lambda binary=None: False)
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    check = doctor.check_media(cfg)
    assert check.status == "warn"
    assert "whisper-cli" in check.message


def test_check_media_video_ocr_missing_tesseract(env, monkeypatch):
    from feagle import paths
    from feagle.indexer import media, ocr

    cfg = config.default_config()
    cfg["media"]["video"] = True
    cfg["media"]["video_ocr"] = True
    target = paths.models_dir() / "mmproj-embeddinggemma-2-Q8_0.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    check = doctor.check_media(cfg)
    assert check.status == "warn"
    assert "tesseract" in check.message


class _Proc:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


def test_llama_devices_cpu_only(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/llama-server")
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: _Proc(stdout="Available devices:\n  (none)\n"),
    )
    check = doctor.check_llama_devices()
    assert check.status == "info"
    assert "CPU only" in check.message


def test_llama_devices_reports_gpu_and_hint(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/llama-server")
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *a, **k: _Proc(stdout="Available devices:\n  Vulkan0: AMD Radeon (1234 MiB)\n"),
    )
    check = doctor.check_llama_devices()
    assert check.status == "ok"
    assert "Vulkan0" in check.message
    assert "-ngl" in check.message


def test_llama_devices_without_binary(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    check = doctor.check_llama_devices()
    assert check.status == "info"
    assert "not installed" in check.message
