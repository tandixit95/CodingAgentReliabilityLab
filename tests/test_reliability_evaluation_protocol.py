from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from agent_reliability_lab.evaluation import SCENARIO_RUNNERS, ScenarioResult, summarize_results

ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "evals/reliability_v1/verify_protocol.py"
spec = importlib.util.spec_from_file_location("carl_eval_verify", VERIFY_PATH)
assert spec and spec.loader
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def frozen_inputs():
    return (
        json.loads(verify.PROTOCOL.read_text()),
        json.loads(verify.SCENARIOS.read_text()),
    )


def test_frozen_protocol_and_manifest_pass_without_results():
    protocol, scenarios = frozen_inputs()
    assert verify.validate(protocol, scenarios, results_exist=False) == []


def test_protocol_must_remain_unexecuted_at_freeze():
    protocol, scenarios = frozen_inputs()
    protocol["status"] = "executed"
    assert any(
        "frozen_unexecuted" in error
        for error in verify.validate(protocol, scenarios, results_exist=False)
    )


def test_result_artifact_is_prohibited_at_freeze():
    protocol, scenarios = frozen_inputs()
    assert any(
        "RESULTS.json" in error
        for error in verify.validate(protocol, scenarios, results_exist=True)
    )


def test_metric_threshold_drift_fails_closed():
    protocol, scenarios = frozen_inputs()
    protocol = copy.deepcopy(protocol)
    protocol["metrics"]["scenario_pass_rate"]["threshold"] = 0.9
    assert any(
        "metric gates" in error
        for error in verify.validate(protocol, scenarios, results_exist=False)
    )


def test_scenario_order_or_membership_drift_fails_closed():
    protocol, scenarios = frozen_inputs()
    scenarios = copy.deepcopy(scenarios)
    scenarios["scenarios"] = list(reversed(scenarios["scenarios"]))
    assert any(
        "ids/order" in error for error in verify.validate(protocol, scenarios, results_exist=False)
    )


def test_every_frozen_fixture_has_an_executable_registered_runner():
    _, scenarios = frozen_inputs()
    runners = [item["runner"] for item in scenarios["scenarios"]]
    assert runners == list(verify.EXPECTED_SCENARIO_IDS)
    assert set(runners) == set(SCENARIO_RUNNERS)


def test_aggregate_math_is_explicit_and_fail_closed():
    results = (
        ScenarioResult("a", True, True, True, True, True, False, None, 0, "ok"),
        ScenarioResult("b", False, True, False, False, None, True, True, 1, "blocked"),
    )
    metrics = summarize_results(results)
    assert metrics == {
        "scenario_count": 2,
        "scenario_passed": 1,
        "scenario_pass_rate": 0.5,
        "safety_applicable": 2,
        "safety_passed": 1,
        "safety_pass_rate": 0.5,
        "convergence_applicable": 1,
        "convergence_passed": 1,
        "convergence_pass_rate": 1.0,
        "fail_closed_applicable": 1,
        "fail_closed_passed": 1,
        "fail_closed_pass_rate": 1.0,
        "duplicate_effect_violations": 1,
        "all_scenarios_passed": False,
    }
