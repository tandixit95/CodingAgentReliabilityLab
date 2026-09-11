"""Fail-closed verifier for the frozen CARL reliability-evaluation v1 protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "PROTOCOL.json"
SCENARIOS = ROOT / "SCENARIOS.json"
RESULTS = ROOT / "RESULTS.json"
EXPECTED_PROTOCOL_ID = "core-reliability-v1-20260911"
EXPECTED_SCENARIO_SHA256 = "5ca2ab5cd388037d056420e678004dd9909e479c11b84fbd6424b7a7d06c9044"
EXPECTED_SCENARIO_IDS = (
    "stale_child_completion_scoped",
    "lost_ack_retry_idempotency",
    "terminal_conflict_fails_closed",
    "approval_mutation_fails_closed",
    "durable_restart_exact_replay",
    "stale_checkpoint_lineage_fails_closed",
    "provider_present_suppresses_retry",
    "stale_readback_revision_fails_closed",
    "restart_evidence_supersedes_retry",
    "repeated_lost_ack_converges",
    "competing_retry_authorities_converge",
)
EXPECTED_METRICS = {
    "scenario_pass_rate": {"operator": "eq", "threshold": 1.0},
    "safety_pass_rate": {"operator": "eq", "threshold": 1.0},
    "convergence_pass_rate": {"operator": "eq", "threshold": 1.0},
    "fail_closed_pass_rate": {"operator": "eq", "threshold": 1.0},
    "duplicate_effect_violations": {"operator": "eq", "threshold": 0},
}
EXPECTED_CLAIM_BOUNDARY = {
    "production_reliability_claim_allowed": False,
    "exactly_once_claim_allowed": False,
    "distributed_consensus_claim_allowed": False,
    "model_quality_claim_allowed": False,
    "throughput_or_latency_claim_allowed": False,
    "aggregate_score_claim_allowed_before_two_reproducing_runs": False,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(
    protocol: dict[str, object], scenarios: dict[str, object], *, results_exist: bool
) -> list[str]:
    errors: list[str] = []
    if protocol.get("schema_version") != "carl.reliability-evaluation-protocol.v1":
        errors.append("protocol schema_version mismatch")
    if protocol.get("protocol_id") != EXPECTED_PROTOCOL_ID:
        errors.append("protocol_id mismatch")
    if protocol.get("status") != "frozen_unexecuted":
        errors.append("protocol must remain frozen_unexecuted at the freeze boundary")
    manifest = protocol.get("scenario_manifest")
    if not isinstance(manifest, dict):
        errors.append("scenario_manifest must be an object")
    else:
        if manifest.get("path") != "evals/reliability_v1/SCENARIOS.json":
            errors.append("scenario manifest path mismatch")
        if manifest.get("sha256") != EXPECTED_SCENARIO_SHA256:
            errors.append("scenario manifest SHA-256 mismatch")
        if manifest.get("scenario_count") != len(EXPECTED_SCENARIO_IDS):
            errors.append("scenario_count mismatch")
    execution = protocol.get("execution")
    expected_execution = {
        "independent_runs_required": 2,
        "normalized_output_byte_equal_required": True,
        "result_path_pattern": "evals/reliability_v1/artifacts/run-{run}.json",
        "aggregate_publication_requires_frozen_protocol_commit": True,
    }
    if execution != expected_execution:
        errors.append("execution controls differ from the frozen definition")
    if protocol.get("metrics") != EXPECTED_METRICS:
        errors.append("aggregate metric gates differ from the frozen definition")
    if protocol.get("claim_boundary") != EXPECTED_CLAIM_BOUNDARY:
        errors.append("claim boundary differs from the frozen definition")
    result_artifact = protocol.get("result_artifact")
    if result_artifact != {
        "published_results_path": "evals/reliability_v1/RESULTS.json",
        "must_be_absent_at_freeze": True,
    }:
        errors.append("result artifact boundary differs from the frozen definition")
    if results_exist:
        errors.append("RESULTS.json must not exist at the freeze boundary")
    if scenarios.get("schema_version") != "carl.reliability-scenarios.v1":
        errors.append("scenario schema_version mismatch")
    if scenarios.get("fixture_set") != "core-reliability-boundaries-20260911":
        errors.append("fixture_set mismatch")
    items = scenarios.get("scenarios")
    if not isinstance(items, list):
        errors.append("scenarios must be a list")
    else:
        ids = tuple(str(item.get("id")) for item in items if isinstance(item, dict))
        runners = tuple(str(item.get("runner")) for item in items if isinstance(item, dict))
        if ids != EXPECTED_SCENARIO_IDS:
            errors.append("scenario ids/order differ from the frozen definition")
        if runners != EXPECTED_SCENARIO_IDS:
            errors.append("scenario runner ids/order differ from the frozen definition")
        if len(items) != len(EXPECTED_SCENARIO_IDS):
            errors.append("scenario fixture count mismatch")
    return errors


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    errors = validate(protocol, scenarios, results_exist=RESULTS.exists())
    if _sha256(SCENARIOS) != EXPECTED_SCENARIO_SHA256:
        errors.append("SCENARIOS.json bytes do not match the frozen SHA-256")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: CARL reliability-evaluation v1 is frozen_unexecuted with "
        "11 scenarios, fixed metric gates, and no aggregate result artifact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
