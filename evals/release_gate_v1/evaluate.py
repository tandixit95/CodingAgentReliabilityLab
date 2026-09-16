"""Evaluate frozen CARL evidence publications against a combined release gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PROTOCOL = ROOT / "PROTOCOL.json"
EVIDENCE = ROOT / "EVIDENCE.json"

VerifierRunner = Callable[[Path], tuple[int, str]]
GitBlobReader = Callable[[Path, str, str], bytes | None]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_blob(repo: Path, commit: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _run_verifier(path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output


def _check_blob_identity(
    *,
    repo: Path,
    commit: str,
    path: str,
    expected_sha256: str,
    git_blob_reader: GitBlobReader,
) -> tuple[bool, str]:
    payload = git_blob_reader(repo, commit, path)
    if payload is None:
        return False, "pinned_git_blob_missing"
    if _sha256_bytes(payload) != expected_sha256:
        return False, "pinned_git_blob_sha256_mismatch"
    return True, "ok"


def evaluate_source(
    repo: Path,
    source: dict[str, Any],
    criteria: dict[str, Any],
    *,
    verifier_runner: VerifierRunner = _run_verifier,
    git_blob_reader: GitBlobReader = _git_blob,
) -> dict[str, Any]:
    source_id = str(source.get("id", "unknown"))
    checks: dict[str, bool] = {}
    errors: list[str] = []

    identities = (
        (
            "protocol",
            source.get("freeze_commit_sha"),
            source.get("protocol_path"),
            source.get("protocol_sha256"),
        ),
        (
            "result",
            source.get("result_commit_sha"),
            source.get("result_path"),
            source.get("result_sha256"),
        ),
        (
            "result_verifier",
            source.get("result_commit_sha"),
            source.get("result_verifier_path"),
            source.get("result_verifier_sha256"),
        ),
    )
    for label, commit, relpath, expected_sha in identities:
        if not all(isinstance(value, str) and value for value in (commit, relpath, expected_sha)):
            checks[f"{label}_identity_complete"] = False
            errors.append(f"{label}_identity_incomplete")
            continue
        path = repo / relpath
        exists = path.is_file()
        checks[f"{label}_present"] = exists
        if not exists:
            errors.append(f"{label}_missing")
            continue
        current_matches = _sha256(path) == expected_sha
        checks[f"{label}_current_sha256_matches"] = current_matches
        if not current_matches:
            errors.append(f"{label}_current_sha256_mismatch")
        blob_ok, blob_error = _check_blob_identity(
            repo=repo,
            commit=commit,
            path=relpath,
            expected_sha256=expected_sha,
            git_blob_reader=git_blob_reader,
        )
        checks[f"{label}_pinned_commit_sha256_matches"] = blob_ok
        if not blob_ok:
            errors.append(f"{label}_{blob_error}")

    result_path = source.get("result_path")
    result: dict[str, Any] | None = None
    if isinstance(result_path, str) and (repo / result_path).is_file():
        try:
            loaded = json.loads((repo / result_path).read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, dict) else None
        except (json.JSONDecodeError, OSError):
            result = None
    checks["result_json_valid"] = result is not None
    if result is None:
        errors.append("result_json_invalid")
    else:
        expected_pairs = {
            "schema_version": source.get("result_schema_version"),
            "protocol_id": source.get("protocol_id"),
            "freeze_commit_sha": source.get("freeze_commit_sha"),
            "protocol_sha256": source.get("protocol_sha256"),
        }
        for key, expected in expected_pairs.items():
            matched = result.get(key) == expected
            checks[f"result_{key}_matches"] = matched
            if not matched:
                errors.append(f"result_{key}_mismatch")

        disposition_ok = result.get("aggregate_disposition") == criteria.get(
            "required_source_disposition"
        )
        checks["source_disposition_passes"] = disposition_ok
        if not disposition_ok:
            errors.append("source_disposition_not_release_ready")

        gates = result.get("frozen_gates")
        gates_ok = (
            isinstance(gates, dict)
            and bool(gates)
            and all(
                isinstance(item, dict) and item.get("passed") is True for item in gates.values()
            )
        )
        checks["all_frozen_source_gates_pass"] = gates_ok
        if not gates_ok:
            errors.append("frozen_source_gate_failure")

        reproduction = result.get("reproduction")
        reproduction_ok = isinstance(reproduction, dict)
        checks["reproduction_object_present"] = reproduction_ok
        if not reproduction_ok:
            errors.append("reproduction_missing")
        else:
            required = reproduction.get("independent_runs_required")
            recorded = reproduction.get("independent_runs_recorded")
            minimum = criteria.get("minimum_source_independent_runs")
            count_ok = (
                isinstance(required, int)
                and isinstance(recorded, int)
                and isinstance(minimum, int)
                and required >= minimum
                and recorded == required
            )
            byte_ok = reproduction.get("normalized_output_byte_identical") is True
            checks["source_independent_runs_satisfied"] = count_ok
            checks["source_normalized_output_byte_identical"] = byte_ok
            if not count_ok:
                errors.append("source_independent_runs_not_satisfied")
            if not byte_ok:
                errors.append("source_normalized_output_not_byte_identical")

    verifier_path = source.get("result_verifier_path")
    verifier_ok = False
    if isinstance(verifier_path, str) and (repo / verifier_path).is_file():
        returncode, _ = verifier_runner(repo / verifier_path)
        verifier_ok = returncode == 0
    checks["source_result_verifier_passes"] = verifier_ok
    if not verifier_ok:
        errors.append("source_result_verifier_failed")

    return {
        "source_id": source_id,
        "passed": not errors,
        "checks": dict(sorted(checks.items())),
        "errors": sorted(set(errors)),
    }


def evaluate_manifest(
    repo: Path,
    protocol: dict[str, Any],
    evidence: dict[str, Any],
    *,
    verifier_runner: VerifierRunner = _run_verifier,
    git_blob_reader: GitBlobReader = _git_blob,
) -> dict[str, Any]:
    criteria = protocol.get("release_criteria")
    sources = evidence.get("sources")
    if not isinstance(criteria, dict):
        raise TypeError("release_criteria must be an object")
    if not isinstance(sources, list):
        raise TypeError("evidence sources must be a list")

    source_results = [
        evaluate_source(
            repo,
            source,
            criteria,
            verifier_runner=verifier_runner,
            git_blob_reader=git_blob_reader,
        )
        for source in sources
        if isinstance(source, dict)
    ]
    required_count = criteria.get("required_source_count")
    count_ok = isinstance(required_count, int) and len(source_results) == required_count
    passed_count = sum(1 for item in source_results if item["passed"])
    release_ready = count_ok and passed_count == len(source_results) and bool(source_results)
    return {
        "schema_version": "carl.combined-evidence-release-run.v1",
        "protocol_id": protocol.get("protocol_id"),
        "evidence_set_id": evidence.get("evidence_set_id"),
        "metrics": {
            "source_count": len(source_results),
            "sources_passed": passed_count,
            "sources_failed": len(source_results) - passed_count,
            "required_source_count_satisfied": count_ok,
        },
        "source_results": source_results,
        "release_ready": release_ready,
    }


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    result = evaluate_manifest(REPO, protocol, evidence)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
