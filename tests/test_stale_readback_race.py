from __future__ import annotations

import pytest

from agent_reliability_lab.reconciliation import (
    ExternalOperation,
    ProviderEvidence,
    ProviderReadbackState,
    ReconciliationConflict,
    RetrySafetyUnknown,
    StaleProviderReadback,
    VersionedProviderState,
    apply_conditional_retry,
    reconcile_lost_ack,
)

OP = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")


def absent_at(revision: int) -> ProviderEvidence:
    return ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.ABSENT,
        readback_revision=revision,
        idempotency_key=OP.operation_id,
        idempotency_effect_sha256=OP.effect_sha256,
    )


def test_stale_absence_readback_cannot_authorize_retry_after_concurrent_commit():
    decision = reconcile_lost_ack(OP, absent_at(4))
    assert decision.retry_precondition_revision == 4

    # Another writer commits the same logical effect after our absence readback.
    concurrent_state = VersionedProviderState(revision=5, effects=(OP,))
    naive_unconditional_retry = concurrent_state.effects + (OP,)
    assert len(naive_unconditional_retry) == 2

    with pytest.raises(StaleProviderReadback, match="revision changed"):
        apply_conditional_retry(OP, decision, concurrent_state)


def test_current_absence_revision_allows_one_atomic_conditional_retry():
    decision = reconcile_lost_ack(OP, absent_at(9))
    state = VersionedProviderState(revision=9)

    updated = apply_conditional_retry(OP, decision, state)

    assert updated.revision == 10
    assert updated.effects == (OP,)


def test_absence_without_revision_fails_closed_even_with_exact_idempotency_binding():
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.ABSENT,
        idempotency_key=OP.operation_id,
        idempotency_effect_sha256=OP.effect_sha256,
    )
    with pytest.raises(RetrySafetyUnknown, match="no revision"):
        reconcile_lost_ack(OP, evidence)


def test_present_readback_does_not_need_retry_revision():
    evidence = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
    )
    decision = reconcile_lost_ack(OP, evidence)
    assert decision.should_retry is False
    assert decision.retry_precondition_revision is None


def test_non_retry_decision_cannot_be_used_as_conditional_retry_authority():
    present = ProviderEvidence(
        provider=OP.provider,
        operation_id=OP.operation_id,
        readback=ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
    )
    decision = reconcile_lost_ack(OP, present)
    with pytest.raises(RetrySafetyUnknown, match="does not authorize"):
        apply_conditional_retry(OP, decision, VersionedProviderState(revision=1, effects=(OP,)))


def test_provider_state_cannot_claim_same_revision_but_contain_absent_operation():
    decision = reconcile_lost_ack(OP, absent_at(3))
    contradictory = VersionedProviderState(revision=3, effects=(OP,))
    with pytest.raises(ReconciliationConflict, match="contradicts the absence"):
        apply_conditional_retry(OP, decision, contradictory)


def test_negative_revisions_are_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        absent_at(-1)
    with pytest.raises(ValueError, match="nonnegative"):
        VersionedProviderState(revision=-1)


def test_retry_decision_cannot_be_reused_for_a_changed_operation():
    decision = reconcile_lost_ack(OP, absent_at(12))
    changed = ExternalOperation(OP.provider, OP.operation_id, OP.tool, "payload-v2")
    with pytest.raises(ReconciliationConflict, match="exact operation effect"):
        apply_conditional_retry(changed, decision, VersionedProviderState(revision=12))
