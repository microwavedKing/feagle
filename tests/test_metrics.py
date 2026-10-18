"""Tests for the benchmark measurement helpers."""

from __future__ import annotations

import os

from feagle import metrics


def test_peak_rss_of_this_process():
    value = metrics.peak_rss_mb()
    assert value is not None
    assert value > 0


def test_peak_rss_of_a_given_pid():
    value = metrics.peak_rss_mb(os.getpid())
    assert value is not None
    assert value > 0


def test_peak_rss_of_a_missing_pid_is_none():
    assert metrics.peak_rss_mb(999_999_999) is None


def test_current_rss_of_this_process():
    value = metrics.current_rss_mb(os.getpid())
    assert value is not None
    assert value > 0


def test_current_rss_of_a_missing_pid_is_none():
    assert metrics.current_rss_mb(999_999_999) is None


def test_llama_server_pid_reads_the_pid_file(tmp_path):
    (tmp_path / "llama.pid").write_text("12345\n", encoding="utf-8")
    assert metrics.llama_server_pid(tmp_path) == 12345


def test_llama_server_pid_missing_file_is_none(tmp_path):
    assert metrics.llama_server_pid(tmp_path) is None


def test_llama_server_pid_garbage_is_none(tmp_path):
    (tmp_path / "llama.pid").write_text("not a pid", encoding="utf-8")
    assert metrics.llama_server_pid(tmp_path) is None


def test_percentile_empty_is_zero():
    assert metrics.percentile([], 50) == 0.0


def test_percentile_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0]
    assert metrics.percentile(values, 0) == 10.0
    assert metrics.percentile(values, 100) == 40.0
    assert metrics.percentile([5.0], 50) == 5.0


def test_summarise_empty_is_zeros():
    assert metrics.summarise([]) == {
        "count": 0,
        "p50": 0.0,
        "p95": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mean": 0.0,
    }


def test_summarise_rounds():
    result = metrics.summarise([1.0, 2.0, 3.0])
    assert result["count"] == 3
    assert result["min"] == 1.0
    assert result["max"] == 3.0
    assert result["mean"] == 2.0
    assert result["p50"] == 2.0
