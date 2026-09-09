from __future__ import annotations

import pytest

from agent_reliability_lab.evidence_lineage import (
    EvidenceLineageConflict,
    ReconciliationEvidenceStore,
    SupersededReconciliationDecision,
)
from agent_reliability_lab.reconciliation import (
    ExternalOperation,
    ProviderEvidence,
    ProviderReadbackState,
    StaleProviderReadback,
    VersionedProviderState,
)

OP = ExternalOperation("provider-a", "op-loop", "create_record", "payload-v1")


def absent(revision: int) -> ProviderEvidence:
    return ProviderEvidence(
        OP.provider,
        OP.operation_id,
        ProviderReadbackState.ABSENT,
        readback_revision=revision,
        idempotency_key=OP.operation_id,
        idempotency_effect_sha256=OP.effect_sha256,
    )


def present(revision: int) -> ProviderEvidence:
    return ProviderEvidence(
        OP.provider,
        OP.operation_id,
        ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
        readback_revision=revision,
    )


def test_repeated_lost_ack_loop_converges_after_restart_without_duplicate_effect(tmp_path):
    database = tmp_path / "evidence.sqlite3"
    first = ReconciliationEvidenceStore(database)
    retry = first.record(OP, "absent-4", absent(4))

    # Conditional retry commits remotely, but its acknowledgement is lost locally.
    provider = first.apply_retry(OP, retry.evidence_id, retry.decision, VersionedProviderState(4))
    assert provider.revision == 5
    assert provider.effects == (OP,)
    assert first.completion(OP) is None

    # A restarted runtime reads the provider again and observes the committed effect.
    restarted = ReconciliationEvidenceStore(database)
    committed = restarted.record(OP, "present-5", present(5))
    completion = restarted.mark_reconciled(OP, committed.evidence_id, committed.decision)

    assert committed.decision.should_retry is False
    assert completion.revision == 5
    assert provider.effects == (OP,)

    # The pre-crash retry authority is now durably superseded and cannot duplicate the effect.
    with pytest.raises(SupersededReconciliationDecision, match="superseded"):
        restarted.apply_retry(OP, retry.evidence_id, retry.decision, provider)

    restarted_again = ReconciliationEvidenceStore(database)
    assert restarted_again.completion(OP) == completion
    assert (
        restarted_again.mark_reconciled(OP, committed.evidence_id, committed.decision) == completion
    )


def test_retry_replay_before_fresh_readback_fails_on_advanced_provider_revision(tmp_path):
    database = tmp_path / "evidence.sqlite3"
    first = ReconciliationEvidenceStore(database)
    retry = first.record(OP, "absent-8", absent(8))
    provider = first.apply_retry(OP, retry.evidence_id, retry.decision, VersionedProviderState(8))

    restarted = ReconciliationEvidenceStore(database)
    with pytest.raises(StaleProviderReadback, match="revision changed"):
        restarted.apply_retry(OP, retry.evidence_id, retry.decision, provider)
    assert provider.effects == (OP,)
    assert restarted.completion(OP) is None


def test_retry_authority_cannot_mark_reconciliation_complete(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    retry = store.record(OP, "absent-2", absent(2))
    with pytest.raises(EvidenceLineageConflict, match="provider-PRESENT"):
        store.mark_reconciled(OP, retry.evidence_id, retry.decision)


def test_completed_operation_cannot_regain_retry_authority_from_later_absence(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    committed = store.record(OP, "present-5", present(5))
    store.mark_reconciled(OP, committed.evidence_id, committed.decision)

    with pytest.raises(SupersededReconciliationDecision, match="cannot regain retry authority"):
        store.record(OP, "absent-6", absent(6))


def test_newer_present_observation_preserves_existing_completion_record(tmp_path):
    database = tmp_path / "evidence.sqlite3"
    store = ReconciliationEvidenceStore(database)
    committed = store.record(OP, "present-5", present(5))
    completion = store.mark_reconciled(OP, committed.evidence_id, committed.decision)

    newer = ReconciliationEvidenceStore(database).record(OP, "present-6", present(6))
    restarted = ReconciliationEvidenceStore(database)
    assert restarted.mark_reconciled(OP, newer.evidence_id, newer.decision) == completion
    assert restarted.completion(OP) == completion
