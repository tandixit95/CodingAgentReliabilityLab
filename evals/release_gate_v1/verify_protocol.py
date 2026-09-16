"""Fail-closed verifier for the frozen CARL combined evidence-release protocol."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROTOCOL = ROOT / "PROTOCOL.json"
EVIDENCE = ROOT / "EVIDENCE.json"
EVALUATOR = ROOT / "evaluate.py"
RESULTS = ROOT / "RESULTS.json"
EXPECTED_PROTOCOL_SHA256 = "3aea2286f57b832490bbe85479d042ffa525877dd99d521f69286e6494097ca1"
EXPECTED_EVIDENCE_SHA256 = "99438a2c2ab2cef784df4d3a51ad7f114d695509d657ca25c502f8d74d15397f"
EXPECTED_EVALUATOR_SHA256 = "a0a2ca7c771312d70b4bd5f582c0db0ec272855c67e2d95aa136c03745e106c4"
EXPECTED_SOURCE_IDS = ("core_reliability_v1", "trace_integrity_v1")
EXPECTED_RELEASE_CRITERIA = {
    "minimum_source_independent_runs": 2,
    "require_current_result_bytes_match_pinned_commit": True,
    "require_exact_result_commit_identity": True,
    "require_source_frozen_gates_all_pass": True,
    "require_source_independent_runs_satisfied": True,
    "require_source_normalized_output_byte_identical": True,
    "require_source_result_protocol_identity_match_manifest": True,
    "require_source_result_verifier_pass": True,
    "required_source_count": 2,
    "required_source_disposition": "pass",
}
EXPECTED_CLAIM_BOUNDARY = {
    "distributed_consensus_claim_allowed": False,
    "exactly_once_claim_allowed": False,
    "production_reliability_claim_allowed": False,
    "production_trace_integrity_claim_allowed": False,
    "real_provider_claim_allowed": False,
    "release_ready_claim_allowed_before_two_reproducing_runs": False,
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_blob(commit: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def validate(
    protocol: dict[str, object], evidence: dict[str, object], *, results_exist: bool
) -> list[str]:
    errors: list[str] = []
    if protocol.get("schema_version") != "carl.combined-evidence-release-protocol.v1":
        errors.append("protocol schema_version mismatch")
    if protocol.get("protocol_id") != "combined-evidence-release-v1-20260916":
        errors.append("protocol_id mismatch")
    if protocol.get("status") != "frozen_unexecuted":
        errors.append("protocol must remain frozen_unexecuted at the freeze boundary")
    if protocol.get("release_criteria") != EXPECTED_RELEASE_CRITERIA:
        errors.append("release criteria differ from the frozen definition")
    if protocol.get("claim_boundary") != EXPECTED_CLAIM_BOUNDARY:
        errors.append("claim boundary differs from the frozen definition")
    if protocol.get("evidence_manifest") != {
        "path": "evals/release_gate_v1/EVIDENCE.json",
        "sha256": EXPECTED_EVIDENCE_SHA256,
        "source_count": 2,
    }:
        errors.append("evidence manifest identity differs from the frozen definition")
    if protocol.get("evaluator") != {
        "path": "evals/release_gate_v1/evaluate.py",
        "sha256": EXPECTED_EVALUATOR_SHA256,
    }:
        errors.append("evaluator identity differs from the frozen definition")
    if protocol.get("execution") != {
        "independent_runs_required": 2,
        "normalized_output_byte_equal_required": True,
        "result_path_pattern": "evals/release_gate_v1/artifacts/run-{run}.json",
        "aggregate_publication_requires_frozen_protocol_commit": True,
    }:
        errors.append("execution controls differ from the frozen definition")
    if protocol.get("result_artifact") != {
        "published_results_path": "evals/release_gate_v1/RESULTS.json",
        "must_be_absent_at_freeze": True,
    }:
        errors.append("result artifact boundary differs from the frozen definition")
    if results_exist:
        errors.append("RESULTS.json must not exist at the freeze boundary")

    if evidence.get("schema_version") != "carl.combined-evidence-manifest.v1":
        errors.append("evidence schema_version mismatch")
    if evidence.get("evidence_set_id") != "combined-release-evidence-v1-20260916":
        errors.append("evidence_set_id mismatch")
    sources = evidence.get("sources")
    if not isinstance(sources, list):
        errors.append("evidence sources must be a list")
        return errors
    ids = tuple(str(source.get("id")) for source in sources if isinstance(source, dict))
    if ids != EXPECTED_SOURCE_IDS:
        errors.append("evidence source ids/order differ from the frozen definition")
    if len(sources) != 2:
        errors.append("evidence source count mismatch")
    return errors


def verify_source_identities(evidence: dict[str, object]) -> list[str]:
    errors: list[str] = []
    sources = evidence.get("sources")
    if not isinstance(sources, list):
        return ["evidence sources must be a list"]
    for source in sources:
        if not isinstance(source, dict):
            errors.append("evidence source must be an object")
            continue
        source_id = str(source.get("id", "unknown"))
        identities = (
            (
                source.get("freeze_commit_sha"),
                source.get("protocol_path"),
                source.get("protocol_sha256"),
                "protocol",
            ),
            (
                source.get("result_commit_sha"),
                source.get("result_path"),
                source.get("result_sha256"),
                "result",
            ),
            (
                source.get("result_commit_sha"),
                source.get("result_verifier_path"),
                source.get("result_verifier_sha256"),
                "result_verifier",
            ),
        )
        for commit, relpath, expected_sha, label in identities:
            if not all(
                isinstance(value, str) and value for value in (commit, relpath, expected_sha)
            ):
                errors.append(f"{source_id} {label} identity incomplete")
                continue
            current = REPO / relpath
            if not current.is_file():
                errors.append(f"{source_id} {label} missing")
                continue
            if _sha256(current) != expected_sha:
                errors.append(f"{source_id} {label} current bytes are stale or mismatched")
            blob = _git_blob(commit, relpath)
            if blob is None:
                errors.append(f"{source_id} {label} pinned commit/path missing")
            elif _sha256_bytes(blob) != expected_sha:
                errors.append(f"{source_id} {label} pinned commit bytes mismatch")
    return errors


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    errors = validate(protocol, evidence, results_exist=RESULTS.exists())
    if _sha256(PROTOCOL) != EXPECTED_PROTOCOL_SHA256:
        errors.append("PROTOCOL.json bytes do not match the frozen SHA-256")
    if _sha256(EVIDENCE) != EXPECTED_EVIDENCE_SHA256:
        errors.append("EVIDENCE.json bytes do not match the frozen SHA-256")
    if _sha256(EVALUATOR) != EXPECTED_EVALUATOR_SHA256:
        errors.append("evaluator bytes do not match the frozen SHA-256")
    errors.extend(verify_source_identities(evidence))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: combined evidence-release v1 is frozen_unexecuted with two exact source publications, "
        "fixed release criteria, frozen evaluator bytes, and no combined result artifact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
