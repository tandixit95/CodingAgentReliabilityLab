"""Fail-closed reconciliation for a lost acknowledgement across system boundaries.

This module models evidence a runtime may receive from an external provider. It
does not contact a real service. Retry is allowed only after an authoritative
readback confirms absence and the provider evidence binds an exact idempotency key
to the same operation. Ambiguous or conflicting remote state never authorizes a
retry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ProviderReadbackState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"


class RemoteStateAmbiguous(RuntimeError):
    """Raised when provider evidence cannot establish whether the effect committed."""


class ReconciliationConflict(ValueError):
    """Raised when provider evidence contradicts the exact operation being reconciled."""


class RetrySafetyUnknown(RuntimeError):
    """Raised when absence is known but exact replay safety is not established."""


@dataclass(frozen=True, slots=True)
class ExternalOperation:
    provider: str
    operation_id: str
    tool: str
    payload: str

    def __post_init__(self) -> None:
        for name, value in (
            ("provider", self.provider),
            ("operation_id", self.operation_id),
            ("tool", self.tool),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")
        if not isinstance(self.payload, str):
            raise TypeError("payload must be a string; reconciliation matching is exact")

    @property
    def effect_sha256(self) -> str:
        canonical = json.dumps(
            [self.provider, self.operation_id, self.tool, self.payload],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """One explicit provider readback plus optional exact idempotency evidence."""

    provider: str
    operation_id: str
    readback: ProviderReadbackState
    observed_tool: str | None = None
    observed_payload: str | None = None
    idempotency_key: str | None = None
    idempotency_effect_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    action: Literal["accept_remote_commit", "retry_exact_operation"]
    should_retry: bool
    reason: str


def reconcile_lost_ack(
    operation: ExternalOperation, evidence: ProviderEvidence
) -> ReconciliationDecision:
    """Resolve a lost acknowledgement without guessing remote state.

    A matching provider readback proves the effect already committed, so retry is
    suppressed. A confirmed-absent readback permits retry only when provider
    evidence binds the operation id as an exact idempotency key for the same effect.
    Unknown, contradictory, or weak evidence fails closed.
    """

    if evidence.provider != operation.provider or evidence.operation_id != operation.operation_id:
        raise ReconciliationConflict(
            "provider evidence is not bound to the exact provider and operation identity"
        )

    if evidence.readback is ProviderReadbackState.AMBIGUOUS:
        raise RemoteStateAmbiguous(
            "provider readback is ambiguous; retry would risk duplicating a committed effect"
        )

    if evidence.readback is ProviderReadbackState.PRESENT:
        observed = (evidence.observed_tool, evidence.observed_payload)
        expected = (operation.tool, operation.payload)
        if observed != expected:
            raise ReconciliationConflict(
                "provider readback found a conflicting effect for this operation identity"
            )
        return ReconciliationDecision(
            action="accept_remote_commit",
            should_retry=False,
            reason="provider readback confirms the exact effect already committed",
        )

    if evidence.readback is not ProviderReadbackState.ABSENT:
        raise ValueError(f"unsupported provider readback state: {evidence.readback}")

    if evidence.observed_tool is not None or evidence.observed_payload is not None:
        raise ReconciliationConflict(
            "confirmed-absent evidence cannot simultaneously report an observed effect"
        )

    exact_idempotency = (
        evidence.idempotency_key == operation.operation_id
        and evidence.idempotency_effect_sha256 == operation.effect_sha256
    )
    if not exact_idempotency:
        raise RetrySafetyUnknown(
            "provider absence is known, but exact idempotent replay safety is not established"
        )

    return ReconciliationDecision(
        action="retry_exact_operation",
        should_retry=True,
        reason="provider confirms absence and exact idempotency evidence permits same-operation replay",
    )
