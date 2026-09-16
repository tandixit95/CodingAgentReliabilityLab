from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals/release_gate_v1"

verify_spec = importlib.util.spec_from_file_location(
    "carl_release_gate_verify", EVAL_ROOT / "verify_protocol.py"
)
assert verify_spec and verify_spec.loader
verify = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verify)

eval_spec = importlib.util.spec_from_file_location(
    "carl_release_gate_eval", EVAL_ROOT / "evaluate.py"
)
assert eval_spec and eval_spec.loader
evaluator = importlib.util.module_from_spec(eval_spec)
eval_spec.loader.exec_module(evaluator)


def frozen_inputs():
    return json.loads(verify.PROTOCOL.read_text()), json.loads(verify.EVIDENCE.read_text())


def test_frozen_protocol_passes_without_combined_results():
    protocol, evidence = frozen_inputs()
    assert verify.validate(protocol, evidence, results_exist=False) == []
    assert verify.verify_source_identities(evidence) == []


def test_combined_result_is_prohibited_at_freeze():
    protocol, evidence = frozen_inputs()
    assert any(
        "RESULTS.json" in error for error in verify.validate(protocol, evidence, results_exist=True)
    )


def test_release_criteria_drift_fails_closed():
    protocol, evidence = frozen_inputs()
    changed = copy.deepcopy(protocol)
    changed["release_criteria"]["minimum_source_independent_runs"] = 1
    assert any(
        "release criteria" in error
        for error in verify.validate(changed, evidence, results_exist=False)
    )


def test_evidence_source_order_or_membership_drift_fails_closed():
    protocol, evidence = frozen_inputs()
    changed = copy.deepcopy(evidence)
    changed["sources"] = list(reversed(changed["sources"]))
    assert any(
        "source ids/order" in error
        for error in verify.validate(protocol, changed, results_exist=False)
    )


def _synthetic_source(tmp_path: Path, *, disposition: str = "pass", verifier_code: int = 0):
    protocol_path = tmp_path / "protocol.json"
    result_path = tmp_path / "result.json"
    verifier_path = tmp_path / "verify.py"
    protocol_path.write_text("{}\n")
    result = {
        "schema_version": "synthetic.publication.v1",
        "protocol_id": "synthetic-protocol",
        "freeze_commit_sha": "freeze",
        "protocol_sha256": evaluator._sha256(protocol_path),
        "aggregate_disposition": disposition,
        "frozen_gates": {"gate": {"passed": disposition == "pass"}},
        "reproduction": {
            "independent_runs_required": 2,
            "independent_runs_recorded": 2,
            "normalized_output_byte_identical": True,
        },
    }
    result_path.write_text(json.dumps(result, sort_keys=True) + "\n")
    verifier_path.write_text("# synthetic\n")
    source = {
        "id": "synthetic",
        "protocol_id": "synthetic-protocol",
        "freeze_commit_sha": "freeze",
        "result_commit_sha": "result",
        "protocol_path": "protocol.json",
        "protocol_sha256": evaluator._sha256(protocol_path),
        "result_path": "result.json",
        "result_sha256": evaluator._sha256(result_path),
        "result_schema_version": "synthetic.publication.v1",
        "result_verifier_path": "verify.py",
        "result_verifier_sha256": evaluator._sha256(verifier_path),
    }
    blobs = {
        ("freeze", "protocol.json"): protocol_path.read_bytes(),
        ("result", "result.json"): result_path.read_bytes(),
        ("result", "verify.py"): verifier_path.read_bytes(),
    }
    return source, blobs, verifier_code


def test_evaluator_accepts_exact_synthetic_source_without_touching_real_evidence(tmp_path):
    source, blobs, verifier_code = _synthetic_source(tmp_path)
    criteria = {
        "required_source_disposition": "pass",
        "minimum_source_independent_runs": 2,
    }
    result = evaluator.evaluate_source(
        tmp_path,
        source,
        criteria,
        verifier_runner=lambda _: (verifier_code, "PASS"),
        git_blob_reader=lambda _repo, commit, path: blobs.get((commit, path)),
    )
    assert result["passed"] is True
    assert result["errors"] == []


def test_evaluator_rejects_stale_current_result_bytes(tmp_path):
    source, blobs, _ = _synthetic_source(tmp_path)
    (tmp_path / "result.json").write_text("{}\n")
    result = evaluator.evaluate_source(
        tmp_path,
        source,
        {"required_source_disposition": "pass", "minimum_source_independent_runs": 2},
        verifier_runner=lambda _: (0, "PASS"),
        git_blob_reader=lambda _repo, commit, path: blobs.get((commit, path)),
    )
    assert result["passed"] is False
    assert "result_current_sha256_mismatch" in result["errors"]


def test_evaluator_preserves_negative_source_disposition(tmp_path):
    source, blobs, _ = _synthetic_source(tmp_path, disposition="fail")
    result = evaluator.evaluate_source(
        tmp_path,
        source,
        {"required_source_disposition": "pass", "minimum_source_independent_runs": 2},
        verifier_runner=lambda _: (0, "PASS"),
        git_blob_reader=lambda _repo, commit, path: blobs.get((commit, path)),
    )
    assert result["passed"] is False
    assert "source_disposition_not_release_ready" in result["errors"]
    assert "frozen_source_gate_failure" in result["errors"]


def test_evaluator_fails_closed_when_source_verifier_fails(tmp_path):
    source, blobs, _ = _synthetic_source(tmp_path, verifier_code=1)
    result = evaluator.evaluate_source(
        tmp_path,
        source,
        {"required_source_disposition": "pass", "minimum_source_independent_runs": 2},
        verifier_runner=lambda _: (1, "FAIL"),
        git_blob_reader=lambda _repo, commit, path: blobs.get((commit, path)),
    )
    assert result["passed"] is False
    assert "source_result_verifier_failed" in result["errors"]
