"""Frozen deterministic reliability-evaluation harness.

The aggregate suite is intentionally separate from the individual regression tests.
A protocol and machine-readable scenario manifest must be frozen before this module
is used to generate aggregate results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .approvals import ApprovalAction, ApprovalConflict, ApprovalEvent, replay_approval
from .durable import DurableSandbox, LocalOperation
from .effects import EffectRequest, replay_effects
from .events import Event, EventKind
from .evidence_lineage import ReconciliationEvidenceStore, SupersededReconciliationDecision
from .reconciliation import (
    ExternalOperation,
    ProviderEvidence,
    ProviderReadbackState,
    StaleProviderReadback,
    VersionedProviderState,
    VersionedProviderStore,
    apply_conditional_retry,
    reconcile_lost_ack,
)
from .recovery import CheckpointStore, RecoveryOperation, StaleCheckpoint
from .simulator import RunState, TerminalStateConflict, replay


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    safety_applicable: bool
    safety_passed: bool | None
    convergence_applicable: bool
    convergence_passed: bool | None
    fail_closed_applicable: bool
    fail_closed_passed: bool | None
    duplicate_effect_violations: int
    detail: str


def _result(
    scenario_id: str,
    *,
    passed: bool,
    safety: bool | None,
    convergence: bool | None,
    fail_closed: bool | None,
    duplicate_effect_violations: int = 0,
    detail: str,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        passed=passed,
        safety_applicable=safety is not None,
        safety_passed=safety,
        convergence_applicable=convergence is not None,
        convergence_passed=convergence,
        fail_closed_applicable=fail_closed is not None,
        fail_closed_passed=fail_closed,
        duplicate_effect_violations=duplicate_effect_violations,
        detail=detail,
    )


def _stale_child_completion_scoped() -> ScenarioResult:
    initial = RunState(active_run_id="parent", active_turn_id="parent-turn")
    events = (
        Event(1, "parent", "parent-turn", EventKind.OUTPUT, "partial"),
        Event(2, "child", "child-turn", EventKind.COMPLETED, "child-final"),
        Event(3, "parent", "parent-turn", EventKind.COMPLETED, "parent-final"),
    )
    state = replay(events, initial, mode="scoped").state
    ok = (
        state.terminal
        and not state.failed
        and state.output == "parent-final"
        and state.accepted_events == (1, 3)
        and state.ignored_events == (2,)
    )
    return _result(
        "stale_child_completion_scoped",
        passed=ok,
        safety=ok,
        convergence=ok,
        fail_closed=None,
        detail="foreign child completion is ignored and the parent reaches its own terminal state",
    )


def _lost_ack_retry_idempotency() -> ScenarioResult:
    requests = (
        EffectRequest(1, "write:plan", "write_file", "plan-v1"),
        EffectRequest(2, "write:plan", "write_file", "plan-v1"),
    )
    result = replay_effects(requests, mode="idempotent")
    ok = len(result.applied) == 1 and len(result.duplicates) == 1
    return _result(
        "lost_ack_retry_idempotency",
        passed=ok,
        safety=ok,
        convergence=ok,
        fail_closed=None,
        duplicate_effect_violations=0 if ok else max(0, len(result.applied) - 1),
        detail="exact replay is suppressed by the stable operation identity",
    )


def _terminal_conflict_fails_closed() -> ScenarioResult:
    initial = RunState(active_run_id="parent", active_turn_id="turn")
    events = (
        Event(1, "parent", "turn", EventKind.COMPLETED, "answer"),
        Event(2, "parent", "turn", EventKind.FAILED, "late-failure"),
    )
    blocked = False
    try:
        replay(events, initial, mode="scoped")
    except TerminalStateConflict:
        blocked = True
    return _result(
        "terminal_conflict_fails_closed",
        passed=blocked,
        safety=blocked,
        convergence=None,
        fail_closed=blocked,
        detail="incompatible same-turn terminal outcomes are rejected instead of last-write-wins",
    )


def _approval_mutation_fails_closed() -> ScenarioResult:
    events = (
        ApprovalEvent(1, ApprovalAction.REQUESTED, "write:plan", "write_file", "plan-v1"),
        ApprovalEvent(2, ApprovalAction.APPROVED, "write:plan", "write_file", "plan-v1"),
        ApprovalEvent(3, ApprovalAction.EXECUTE, "write:plan", "write_file", "plan-v2"),
    )
    blocked = False
    try:
        replay_approval(events)
    except ApprovalConflict:
        blocked = True
    return _result(
        "approval_mutation_fails_closed",
        passed=blocked,
        safety=blocked,
        convergence=None,
        fail_closed=blocked,
        detail="approval is bound to the exact operation, tool, and payload",
    )


def _durable_restart_exact_replay() -> ScenarioResult:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "sandbox.sqlite3"
        operation = LocalOperation("op-1", "record_note", "safe-local-note")
        first = DurableSandbox(database)
        first.request(operation)
        first.decide(operation, "approved")
        first_apply = first.execute(operation)
        restarted = DurableSandbox(database)
        second_apply = restarted.execute(operation)
        notes = restarted.notes()
    ok = first_apply and not second_apply and notes == (("op-1", "safe-local-note"),)
    return _result(
        "durable_restart_exact_replay",
        passed=ok,
        safety=ok,
        convergence=ok,
        fail_closed=None,
        duplicate_effect_violations=0 if ok else max(0, len(notes) - 1),
        detail="file-backed completion survives restart and exact replay does not repeat the local effect",
    )


def _stale_checkpoint_lineage_fails_closed() -> ScenarioResult:
    operations = (
        RecoveryOperation("op-1", "edit", "one"),
        RecoveryOperation("op-2", "test", "two"),
        RecoveryOperation("op-3", "publish", "three"),
    )
    with tempfile.TemporaryDirectory() as directory:
        store = CheckpointStore(Path(directory) / "recovery.sqlite3")
        start = store.start("run-1", operations)
        after_one = store.advance(start, "after-one", completed_count=1)
        after_two = store.advance(after_one, "after-two", completed_count=2)
        blocked = False
        try:
            store.resume(after_one, operations)
        except StaleCheckpoint:
            blocked = True
        remaining = store.resume(after_two, operations)
    converged = tuple(op.operation_id for op in remaining) == ("op-3",)
    ok = blocked and converged
    return _result(
        "stale_checkpoint_lineage_fails_closed",
        passed=ok,
        safety=blocked,
        convergence=converged,
        fail_closed=blocked,
        detail="stale checkpoint replay is rejected and recovery resumes only from the current lineage",
    )


def _provider_present_suppresses_retry() -> ScenarioResult:
    operation = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")
    evidence = ProviderEvidence(
        operation.provider,
        operation.operation_id,
        ProviderReadbackState.PRESENT,
        observed_tool=operation.tool,
        observed_payload=operation.payload,
        readback_revision=5,
    )
    decision = reconcile_lost_ack(operation, evidence)
    ok = decision.action == "accept_remote_commit" and not decision.should_retry
    return _result(
        "provider_present_suppresses_retry",
        passed=ok,
        safety=ok,
        convergence=ok,
        fail_closed=None,
        detail="authoritative PRESENT readback accepts the committed effect instead of replaying it",
    )


def _stale_readback_revision_fails_closed() -> ScenarioResult:
    operation = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")
    evidence = ProviderEvidence(
        operation.provider,
        operation.operation_id,
        ProviderReadbackState.ABSENT,
        readback_revision=4,
        idempotency_key=operation.operation_id,
        idempotency_effect_sha256=operation.effect_sha256,
    )
    decision = reconcile_lost_ack(operation, evidence)
    blocked = False
    try:
        apply_conditional_retry(
            operation,
            decision,
            VersionedProviderState(revision=5, effects=(operation,)),
        )
    except StaleProviderReadback:
        blocked = True
    return _result(
        "stale_readback_revision_fails_closed",
        passed=blocked,
        safety=blocked,
        convergence=None,
        fail_closed=blocked,
        detail="revision drift invalidates old absence evidence before a retry can commit",
    )


def _restart_evidence_supersedes_retry() -> ScenarioResult:
    operation = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "evidence.sqlite3"
        first = ReconciliationEvidenceStore(database)
        old = first.record(
            operation,
            "absent-4",
            ProviderEvidence(
                operation.provider,
                operation.operation_id,
                ProviderReadbackState.ABSENT,
                readback_revision=4,
                idempotency_key=operation.operation_id,
                idempotency_effect_sha256=operation.effect_sha256,
            ),
        )
        restarted = ReconciliationEvidenceStore(database)
        current = restarted.record(
            operation,
            "present-5",
            ProviderEvidence(
                operation.provider,
                operation.operation_id,
                ProviderReadbackState.PRESENT,
                observed_tool=operation.tool,
                observed_payload=operation.payload,
                readback_revision=5,
            ),
        )
        blocked = False
        try:
            restarted.apply_retry(
                operation, old.evidence_id, old.decision, VersionedProviderState(4)
            )
        except SupersededReconciliationDecision:
            blocked = True
        converged = current.revision == 5 and not current.decision.should_retry
    ok = blocked and converged
    return _result(
        "restart_evidence_supersedes_retry",
        passed=ok,
        safety=blocked,
        convergence=converged,
        fail_closed=blocked,
        detail="newer provider evidence durably supersedes pre-restart retry authority",
    )


def _repeated_lost_ack_converges() -> ScenarioResult:
    operation = ExternalOperation("provider-a", "op-loop", "create_record", "payload-v1")
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "evidence.sqlite3"
        first = ReconciliationEvidenceStore(database)
        retry = first.record(
            operation,
            "absent-4",
            ProviderEvidence(
                operation.provider,
                operation.operation_id,
                ProviderReadbackState.ABSENT,
                readback_revision=4,
                idempotency_key=operation.operation_id,
                idempotency_effect_sha256=operation.effect_sha256,
            ),
        )
        provider = first.apply_retry(
            operation, retry.evidence_id, retry.decision, VersionedProviderState(4)
        )
        restarted = ReconciliationEvidenceStore(database)
        current = restarted.record(
            operation,
            "present-5",
            ProviderEvidence(
                operation.provider,
                operation.operation_id,
                ProviderReadbackState.PRESENT,
                observed_tool=operation.tool,
                observed_payload=operation.payload,
                readback_revision=5,
            ),
        )
        completion = restarted.mark_reconciled(operation, current.evidence_id, current.decision)
        blocked = False
        try:
            restarted.apply_retry(operation, retry.evidence_id, retry.decision, provider)
        except SupersededReconciliationDecision:
            blocked = True
    effect_count = len(provider.effects)
    converged = completion.revision == 5 and effect_count == 1
    ok = blocked and converged
    return _result(
        "repeated_lost_ack_converges",
        passed=ok,
        safety=blocked and effect_count == 1,
        convergence=converged,
        fail_closed=blocked,
        duplicate_effect_violations=max(0, effect_count - 1),
        detail="fresh PRESENT evidence closes a lost-ack loop and supersedes stale retry authority",
    )


def _competing_retry_authorities_converge() -> ScenarioResult:
    operation = ExternalOperation("provider-a", "op-race", "create_record", "payload-v1")
    with tempfile.TemporaryDirectory() as directory:
        provider = VersionedProviderStore(Path(directory) / "provider.sqlite3")
        readback = provider.read(operation)
        evidence_store = ReconciliationEvidenceStore(Path(directory) / "evidence.sqlite3")
        retry = evidence_store.record(operation, "absent-0", readback)
        first = provider.conditional_apply(operation, retry.decision)
        stale_blocked = False
        try:
            provider.conditional_apply(operation, retry.decision)
        except StaleProviderReadback:
            stale_blocked = True
        present = provider.read(operation)
        current = evidence_store.record(operation, "present-1", present)
        completion = evidence_store.mark_reconciled(
            operation, current.evidence_id, current.decision
        )
        final = provider.snapshot(operation.provider)
    effect_count = len(final.effects)
    converged = (
        first.revision == 1
        and final.revision == 1
        and completion.revision == 1
        and not current.decision.should_retry
        and effect_count == 1
    )
    ok = stale_blocked and converged
    return _result(
        "competing_retry_authorities_converge",
        passed=ok,
        safety=stale_blocked and effect_count == 1,
        convergence=converged,
        fail_closed=stale_blocked,
        duplicate_effect_violations=max(0, effect_count - 1),
        detail="one revision-bound retry commits and stale competing authority is rejected before convergence",
    )


SCENARIO_RUNNERS: dict[str, Callable[[], ScenarioResult]] = {
    "stale_child_completion_scoped": _stale_child_completion_scoped,
    "lost_ack_retry_idempotency": _lost_ack_retry_idempotency,
    "terminal_conflict_fails_closed": _terminal_conflict_fails_closed,
    "approval_mutation_fails_closed": _approval_mutation_fails_closed,
    "durable_restart_exact_replay": _durable_restart_exact_replay,
    "stale_checkpoint_lineage_fails_closed": _stale_checkpoint_lineage_fails_closed,
    "provider_present_suppresses_retry": _provider_present_suppresses_retry,
    "stale_readback_revision_fails_closed": _stale_readback_revision_fails_closed,
    "restart_evidence_supersedes_retry": _restart_evidence_supersedes_retry,
    "repeated_lost_ack_converges": _repeated_lost_ack_converges,
    "competing_retry_authorities_converge": _competing_retry_authorities_converge,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_results(results: tuple[ScenarioResult, ...]) -> dict[str, int | float | bool]:
    if not results:
        raise ValueError("evaluation requires at least one scenario result")

    safety = [result for result in results if result.safety_applicable]
    convergence = [result for result in results if result.convergence_applicable]
    fail_closed = [result for result in results if result.fail_closed_applicable]
    passed_count = sum(result.passed for result in results)
    safety_passed = sum(result.safety_passed is True for result in safety)
    convergence_passed = sum(result.convergence_passed is True for result in convergence)
    fail_closed_passed = sum(result.fail_closed_passed is True for result in fail_closed)
    duplicates = sum(result.duplicate_effect_violations for result in results)

    return {
        "scenario_count": len(results),
        "scenario_passed": passed_count,
        "scenario_pass_rate": passed_count / len(results),
        "safety_applicable": len(safety),
        "safety_passed": safety_passed,
        "safety_pass_rate": safety_passed / len(safety) if safety else 1.0,
        "convergence_applicable": len(convergence),
        "convergence_passed": convergence_passed,
        "convergence_pass_rate": convergence_passed / len(convergence) if convergence else 1.0,
        "fail_closed_applicable": len(fail_closed),
        "fail_closed_passed": fail_closed_passed,
        "fail_closed_pass_rate": fail_closed_passed / len(fail_closed) if fail_closed else 1.0,
        "duplicate_effect_violations": duplicates,
        "all_scenarios_passed": passed_count == len(results),
    }


def run_scenario_manifest(manifest: dict[str, object]) -> tuple[ScenarioResult, ...]:
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        raise TypeError("scenario manifest must contain a scenarios list")
    results: list[ScenarioResult] = []
    for item in scenarios:
        if not isinstance(item, dict):
            raise TypeError("each scenario fixture must be an object")
        scenario_id = str(item.get("id", ""))
        runner_name = str(item.get("runner", ""))
        runner = SCENARIO_RUNNERS.get(runner_name)
        if runner is None:
            raise ValueError(f"unknown frozen scenario runner: {runner_name}")
        result = runner()
        if result.scenario_id != scenario_id:
            raise RuntimeError(
                f"scenario identity mismatch: fixture={scenario_id!r} runner={result.scenario_id!r}"
            )
        for fixture_key, result_value in (
            ("safety_required", result.safety_applicable),
            ("convergence_required", result.convergence_applicable),
            ("fail_closed_required", result.fail_closed_applicable),
        ):
            if item.get(fixture_key) is not result_value:
                raise RuntimeError(
                    f"scenario applicability mismatch for {scenario_id}: {fixture_key}"
                )
        results.append(result)
    return tuple(results)


def evaluate(protocol_path: Path, scenario_path: Path) -> dict[str, object]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = json.loads(scenario_path.read_text(encoding="utf-8"))
    if protocol.get("status") != "frozen_unexecuted":
        raise ValueError("aggregate execution requires the frozen_unexecuted protocol")
    frozen_manifest = protocol.get("scenario_manifest")
    if not isinstance(frozen_manifest, dict):
        raise TypeError("protocol scenario_manifest is missing")
    if sha256(scenario_path) != frozen_manifest.get("sha256"):
        raise ValueError("scenario manifest does not match the frozen SHA-256")
    results = run_scenario_manifest(manifest)
    metrics = summarize_results(results)
    return {
        "schema_version": "carl.reliability-evaluation-result.v1",
        "protocol_id": protocol["protocol_id"],
        "scenario_manifest_sha256": frozen_manifest["sha256"],
        "scenario_results": [asdict(result) for result in results],
        "metrics": metrics,
        "claim_boundary": protocol["claim_boundary"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate(args.protocol, args.scenarios)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PASS: wrote one normalized reliability-evaluation run to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
