"""Tests for battery and load-average checks."""

from __future__ import annotations

from feagle import config, power


def make_supply(root, name, supply_type, status):
    entry = root / name
    entry.mkdir()
    (entry / "type").write_text(supply_type, encoding="utf-8")
    (entry / "status").write_text(status, encoding="utf-8")
    return entry


def make_cfg(**overrides):
    cfg = config.default_config()
    cfg["index"].update(overrides)
    return cfg


def test_on_battery_true_when_discharging(tmp_path):
    make_supply(tmp_path, "AC", "Mains", "Not charging")
    make_supply(tmp_path, "BAT0", "Battery", "Discharging")
    assert power.on_battery(tmp_path) is True


def test_on_battery_false_when_charging(tmp_path):
    make_supply(tmp_path, "BAT0", "Battery", "Charging")
    assert power.on_battery(tmp_path) is False


def test_on_battery_false_without_battery(tmp_path):
    make_supply(tmp_path, "AC", "Mains", "Not charging")
    assert power.on_battery(tmp_path) is False


def test_on_battery_false_when_dir_missing(tmp_path):
    assert power.on_battery(tmp_path / "nope") is False


def test_load_average_parsing(tmp_path):
    path = tmp_path / "loadavg"
    path.write_text("1.25 0.80 0.50 1/234 5678\n", encoding="utf-8")
    assert power.load_average(path) == 1.25


def test_load_average_missing_file(tmp_path):
    assert power.load_average(tmp_path / "nope") == 0.0


def test_pause_reason_battery(tmp_path):
    make_supply(tmp_path, "BAT0", "Battery", "Discharging")
    reason = power.pause_reason(
        make_cfg(pause_on_battery=True, pause_on_loadavg=0),
        power_supply_dir=tmp_path,
        loadavg_path=tmp_path / "loadavg",
    )
    assert reason == "running on battery"


def test_pause_reason_loadavg(tmp_path):
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("9.50 1.00 1.00 1/10 100\n", encoding="utf-8")
    reason = power.pause_reason(
        make_cfg(pause_on_battery=False, pause_on_loadavg=4.0),
        power_supply_dir=tmp_path,
        loadavg_path=loadavg,
    )
    assert reason is not None and "load average" in reason


def test_pause_reason_none_when_all_clear(tmp_path):
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.10 0.10 0.10 1/10 100\n", encoding="utf-8")
    make_supply(tmp_path, "BAT0", "Battery", "Charging")
    assert (
        power.pause_reason(
            make_cfg(pause_on_battery=True, pause_on_loadavg=4.0),
            power_supply_dir=tmp_path,
            loadavg_path=loadavg,
        )
        is None
    )


def test_pause_reason_battery_disabled(tmp_path):
    make_supply(tmp_path, "BAT0", "Battery", "Discharging")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.1 0.1 0.1 1/1 1\n", encoding="utf-8")
    assert (
        power.pause_reason(
            make_cfg(pause_on_battery=False, pause_on_loadavg=0),
            power_supply_dir=tmp_path,
            loadavg_path=loadavg,
        )
        is None
    )
