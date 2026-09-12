from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "evals/reliability_v1/verify_results.py"
spec = importlib.util.spec_from_file_location("carl_eval_result_verify", VERIFY_PATH)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def published_inputs():
    protocol = json.loads(verify.PROTOCOL.read_text())
    scenarios = json.loads(verify.SCENARIOS.read_text())
    results = json.loads(verify.RESULTS.read_text())
    count = protocol["execution"]["independent_runs_required"]
    runs = tuple(
        (verify.ROOT / "artifacts" / f"run-{index}.json").read_bytes()
        for index in range(1, count + 1)
    )
    return protocol, scenarios, results, runs


def validate(protocol, scenarios, results, runs):
    return verify.validate(
        protocol,
        scenarios,
        results,
        runs,
        protocol_sha256=verify._sha256(verify.PROTOCOL),
        scenario_sha256=verify._sha256(verify.SCENARIOS),
    )


def test_published_result_matches_frozen_protocol_and_two_identical_runs():
    protocol, scenarios, results, runs = published_inputs()
    assert len(runs) == 2
    assert runs[0] == runs[1]
    assert validate(protocol, scenarios, results, runs) == []


def test_published_result_rejects_nonidentical_independent_run():
    protocol, scenarios, results, runs = published_inputs()
    changed = json.loads(runs[1])
    changed["scenario_results"][0]["detail"] += " changed"
    changed_bytes = (json.dumps(changed, indent=2, sort_keys=True) + "\n").encode()
    errors = validate(protocol, scenarios, results, (runs[0], changed_bytes))
    assert any("not byte-identical" in error for error in errors)


def test_published_metrics_cannot_drift_from_normalized_run():
    protocol, scenarios, results, runs = published_inputs()
    changed = copy.deepcopy(results)
    changed["metrics"]["scenario_pass_rate"] = 0.9
    errors = validate(protocol, scenarios, changed, runs)
    assert any("metrics differ" in error for error in errors)


def test_gate_disposition_is_derived_from_frozen_protocol_thresholds():
    protocol, scenarios, results, runs = published_inputs()
    changed = copy.deepcopy(results)
    changed["aggregate_disposition"] = "fail"
    errors = validate(protocol, scenarios, changed, runs)
    assert any("aggregate disposition" in error for error in errors)


def test_gate_derivation_preserves_a_negative_result_without_redefining_thresholds():
    protocol, _, _, _ = published_inputs()
    metrics = {
        "scenario_pass_rate": 10 / 11,
        "safety_pass_rate": 1.0,
        "convergence_pass_rate": 1.0,
        "fail_closed_pass_rate": 1.0,
        "duplicate_effect_violations": 0,
    }
    gates = verify.derive_gate_results(protocol, metrics)
    assert gates["scenario_pass_rate"] == {
        "actual": 10 / 11,
        "operator": "eq",
        "threshold": 1.0,
        "passed": False,
    }
    assert all(gates[name]["passed"] for name in gates if name != "scenario_pass_rate")
