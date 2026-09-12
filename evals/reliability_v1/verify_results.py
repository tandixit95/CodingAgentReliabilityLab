"""Verify the published CARL reliability-evaluation v1 result without redefining gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "PROTOCOL.json"
SCENARIOS = ROOT / "SCENARIOS.json"
RESULTS = ROOT / "RESULTS.json"
FREEZE_COMMIT_SHA = "f11c5d441261fbbd07be23b806c53d7c685d8560"
EXPECTED_PROTOCOL_SHA256 = "bba37a4f3f5733bd51af35eae1286090dcd36929fbaf711a28ffccc0735819df"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _gate_passed(actual: float, operator: str, threshold: float) -> bool:
    if operator == "eq":
        return actual == threshold
    raise ValueError(f"unsupported frozen gate operator: {operator}")


def derive_gate_results(
    protocol: dict[str, Any], metrics: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    gates = protocol.get("metrics")
    if not isinstance(gates, dict):
        raise TypeError("protocol metrics must be an object")
    derived: dict[str, dict[str, Any]] = {}
    for name, specification in sorted(gates.items()):
        if not isinstance(specification, dict):
            raise TypeError(f"gate specification must be an object: {name}")
        operator = specification.get("operator")
        threshold = specification.get("threshold")
        actual = metrics.get(name)
        if not isinstance(operator, str):
            raise TypeError(f"gate operator must be a string: {name}")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise TypeError(f"gate threshold must be numeric: {name}")
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            raise TypeError(f"result metric must be numeric: {name}")
        derived[name] = {
            "actual": actual,
            "operator": operator,
            "threshold": threshold,
            "passed": _gate_passed(actual, operator, threshold),
        }
    return derived


def validate(
    protocol: dict[str, Any],
    scenarios: dict[str, Any],
    results: dict[str, Any],
    run_payloads: tuple[bytes, ...],
    *,
    protocol_sha256: str,
    scenario_sha256: str,
) -> list[str]:
    errors: list[str] = []
    execution = protocol.get("execution")
    if not isinstance(execution, dict):
        return ["protocol execution controls are missing"]
    required_runs = execution.get("independent_runs_required")
    if not isinstance(required_runs, int) or required_runs < 1:
        return ["independent_runs_required must be a positive integer"]
    if len(run_payloads) != required_runs:
        errors.append("published run count differs from the frozen requirement")

    if protocol_sha256 != EXPECTED_PROTOCOL_SHA256:
        errors.append("PROTOCOL.json bytes differ from the frozen commit")
    manifest = protocol.get("scenario_manifest")
    if not isinstance(manifest, dict):
        errors.append("scenario_manifest is missing")
    else:
        if scenario_sha256 != manifest.get("sha256"):
            errors.append("SCENARIOS.json bytes differ from the frozen manifest")
        scenario_items = scenarios.get("scenarios")
        if isinstance(scenario_items, list) and len(scenario_items) != manifest.get(
            "scenario_count"
        ):
            errors.append("scenario count differs from the frozen manifest")

    byte_equal = bool(run_payloads) and all(
        payload == run_payloads[0] for payload in run_payloads[1:]
    )
    if execution.get("normalized_output_byte_equal_required") is True and not byte_equal:
        errors.append("normalized independent run outputs are not byte-identical")

    try:
        normalized = json.loads(run_payloads[0]) if run_payloads else {}
    except json.JSONDecodeError:
        normalized = {}
        errors.append("normalized run artifact is not valid JSON")

    if results.get("schema_version") != "carl.reliability-evaluation-publication.v1":
        errors.append("result publication schema_version mismatch")
    if results.get("protocol_id") != protocol.get("protocol_id"):
        errors.append("result protocol_id mismatch")
    if results.get("freeze_commit_sha") != FREEZE_COMMIT_SHA:
        errors.append("freeze commit mismatch")
    if results.get("protocol_sha256") != protocol_sha256:
        errors.append("published protocol SHA-256 mismatch")
    if results.get("scenario_manifest_sha256") != scenario_sha256:
        errors.append("published scenario SHA-256 mismatch")
    if results.get("claim_boundary") != protocol.get("claim_boundary"):
        errors.append("published claim boundary differs from the frozen protocol")
    if results.get("metrics") != normalized.get("metrics"):
        errors.append("published metrics differ from the normalized run")
    if results.get("scenario_results") != normalized.get("scenario_results"):
        errors.append("published scenario results differ from the normalized run")

    expected_runs = [
        {
            "path": f"evals/reliability_v1/artifacts/run-{index}.json",
            "sha256": _sha256_bytes(payload),
        }
        for index, payload in enumerate(run_payloads, start=1)
    ]
    if results.get("run_artifacts") != expected_runs:
        errors.append("published run-artifact identities differ from the executed runs")

    reproduction = results.get("reproduction")
    expected_reproduction = {
        "independent_runs_required": required_runs,
        "independent_runs_recorded": len(run_payloads),
        "normalized_output_byte_identical": byte_equal,
        "normalized_output_sha256": _sha256_bytes(run_payloads[0]) if run_payloads else None,
    }
    if reproduction != expected_reproduction:
        errors.append("published reproduction summary differs from the executed runs")

    metrics = normalized.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("normalized run metrics are missing")
        derived: dict[str, dict[str, Any]] = {}
    else:
        try:
            derived = derive_gate_results(protocol, metrics)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            derived = {}
    if results.get("frozen_gates") != derived:
        errors.append("published gate results differ from the frozen gate evaluation")
    disposition = "pass" if derived and all(item["passed"] for item in derived.values()) else "fail"
    if results.get("aggregate_disposition") != disposition:
        errors.append("aggregate disposition differs from the frozen gate evaluation")
    return errors


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    execution = protocol["execution"]
    count = execution["independent_runs_required"]
    run_payloads = tuple(
        (ROOT / "artifacts" / f"run-{index}.json").read_bytes() for index in range(1, count + 1)
    )
    errors = validate(
        protocol,
        scenarios,
        results,
        run_payloads,
        protocol_sha256=_sha256(PROTOCOL),
        scenario_sha256=_sha256(SCENARIOS),
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "PASS: published reliability-evaluation v1 reproduces byte-for-byte across "
        f"{count} independent runs and matches every frozen gate/result identity"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
