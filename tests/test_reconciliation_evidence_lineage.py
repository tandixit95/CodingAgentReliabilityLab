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
    VersionedProviderState,
)

OP = ExternalOperation("provider-a", "op-1", "create_record", "payload-v1")


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


def test_newer_readback_supersedes_retry_authority_across_restart(tmp_path):
    db = tmp_path / "evidence.sqlite3"
    first = ReconciliationEvidenceStore(db)
    old = first.record(OP, "read-4", absent(4))
    assert old.decision.should_retry is True

    restarted = ReconciliationEvidenceStore(db)
    newer = restarted.record(OP, "read-5", present(5))
    assert newer.decision.should_retry is False

    restarted_again = ReconciliationEvidenceStore(db)
    with pytest.raises(SupersededReconciliationDecision, match="superseded"):
        restarted_again.apply_retry(OP, old.evidence_id, old.decision, VersionedProviderState(4))
    assert restarted_again.current(OP) == newer


def test_current_retry_authority_survives_restart_and_can_apply_once(tmp_path):
    db = tmp_path / "evidence.sqlite3"
    recorded = ReconciliationEvidenceStore(db).record(OP, "read-9", absent(9))
    restarted = ReconciliationEvidenceStore(db)
    updated = restarted.apply_retry(
        OP, recorded.evidence_id, recorded.decision, VersionedProviderState(9)
    )
    assert updated.revision == 10
    assert updated.effects == (OP,)


def test_older_observation_cannot_replace_newer_persisted_head(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(OP, "read-8", absent(8))
    with pytest.raises(SupersededReconciliationDecision, match="older"):
        store.record(OP, "read-7", absent(7))


def test_exact_head_replay_is_idempotent_after_restart(tmp_path):
    db = tmp_path / "evidence.sqlite3"
    expected = ReconciliationEvidenceStore(db).record(OP, "read-3", absent(3))
    assert ReconciliationEvidenceStore(db).record(OP, "read-3", absent(3)) == expected


def test_same_revision_conflicting_evidence_fails_closed(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(OP, "absent-3", absent(3))
    with pytest.raises(EvidenceLineageConflict, match="same provider revision"):
        store.record(OP, "present-3", present(3))


def test_changed_effect_cannot_reuse_persisted_operation_identity(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    store.record(OP, "read-1", absent(1))
    changed = ExternalOperation(OP.provider, OP.operation_id, OP.tool, "payload-v2")
    changed_evidence = ProviderEvidence(
        changed.provider,
        changed.operation_id,
        ProviderReadbackState.ABSENT,
        readback_revision=2,
        idempotency_key=changed.operation_id,
        idempotency_effect_sha256=changed.effect_sha256,
    )
    with pytest.raises(EvidenceLineageConflict, match="different exact effect"):
        store.record(changed, "read-2", changed_evidence)


def test_lineage_requires_revision_even_for_present_evidence(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    no_revision = ProviderEvidence(
        OP.provider,
        OP.operation_id,
        ProviderReadbackState.PRESENT,
        observed_tool=OP.tool,
        observed_payload=OP.payload,
    )
    with pytest.raises(EvidenceLineageConflict, match="requires a provider revision"):
        store.record(OP, "unversioned", no_revision)
