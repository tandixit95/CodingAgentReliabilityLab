from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from agent_reliability_lab.trace_integrity import apply_mutation, effect_fingerprint, validate_trace

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals/trace_integrity_v1"
VERIFY_PATH = EVAL_ROOT / "verify_protocol.py"
spec = importlib.util.spec_from_file_location("carl_trace_integrity_verify", VERIFY_PATH)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def frozen_inputs():
    return (
        json.loads(verify.PROTOCOL.read_text()),
        json.loads(verify.TRACE_SCHEMA.read_text()),
        json.loads(verify.BASELINES.read_text()),
        json.loads(verify.MUTATIONS.read_text()),
    )


def test_frozen_protocol_passes_without_aggregate_results():
    protocol, schema, baselines, mutations = frozen_inputs()
    assert verify.validate(protocol, schema, baselines, mutations, results_exist=False) == []


def test_protocol_must_remain_unexecuted_at_freeze():
    protocol, schema, baselines, mutations = frozen_inputs()
    protocol["status"] = "executed"
    assert any(
        "frozen_unexecuted" in error
        for error in verify.validate(protocol, schema, baselines, mutations, results_exist=False)
    )


def test_result_artifact_is_prohibited_at_freeze():
    protocol, schema, baselines, mutations = frozen_inputs()
    assert any(
        "RESULTS.json" in error
        for error in verify.validate(protocol, schema, baselines, mutations, results_exist=True)
    )


def test_detection_gate_drift_fails_closed():
    protocol, schema, baselines, mutations = frozen_inputs()
    protocol = copy.deepcopy(protocol)
    protocol["detection_gates"]["mutation_detection_rate"]["threshold"] = 0.9
    assert any(
        "detection gates" in error
        for error in verify.validate(protocol, schema, baselines, mutations, results_exist=False)
    )


def test_mutation_order_or_membership_drift_fails_closed():
    protocol, schema, baselines, mutations = frozen_inputs()
    mutations = copy.deepcopy(mutations)
    mutations["mutations"] = list(reversed(mutations["mutations"]))
    assert any(
        "mutation ids/order" in error
        for error in verify.validate(protocol, schema, baselines, mutations, results_exist=False)
    )


def test_every_frozen_mutation_applies_and_changes_its_baseline_without_running_detector():
    _, _, baselines, mutations = frozen_inputs()
    by_id = {trace["trace_id"]: trace for trace in baselines["traces"]}
    for mutation in mutations["mutations"]:
        base = by_id[mutation["base_trace_id"]]
        assert apply_mutation(base, mutation) != base


def test_detector_handles_synthetic_clean_event_and_foreign_run_without_corpus_execution():
    payload = "0" * 64
    clean = {
        "trace_id": "unit-clean",
        "scope": {
            "run_id": "run-1",
            "turn_id": "turn-1",
            "provider": "provider",
            "operation_id": "op-1",
        },
        "records": [
            {
                "record_id": "r1",
                "sequence": 1,
                "kind": "runtime_event",
                "event_id": "e1",
                "run_id": "run-1",
                "turn_id": "turn-1",
                "event_kind": "output",
                "payload_sha256": payload,
            }
        ],
    }
    assert validate_trace(clean) == ()
    changed = copy.deepcopy(clean)
    changed["records"][0]["run_id"] = "foreign"
    assert "event_run_scope_mismatch" in validate_trace(changed)


def test_effect_fingerprint_is_stable_and_identity_bound():
    payload = "a" * 64
    first = effect_fingerprint("provider", "op-1", "tool", payload)
    assert first == effect_fingerprint("provider", "op-1", "tool", payload)
    assert first != effect_fingerprint("provider", "op-2", "tool", payload)
