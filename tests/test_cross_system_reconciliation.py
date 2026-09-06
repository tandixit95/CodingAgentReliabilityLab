from __future__ import annotations

import pytest

from agent_reliability_lab.reconciliation import (
    ExternalOperation,
    ProviderEvidence,
    ProviderReadbackState,
    ReconciliationConflict,
    RemoteStateAmbiguous,
    RetrySafetyUnknown,
    reconcile_lost_ack,
)

OP = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")


def exact_absent_evidence(operation: ExternalOperation = OP) -> ProviderEvidence:
    return ProviderEvidence(
        provider=operation.provider,
        operation_id=operation.operation_id,
        readback=ProviderReadbackState.ABSENT,
        idempotency_key=operation.operation_id,
        idempotency_effect_sha256=operation.effect_sha256,
    )


def test_lost_ack_matching_readback_suppresses_duplicate_retry():
    provider_effects = [OP]  # effect committed; acknowledgement was lost before local completion
    naive_retry = provider_effects + [OP]
    assert len(naive_retry) == 2

    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
    )
    decision = reconcile_lost_ack(OP, evidence)
    assert decision.action == "accept_remote_commit"
    assert decision.should_retry is False


def test_confirmed_absence_with_exact_idempotency_evidence_allows_retry():
    decision = reconcile_lost_ack(OP, exact_absent_evidence())
    assert decision.action == "retry_exact_operation"
    assert decision.should_retry is True


def test_confirmed_absence_without_idempotency_evidence_fails_closed():
    evidence = ProviderEvidence(OP.provider, OP.operation_id, ProviderReadbackState.ABSENT)
    with pytest.raises(RetrySafetyUnknown, match="replay safety"):
        reconcile_lost_ack(OP, evidence)


@pytest.mark.parametrize(
    ("idempotency_key", "effect_sha256"),
    [("other-key", OP.effect_sha256), (OP.operation_id, "0" * 64)],
)
def test_inexact_idempotency_binding_cannot_authorize_retry(idempotency_key, effect_sha256):
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.ABSENT,
        idempotency_key=idempotency_key,
        idempotency_effect_sha256=effect_sha256,
    )
    with pytest.raises(RetrySafetyUnknown, match="replay safety"):
        reconcile_lost_ack(OP, evidence)


def test_ambiguous_readback_fails_closed_even_with_exact_idempotency_evidence():
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.AMBIGUOUS,
        idempotency_key=OP.operation_id,
        idempotency_effect_sha256=OP.effect_sha256,
    )
    with pytest.raises(RemoteStateAmbiguous, match="ambiguous"):
        reconcile_lost_ack(OP, evidence)


def test_present_conflicting_remote_effect_fails_closed():
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload="different-payload",
    )
    with pytest.raises(ReconciliationConflict, match="conflicting effect"):
        reconcile_lost_ack(OP, evidence)


@pytest.mark.parametrize(
    "evidence",
    [
        ProviderEvidence("provider-b", OP.operation_id, ProviderReadbackState.ABSENT),
        ProviderEvidence(OP.provider, "other-op", ProviderReadbackState.ABSENT),
    ],
)
def test_readback_identity_must_match_exact_provider_and_operation(evidence):
    with pytest.raises(ReconciliationConflict, match="exact provider and operation"):
        reconcile_lost_ack(OP, evidence)


def test_changed_request_cannot_reuse_old_idempotency_evidence():
    changed = ExternalOperation(OP.provider, OP.operation_id, OP.tool, "payload-v2")
    with pytest.raises(RetrySafetyUnknown, match="replay safety"):
        reconcile_lost_ack(changed, exact_absent_evidence(OP))


def test_absent_readback_cannot_also_claim_an_observed_effect():
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.ABSENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
        idempotency_key=OP.operation_id,
        idempotency_effect_sha256=OP.effect_sha256,
    )
    with pytest.raises(ReconciliationConflict, match="confirmed-absent"):
        reconcile_lost_ack(OP, evidence)
