"""Deterministic trace-integrity validation and mutation application.

The validator operates on repository-authored trace records only. It does not
contact external providers or establish production correctness. The mutation
corpus that exercises this detector is frozen separately before aggregate
execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

_RECORD_KINDS = {
    "runtime_event",
    "approval",
    "effect",
    "checkpoint",
    "provider_evidence",
    "reconciliation_completion",
}
_EVENT_KINDS = {"output", "completed", "failed"}
_READBACK_STATES = {"present", "absent", "ambiguous"}
_DECISIONS = {"approved", "denied"}


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def effect_fingerprint(provider: str, operation_id: str, tool: str, payload_sha256: str) -> str:
    """Return the canonical trace-level fingerprint for one exact effect."""

    canonical = json.dumps(
        [provider, operation_id, tool, payload_sha256],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pointer_parent(document: object, pointer: str) -> tuple[object, str]:
    if not pointer.startswith("/"):
        raise ValueError("mutation path must be an absolute JSON pointer")
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    if not tokens or tokens == [""]:
        raise ValueError("mutation path must target a child value")
    current: object = document
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise TypeError(f"mutation path crosses a scalar at {token!r}")
    return current, tokens[-1]


def apply_mutation(trace: Mapping[str, Any], mutation: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one deterministic replace/remove mutation to a deep-copied trace."""

    mutated = copy.deepcopy(dict(trace))
    operator = mutation.get("operator")
    path = mutation.get("path")
    if not isinstance(path, str):
        raise TypeError("mutation path must be a string")
    parent, token = _pointer_parent(mutated, path)
    if operator == "replace":
        if "value" not in mutation:
            raise ValueError("replace mutation requires value")
        if isinstance(parent, list):
            parent[int(token)] = copy.deepcopy(mutation["value"])
        elif isinstance(parent, dict):
            if token not in parent:
                raise KeyError(token)
            parent[token] = copy.deepcopy(mutation["value"])
        else:
            raise ValueError("replace target parent must be a list or object")
    elif operator == "remove":
        if isinstance(parent, list):
            del parent[int(token)]
        elif isinstance(parent, dict):
            if token not in parent:
                raise KeyError(token)
            del parent[token]
        else:
            raise ValueError("remove target parent must be a list or object")
    else:
        raise ValueError(f"unsupported mutation operator: {operator!r}")
    return mutated


def validate_trace(trace: Mapping[str, Any]) -> tuple[str, ...]:
    """Return deterministic integrity violations for one canonical trace."""

    violations: set[str] = set()
    scope = trace.get("scope")
    records = trace.get("records")
    if not isinstance(scope, Mapping):
        return ("scope_missing_or_invalid",)
    if not isinstance(records, list):
        return ("records_missing_or_invalid",)

    run_id = scope.get("run_id")
    turn_id = scope.get("turn_id")
    provider = scope.get("provider")
    operation_id = scope.get("operation_id")
    for name, value in (
        ("run_id", run_id),
        ("turn_id", turn_id),
        ("provider", provider),
        ("operation_id", operation_id),
    ):
        if not isinstance(value, str) or not value:
            violations.add(f"scope_{name}_invalid")

    sequences: list[int] = []
    record_ids: list[str] = []
    event_ids: set[str] = set()
    binding: tuple[str, str] | None = None
    approved_sequence: int | None = None
    denied_sequence: int | None = None
    expected_effect: str | None = None
    checkpoints: list[Mapping[str, Any]] = []
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    evidence_history: list[Mapping[str, Any]] = []
    completion_count = 0

    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            violations.add(f"record_{index}_invalid")
            continue
        record_id = record.get("record_id")
        sequence = record.get("sequence")
        kind = record.get("kind")
        if not isinstance(record_id, str) or not record_id:
            violations.add("record_id_invalid")
        else:
            record_ids.append(record_id)
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            violations.add("record_sequence_invalid")
        else:
            sequences.append(sequence)
        if kind not in _RECORD_KINDS:
            violations.add("record_kind_invalid")
            continue

        if kind == "runtime_event":
            if record.get("run_id") != run_id:
                violations.add("event_run_scope_mismatch")
            if record.get("turn_id") != turn_id:
                violations.add("event_turn_scope_mismatch")
            event_id = record.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                violations.add("event_id_invalid")
            elif event_id in event_ids:
                violations.add("event_id_duplicate")
            else:
                event_ids.add(event_id)
            if record.get("event_kind") not in _EVENT_KINDS:
                violations.add("event_kind_invalid")
            if not _is_sha256(record.get("payload_sha256")):
                violations.add("event_payload_sha256_invalid")
            continue

        if kind == "checkpoint":
            if record.get("run_id") != run_id:
                violations.add("checkpoint_run_scope_mismatch")
            checkpoints.append(record)
            continue

        if record.get("operation_id") != operation_id:
            violations.add("operation_scope_mismatch")
        if record.get("provider") != provider:
            violations.add("provider_scope_mismatch")

        if kind in {"approval", "effect"}:
            tool = record.get("tool")
            payload_sha256 = record.get("payload_sha256")
            if not isinstance(tool, str) or not tool:
                violations.add("operation_tool_invalid")
            if not _is_sha256(payload_sha256):
                violations.add("operation_payload_sha256_invalid")
            if isinstance(tool, str) and _is_sha256(payload_sha256):
                candidate_binding = (tool, payload_sha256)
                if binding is None:
                    binding = candidate_binding
                    if isinstance(provider, str) and isinstance(operation_id, str):
                        expected_effect = effect_fingerprint(
                            provider, operation_id, tool, payload_sha256
                        )
                elif binding != candidate_binding:
                    violations.add("approval_effect_binding_mismatch")

        if kind == "approval":
            decision = record.get("decision")
            if decision not in _DECISIONS:
                violations.add("approval_decision_invalid")
            elif isinstance(sequence, int):
                if decision == "approved":
                    approved_sequence = sequence
                else:
                    denied_sequence = sequence
            if expected_effect is not None and record.get("effect_sha256") != expected_effect:
                violations.add("approval_effect_fingerprint_mismatch")
            continue

        if kind == "effect":
            if expected_effect is not None and record.get("effect_sha256") != expected_effect:
                violations.add("effect_fingerprint_mismatch")
            if isinstance(sequence, int):
                if approved_sequence is None or approved_sequence >= sequence:
                    violations.add("effect_without_prior_approval")
                if denied_sequence is not None and denied_sequence < sequence:
                    violations.add("effect_after_denial")
            continue

        if kind == "provider_evidence":
            evidence_id = record.get("evidence_id")
            revision = record.get("revision")
            readback = record.get("readback")
            if not isinstance(evidence_id, str) or not evidence_id:
                violations.add("provider_evidence_id_invalid")
            elif evidence_id in evidence_by_id:
                violations.add("provider_evidence_id_duplicate")
            else:
                evidence_by_id[evidence_id] = record
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                violations.add("provider_revision_invalid")
            if readback not in _READBACK_STATES:
                violations.add("provider_readback_invalid")
            if expected_effect is not None and record.get("effect_sha256") != expected_effect:
                violations.add("provider_effect_fingerprint_mismatch")
            if evidence_history:
                previous = evidence_history[-1]
                previous_revision = previous.get("revision")
                if isinstance(previous_revision, int) and isinstance(revision, int):
                    if revision < previous_revision:
                        violations.add("provider_revision_rollback")
                    elif revision == previous_revision and (
                        previous.get("readback") != readback
                        or previous.get("effect_sha256") != record.get("effect_sha256")
                    ):
                        violations.add("provider_same_revision_conflict")
            evidence_history.append(record)
            continue

        if kind == "reconciliation_completion":
            completion_count += 1
            evidence_id = record.get("evidence_id")
            revision = record.get("revision")
            evidence = evidence_by_id.get(evidence_id) if isinstance(evidence_id, str) else None
            if completion_count > 1:
                violations.add("multiple_reconciliation_completions")
            if expected_effect is not None and record.get("effect_sha256") != expected_effect:
                violations.add("completion_effect_fingerprint_mismatch")
            if evidence is None:
                violations.add("completion_evidence_missing")
            else:
                if evidence.get("readback") != "present":
                    violations.add("completion_requires_present_evidence")
                if evidence.get("revision") != revision:
                    violations.add("completion_revision_mismatch")
                if evidence.get("effect_sha256") != record.get("effect_sha256"):
                    violations.add("completion_evidence_effect_mismatch")
            revisions = [
                item.get("revision")
                for item in evidence_history
                if isinstance(item.get("revision"), int)
            ]
            if revisions and revision != max(revisions):
                violations.add("completion_not_latest_provider_revision")

    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        violations.add("record_sequence_not_strictly_increasing")
    if len(record_ids) != len(set(record_ids)):
        violations.add("record_id_duplicate")

    previous_checkpoint: Mapping[str, Any] | None = None
    for checkpoint in checkpoints:
        checkpoint_id = checkpoint.get("checkpoint_id")
        checkpoint_sequence = checkpoint.get("checkpoint_sequence")
        completed_count = checkpoint.get("completed_count")
        plan_sha256 = checkpoint.get("plan_sha256")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            violations.add("checkpoint_id_invalid")
        if not isinstance(checkpoint_sequence, int) or checkpoint_sequence < 0:
            violations.add("checkpoint_sequence_invalid")
        if not isinstance(completed_count, int) or completed_count < 0:
            violations.add("checkpoint_completed_count_invalid")
        if not _is_sha256(plan_sha256):
            violations.add("checkpoint_plan_sha256_invalid")
        if previous_checkpoint is None:
            if checkpoint.get("parent_checkpoint_id") is not None:
                violations.add("checkpoint_root_parent_not_null")
            if checkpoint_sequence != 0:
                violations.add("checkpoint_root_sequence_not_zero")
        else:
            if checkpoint.get("parent_checkpoint_id") != previous_checkpoint.get("checkpoint_id"):
                violations.add("checkpoint_parent_mismatch")
            previous_sequence = previous_checkpoint.get("checkpoint_sequence")
            if isinstance(previous_sequence, int) and checkpoint_sequence != previous_sequence + 1:
                violations.add("checkpoint_sequence_gap")
            if plan_sha256 != previous_checkpoint.get("plan_sha256"):
                violations.add("checkpoint_plan_drift")
            previous_completed = previous_checkpoint.get("completed_count")
            if (
                isinstance(previous_completed, int)
                and isinstance(completed_count, int)
                and completed_count < previous_completed
            ):
                violations.add("checkpoint_progress_rollback")
        previous_checkpoint = checkpoint

    return tuple(sorted(violations))


def evaluate_trace_corpus(
    baseline_document: Mapping[str, Any], mutation_document: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate clean traces and frozen mutations with deterministic ordering.

    This function is intentionally pure. The freeze milestone does not execute it
    against the frozen corpus; a later run can do so twice in independent
    processes after verifying the frozen commit and input hashes.
    """

    traces = baseline_document.get("traces")
    mutations = mutation_document.get("mutations")
    if not isinstance(traces, list) or not isinstance(mutations, list):
        raise TypeError("baseline and mutation documents must contain lists")
    by_id = {trace["trace_id"]: trace for trace in traces}
    clean_results = []
    for trace in traces:
        violations = validate_trace(trace)
        clean_results.append(
            {
                "trace_id": trace["trace_id"],
                "accepted": not violations,
                "violations": list(violations),
            }
        )
    mutation_results = []
    for mutation in mutations:
        base = by_id[mutation["base_trace_id"]]
        mutated = apply_mutation(base, mutation)
        violations = validate_trace(mutated)
        mutation_results.append(
            {
                "mutation_id": mutation["id"],
                "detected": bool(violations),
                "violations": list(violations),
            }
        )
    false_positives = sum(not item["accepted"] for item in clean_results)
    false_negatives = sum(not item["detected"] for item in mutation_results)
    clean_count = len(clean_results)
    mutation_count = len(mutation_results)
    return {
        "schema_version": "carl.trace-integrity-result.v1",
        "clean_results": clean_results,
        "mutation_results": mutation_results,
        "metrics": {
            "clean_trace_count": clean_count,
            "clean_trace_accept_rate": (clean_count - false_positives) / clean_count,
            "mutation_count": mutation_count,
            "mutation_detection_rate": (mutation_count - false_negatives) / mutation_count,
            "false_positive_count": false_positives,
            "false_negative_count": false_negatives,
            "detector_error_count": 0,
        },
    }
