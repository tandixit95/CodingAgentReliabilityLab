"""Durable lineage for provider reconciliation evidence and retry authority."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .reconciliation import (
    ExternalOperation,
    ProviderEvidence,
    ReconciliationDecision,
    VersionedProviderState,
    apply_conditional_retry,
    reconcile_lost_ack,
)


class EvidenceLineageConflict(ValueError):
    """Raised when persisted evidence identity or exact operation binding conflicts."""


class SupersededReconciliationDecision(RuntimeError):
    """Raised when replay uses retry authority that newer provider evidence replaced."""


@dataclass(frozen=True, slots=True)
class ReconciliationEvidenceRecord:
    provider: str
    operation_id: str
    evidence_id: str
    revision: int
    effect_sha256: str
    decision: ReconciliationDecision


class ReconciliationEvidenceStore:
    """Persist one monotonic provider-evidence head per exact external operation."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database == ":memory:":
            raise ValueError("a file-backed database is required for restart recovery")
        with self._transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reconciliation_heads ("
                "provider TEXT NOT NULL, operation_id TEXT NOT NULL, effect_sha256 TEXT NOT NULL, "
                "evidence_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL, "
                "should_retry INTEGER NOT NULL, reason TEXT NOT NULL, retry_precondition_revision INTEGER, "
                "retry_effect_sha256 TEXT, PRIMARY KEY(provider, operation_id))"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _decision_from_row(row: tuple) -> ReconciliationDecision:
        return ReconciliationDecision(
            action=row[5],
            should_retry=bool(row[6]),
            reason=row[7],
            retry_precondition_revision=row[8],
            retry_effect_sha256=row[9],
        )

    @classmethod
    def _record_from_row(cls, row: tuple) -> ReconciliationEvidenceRecord:
        return ReconciliationEvidenceRecord(
            provider=row[0],
            operation_id=row[1],
            evidence_id=row[3],
            revision=row[4],
            effect_sha256=row[2],
            decision=cls._decision_from_row(row),
        )

    def record(
        self, operation: ExternalOperation, evidence_id: str, evidence: ProviderEvidence
    ) -> ReconciliationEvidenceRecord:
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id must be a nonempty string")
        if evidence.readback_revision is None:
            raise EvidenceLineageConflict(
                "persistent evidence lineage requires a provider revision"
            )
        decision = reconcile_lost_ack(operation, evidence)
        candidate = ReconciliationEvidenceRecord(
            operation.provider,
            operation.operation_id,
            evidence_id,
            evidence.readback_revision,
            operation.effect_sha256,
            decision,
        )
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT provider, operation_id, effect_sha256, evidence_id, revision, action, "
                "should_retry, reason, retry_precondition_revision, retry_effect_sha256 "
                "FROM reconciliation_heads WHERE provider=? AND operation_id=?",
                (operation.provider, operation.operation_id),
            ).fetchone()
            if row is not None:
                current = self._record_from_row(row)
                if current.effect_sha256 != operation.effect_sha256:
                    raise EvidenceLineageConflict(
                        "operation identity is already bound to a different exact effect"
                    )
                if candidate.revision < current.revision:
                    raise SupersededReconciliationDecision(
                        "provider evidence is older than the persisted reconciliation head"
                    )
                if candidate.revision == current.revision:
                    if candidate == current:
                        return current
                    raise EvidenceLineageConflict(
                        "the same provider revision cannot carry conflicting reconciliation evidence"
                    )
            conn.execute(
                "INSERT INTO reconciliation_heads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider, operation_id) DO UPDATE SET "
                "effect_sha256=excluded.effect_sha256, evidence_id=excluded.evidence_id, "
                "revision=excluded.revision, action=excluded.action, should_retry=excluded.should_retry, "
                "reason=excluded.reason, retry_precondition_revision=excluded.retry_precondition_revision, "
                "retry_effect_sha256=excluded.retry_effect_sha256",
                (
                    candidate.provider,
                    candidate.operation_id,
                    candidate.effect_sha256,
                    candidate.evidence_id,
                    candidate.revision,
                    candidate.decision.action,
                    int(candidate.decision.should_retry),
                    candidate.decision.reason,
                    candidate.decision.retry_precondition_revision,
                    candidate.decision.retry_effect_sha256,
                ),
            )
            return candidate

    def current(self, operation: ExternalOperation) -> ReconciliationEvidenceRecord:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT provider, operation_id, effect_sha256, evidence_id, revision, action, "
                "should_retry, reason, retry_precondition_revision, retry_effect_sha256 "
                "FROM reconciliation_heads WHERE provider=? AND operation_id=?",
                (operation.provider, operation.operation_id),
            ).fetchone()
        if row is None:
            raise EvidenceLineageConflict(
                "no reconciliation evidence is persisted for this operation"
            )
        record = self._record_from_row(row)
        if record.effect_sha256 != operation.effect_sha256:
            raise EvidenceLineageConflict("persisted evidence is bound to a different exact effect")
        return record

    def assert_current(
        self, operation: ExternalOperation, evidence_id: str, decision: ReconciliationDecision
    ) -> None:
        current = self.current(operation)
        if current.evidence_id != evidence_id or current.decision != decision:
            raise SupersededReconciliationDecision(
                "reconciliation decision is superseded by newer persisted provider evidence"
            )

    def apply_retry(
        self,
        operation: ExternalOperation,
        evidence_id: str,
        decision: ReconciliationDecision,
        provider_state: VersionedProviderState,
    ) -> VersionedProviderState:
        self.assert_current(operation, evidence_id, decision)
        return apply_conditional_retry(operation, decision, provider_state)
