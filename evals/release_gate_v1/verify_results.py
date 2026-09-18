"""Verify the published combined evidence-release v1 result against frozen identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "PROTOCOL.json"
EVIDENCE = ROOT / "EVIDENCE.json"
EVALUATOR = ROOT / "evaluate.py"
RESULTS = ROOT / "RESULTS.json"
FREEZE = "ac90e062d55f51afbf54eb8aa9ce0134b902377b"
P_SHA = "3aea2286f57b832490bbe85479d042ffa525877dd99d521f69286e6494097ca1"
E_SHA = "99438a2c2ab2cef784df4d3a51ad7f114d695509d657ca25c502f8d74d15397f"
V_SHA = "a0a2ca7c771312d70b4bd5f582c0db0ec272855c67e2d95aa136c03745e106c4"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def validate(protocol, results, runs):
    errors = []
    required = protocol["execution"]["independent_runs_required"]
    if sha(PROTOCOL) != P_SHA or sha(EVIDENCE) != E_SHA or sha(EVALUATOR) != V_SHA:
        errors.append("frozen protocol/evidence/evaluator identity drift")
    if len(runs) != required:
        errors.append("published run count differs from frozen requirement")
    equal = bool(runs) and all(x == runs[0] for x in runs[1:])
    if protocol["execution"]["normalized_output_byte_equal_required"] and not equal:
        errors.append("independent outputs are not byte-identical")
    try:
        normalized = json.loads(runs[0]) if runs else {}
    except json.JSONDecodeError:
        normalized = {}
        errors.append("run artifact invalid JSON")
    expected = {
        "schema_version": "carl.combined-evidence-release-publication.v1",
        "protocol_id": protocol["protocol_id"],
        "freeze_commit_sha": FREEZE,
        "protocol_sha256": P_SHA,
        "evidence_manifest_sha256": E_SHA,
        "evaluator_sha256": V_SHA,
        "release_ready": normalized.get("release_ready"),
        "metrics": normalized.get("metrics"),
        "source_results": normalized.get("source_results"),
        "claim_boundary": protocol["claim_boundary"],
    }
    for k, v in expected.items():
        if results.get(k) != v:
            errors.append(f"published {k} mismatch")
    runsha = hashlib.sha256(runs[0]).hexdigest() if runs else None
    if results.get("reproduction") != {
        "independent_runs_required": required,
        "independent_runs_recorded": len(runs),
        "normalized_output_byte_identical": equal,
        "normalized_output_sha256": runsha,
    }:
        errors.append("reproduction summary mismatch")
    artifacts = [
        {
            "path": f"evals/release_gate_v1/artifacts/run-{i}.json",
            "sha256": hashlib.sha256(x).hexdigest(),
        }
        for i, x in enumerate(runs, 1)
    ]
    if results.get("run_artifacts") != artifacts:
        errors.append("run artifact identities mismatch")
    sources = normalized.get("source_results") or []
    derived = (
        bool(normalized.get("metrics", {}).get("required_source_count_satisfied"))
        and len(sources) == protocol["release_criteria"]["required_source_count"]
        and all(s.get("passed") is True and not s.get("errors") for s in sources)
    )
    if normalized.get("release_ready") is not derived:
        errors.append("normalized release_ready differs from frozen composition criteria")
    return errors


def main():
    protocol = json.loads(PROTOCOL.read_text())
    results = json.loads(RESULTS.read_text())
    n = protocol["execution"]["independent_runs_required"]
    runs = tuple((ROOT / "artifacts" / f"run-{i}.json").read_bytes() for i in range(1, n + 1))
    errors = validate(protocol, results, runs)
    if errors:
        [print("FAIL:", e) for e in errors]
        return 1
    print(
        f"PASS: combined evidence-release v1 publication reproduces across {n} byte-identical runs; release_ready={results['release_ready']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
