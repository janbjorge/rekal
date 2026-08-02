"""Unit tests for the benchmark grader helpers in benchmarks/bench.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

BENCH_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "bench.py"


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rekal_bench", BENCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grader_requires_strict_tiebreak(bench: ModuleType) -> None:
    grader = str(bench.GRADER)
    assert "STRICT" in grader
    assert "unsure between 1 and 2 -> 1" in grader
    assert "Length and confidence do not raise the score" in grader


def test_parse_verdict_clean_json(bench: ModuleType) -> None:
    payload = json.dumps({"result": json.dumps({"score": 1, "reason": "missing step"})})
    assert bench.parse_verdict(payload) == (1, "missing step")


def test_parse_verdict_fenced_json(bench: ModuleType) -> None:
    inner = '```json\n{"score": 2, "reason": "verified"}\n```'
    payload = json.dumps({"result": inner})
    assert bench.parse_verdict(payload) == (2, "verified")


def test_parse_verdict_prose_wrapper(bench: ModuleType) -> None:
    inner = 'Here you go: {"score": 0, "reason": "contradicts source"} thanks'
    payload = json.dumps({"result": inner})
    assert bench.parse_verdict(payload) == (0, "contradicts source")


def test_parse_verdict_unparseable(bench: ModuleType) -> None:
    assert bench.parse_verdict("not-json") == (0, "unparseable")
    assert bench.parse_verdict(json.dumps({"result": "no json object"})) == (0, "unparseable")
