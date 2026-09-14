from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "evals/trace_integrity_v1/verify_results.py"
spec = importlib.util.spec_from_file_location("carl_trace_integrity_result_verify", VERIFY_PATH)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def published_inputs():
    protocol = json.loads(verify.PROTOCOL.read_text())
    results = json.loads(verify.RESULTS.read_text())
    count = protocol["execution"]["independent_runs_required"]
    runs = tuple(
        (verify.ROOT / "artifacts" / f"run-{index}.json").read_bytes()
        for index in range(1, count + 1)
    )
    return protocol, results, runs


def validate(protocol, results, runs):
    return verify.validate(
        protocol,
        results,
        runs,
        protocol_sha256=verify._sha256(verify.PROTOCOL),
        detector_sha256=verify._sha256(verify.DETECTOR),
        schema_sha256=verify._sha256(verify.TRACE_SCHEMA),
        baselines_sha256=verify._sha256(verify.BASELINES),
        mutations_sha256=verify._sha256(verify.MUTATIONS),
    )


def test_published_result_matches_frozen_inputs_and_two_identical_runs():
    protocol, results, runs = published_inputs()
    assert len(runs) == 2
    assert runs[0] == runs[1]
    assert validate(protocol, results, runs) == []


def test_published_result_rejects_nonidentical_independent_run():
    protocol, results, runs = published_inputs()
    changed = json.loads(runs[1])
    changed["mutation_results"][0]["violations"].append("synthetic_change")
    changed_bytes = (json.dumps(changed, indent=2, sort_keys=True) + "\n").encode()
    errors = validate(protocol, results, (runs[0], changed_bytes))
    assert any("not byte-identical" in error for error in errors)


def test_published_metrics_cannot_drift_from_normalized_run():
    protocol, results, runs = published_inputs()
    changed = copy.deepcopy(results)
    changed["metrics"]["mutation_detection_rate"] = 14 / 15
    errors = validate(protocol, changed, runs)
    assert any("metrics differ" in error for error in errors)


def test_gate_disposition_is_derived_from_frozen_protocol_thresholds():
    protocol, results, runs = published_inputs()
    changed = copy.deepcopy(results)
    changed["aggregate_disposition"] = "fail"
    errors = validate(protocol, changed, runs)
    assert any("aggregate disposition" in error for error in errors)


def test_gate_derivation_preserves_negative_detection_result_without_redefining_thresholds():
    protocol, _, _ = published_inputs()
    metrics = {
        "clean_trace_accept_rate": 1.0,
        "detector_error_count": 0,
        "false_negative_count": 1,
        "false_positive_count": 0,
        "mutation_detection_rate": 14 / 15,
    }
    gates = verify.derive_gate_results(protocol, metrics)
    assert gates["mutation_detection_rate"]["passed"] is False
    assert gates["false_negative_count"]["passed"] is False
    assert gates["clean_trace_accept_rate"]["passed"] is True
