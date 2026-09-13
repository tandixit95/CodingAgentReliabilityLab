"""Fail-closed verifier for the frozen CARL trace-integrity v1 protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_reliability_lab.trace_integrity import apply_mutation

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROTOCOL = ROOT / "PROTOCOL.json"
TRACE_SCHEMA = ROOT / "TRACE_SCHEMA.json"
BASELINES = ROOT / "BASELINE_TRACES.json"
MUTATIONS = ROOT / "MUTATIONS.json"
RESULTS = ROOT / "RESULTS.json"
DETECTOR = REPO / "src/agent_reliability_lab/trace_integrity.py"

EXPECTED_PROTOCOL_ID = "trace-integrity-v1-20260913"
EXPECTED_DETECTOR_SHA256 = "d481d451106dbcf3f7fad7e89ce5aed12bdded26c179553150092524af0cd0e7"
EXPECTED_SCHEMA_SHA256 = "2d1281fc3c16dacca9eb2136c090417adeea160531b812e340a8a3fc6065a7cc"
EXPECTED_BASELINES_SHA256 = "9b2b62d25d849be3123c3b0212351e89c2876dbd8e861675a2a084e666f9c4c5"
EXPECTED_MUTATIONS_SHA256 = "ef4f7eb8bdc8170afe15eb6ae4bf59c5bd677477dde1c0d8e398ee8aeb448af2"
EXPECTED_BASELINE_IDS = ("retry_then_reconcile", "retry_authority_pending", "parent_turn_terminal")
EXPECTED_MUTATION_IDS = (
    "event_run_scope_graft",
    "event_turn_scope_graft",
    "event_id_collision",
    "record_sequence_collision",
    "approval_operation_swap",
    "effect_payload_hash_drift",
    "effect_fingerprint_drift",
    "checkpoint_parent_rewind",
    "checkpoint_sequence_gap",
    "checkpoint_plan_hash_drift",
    "provider_revision_rollback",
    "provider_same_revision_conflict",
    "completion_stale_evidence",
    "completion_revision_drift",
    "completion_effect_drift",
)
EXPECTED_CATEGORIES = (
    "event_identity",
    "trace_order",
    "approval_effect_binding",
    "checkpoint_lineage",
    "provider_revision",
    "reconciliation_completion",
)
EXPECTED_GATES = {
    "clean_trace_accept_rate": {"operator": "eq", "threshold": 1.0},
    "mutation_detection_rate": {"operator": "eq", "threshold": 1.0},
    "false_positive_count": {"operator": "eq", "threshold": 0},
    "false_negative_count": {"operator": "eq", "threshold": 0},
    "detector_error_count": {"operator": "eq", "threshold": 0},
}
EXPECTED_CLAIM_BOUNDARY = {
    "production_trace_integrity_claim_allowed": False,
    "production_reliability_claim_allowed": False,
    "exactly_once_claim_allowed": False,
    "distributed_consensus_claim_allowed": False,
    "real_provider_claim_allowed": False,
    "aggregate_detection_claim_allowed_before_two_reproducing_runs": False,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(
    protocol: dict[str, object],
    schema: dict[str, object],
    baselines: dict[str, object],
    mutations: dict[str, object],
    *,
    results_exist: bool,
) -> list[str]:
    errors: list[str] = []
    if protocol.get("schema_version") != "carl.trace-integrity-evaluation-protocol.v1":
        errors.append("protocol schema_version mismatch")
    if protocol.get("protocol_id") != EXPECTED_PROTOCOL_ID:
        errors.append("protocol_id mismatch")
    if protocol.get("status") != "frozen_unexecuted":
        errors.append("protocol must remain frozen_unexecuted at the freeze boundary")
    if protocol.get("detection_gates") != EXPECTED_GATES:
        errors.append("detection gates differ from the frozen definition")
    if protocol.get("claim_boundary") != EXPECTED_CLAIM_BOUNDARY:
        errors.append("claim boundary differs from the frozen definition")
    if protocol.get("execution") != {
        "independent_runs_required": 2,
        "normalized_output_byte_equal_required": True,
        "result_path_pattern": "evals/trace_integrity_v1/artifacts/run-{run}.json",
        "aggregate_publication_requires_frozen_protocol_commit": True,
    }:
        errors.append("execution controls differ from the frozen definition")
    if protocol.get("result_artifact") != {
        "published_results_path": "evals/trace_integrity_v1/RESULTS.json",
        "must_be_absent_at_freeze": True,
    }:
        errors.append("result artifact boundary differs from the frozen definition")
    if results_exist:
        errors.append("RESULTS.json must not exist at the freeze boundary")

    detector = protocol.get("detector")
    if detector != {
        "callable": "agent_reliability_lab.trace_integrity:evaluate_trace_corpus",
        "source_path": "src/agent_reliability_lab/trace_integrity.py",
        "sha256": EXPECTED_DETECTOR_SHA256,
    }:
        errors.append("detector identity differs from the frozen definition")

    artifacts = protocol.get("artifacts")
    expected_artifacts = {
        "trace_schema": {
            "path": "evals/trace_integrity_v1/TRACE_SCHEMA.json",
            "sha256": EXPECTED_SCHEMA_SHA256,
        },
        "baseline_traces": {
            "path": "evals/trace_integrity_v1/BASELINE_TRACES.json",
            "sha256": EXPECTED_BASELINES_SHA256,
            "trace_count": 3,
        },
        "mutation_corpus": {
            "path": "evals/trace_integrity_v1/MUTATIONS.json",
            "sha256": EXPECTED_MUTATIONS_SHA256,
            "mutation_count": 15,
        },
    }
    if artifacts != expected_artifacts:
        errors.append("artifact identities differ from the frozen definition")

    if schema.get("schema_version") != "carl.trace-integrity-schema.v1":
        errors.append("trace schema_version mismatch")
    record_kinds = schema.get("record_kinds")
    expected_kinds = {
        "runtime_event",
        "approval",
        "effect",
        "checkpoint",
        "provider_evidence",
        "reconciliation_completion",
    }
    if not isinstance(record_kinds, dict) or set(record_kinds) != expected_kinds:
        errors.append("trace schema record kinds differ from the frozen definition")

    if baselines.get("schema_version") != "carl.trace-integrity-baselines.v1":
        errors.append("baseline schema_version mismatch")
    traces = baselines.get("traces")
    if not isinstance(traces, list):
        errors.append("baseline traces must be a list")
        traces = []
    baseline_ids = tuple(str(trace.get("trace_id")) for trace in traces if isinstance(trace, dict))
    if baseline_ids != EXPECTED_BASELINE_IDS:
        errors.append("baseline ids/order differ from the frozen definition")
    by_id = {
        trace["trace_id"]: trace
        for trace in traces
        if isinstance(trace, dict) and "trace_id" in trace
    }

    if mutations.get("schema_version") != "carl.trace-integrity-mutations.v1":
        errors.append("mutation schema_version mismatch")
    items = mutations.get("mutations")
    if not isinstance(items, list):
        errors.append("mutations must be a list")
        items = []
    ids = tuple(str(item.get("id")) for item in items if isinstance(item, dict))
    if ids != EXPECTED_MUTATION_IDS:
        errors.append("mutation ids/order differ from the frozen definition")
    categories = tuple(
        dict.fromkeys(str(item.get("category")) for item in items if isinstance(item, dict))
    )
    if categories != EXPECTED_CATEGORIES:
        errors.append("mutation categories/order differ from the frozen definition")
    for item in items:
        if not isinstance(item, dict):
            errors.append("mutation entry must be an object")
            continue
        if item.get("operator") not in {"replace", "remove"}:
            errors.append(f"unsupported mutation operator: {item.get('id')}")
            continue
        base = by_id.get(item.get("base_trace_id"))
        if base is None:
            errors.append(f"mutation references unknown baseline: {item.get('id')}")
            continue
        try:
            mutated = apply_mutation(base, item)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            errors.append(f"mutation cannot be applied deterministically: {item.get('id')}: {exc}")
            continue
        if mutated == base:
            errors.append(f"mutation produced no structural change: {item.get('id')}")
    return errors


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    schema = json.loads(TRACE_SCHEMA.read_text(encoding="utf-8"))
    baselines = json.loads(BASELINES.read_text(encoding="utf-8"))
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    errors = validate(protocol, schema, baselines, mutations, results_exist=RESULTS.exists())
    for path, expected, label in (
        (DETECTOR, EXPECTED_DETECTOR_SHA256, "detector source"),
        (TRACE_SCHEMA, EXPECTED_SCHEMA_SHA256, "TRACE_SCHEMA.json"),
        (BASELINES, EXPECTED_BASELINES_SHA256, "BASELINE_TRACES.json"),
        (MUTATIONS, EXPECTED_MUTATIONS_SHA256, "MUTATIONS.json"),
    ):
        if _sha256(path) != expected:
            errors.append(f"{label} bytes do not match the frozen SHA-256")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: CARL trace-integrity v1 is frozen_unexecuted with 3 clean baselines, "
        "15 deterministic mutations, fixed detection gates, frozen detector bytes, "
        "and no aggregate result artifact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
