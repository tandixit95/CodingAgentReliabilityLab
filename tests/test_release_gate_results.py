from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "evals/release_gate_v1"
spec = importlib.util.spec_from_file_location("verify_release_results", E / "verify_results.py")
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def loaded():
    p = json.loads((E / "PROTOCOL.json").read_text())
    r = json.loads((E / "RESULTS.json").read_text())
    runs = tuple((E / "artifacts" / f"run-{i}.json").read_bytes() for i in (1, 2))
    return p, r, runs


def test_published_result_verifies():
    p, r, runs = loaded()
    assert v.validate(p, r, runs) == []
    assert r["release_ready"] is True


def test_reproduction_is_byte_identical():
    _, r, runs = loaded()
    assert runs[0] == runs[1]
    assert r["reproduction"]["normalized_output_byte_identical"] is True


def test_release_ready_tamper_fails_closed():
    p, r, runs = loaded()
    r = dict(r)
    r["release_ready"] = False
    assert any("release_ready" in e for e in v.validate(p, r, runs))


def test_run_tamper_fails_closed():
    p, r, runs = loaded()
    bad = (runs[0], runs[1] + b" ")
    assert any("byte-identical" in e for e in v.validate(p, r, bad))
