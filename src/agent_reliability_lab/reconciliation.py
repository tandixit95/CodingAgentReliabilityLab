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


class StaleProviderReadback(RuntimeError):
    """Raised when a conditional retry no longer matches the provider revision read."""


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
    readback_revision: int | None = None
    idempotency_key: str | None = None
    idempotency_effect_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.readback_revision is not None and self.readback_revision < 0:
            raise ValueError("readback_revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    action: Literal["accept_remote_commit", "retry_exact_operation"]
    should_retry: bool
    reason: str
    retry_precondition_revision: int | None = None
    retry_effect_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class VersionedProviderState:
    """Minimal provider state for an atomic compare-and-apply retry simulation."""

    revision: int
    effects: tuple[ExternalOperation, ...] = ()

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("revision must be nonnegative")


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

    if evidence.readback_revision is None:
        raise RetrySafetyUnknown(
            "provider absence is known, but no revision is available to bind a conditional retry"
        )

    return ReconciliationDecision(
        action="retry_exact_operation",
        should_retry=True,
        reason=(
            "provider confirms absence and exact idempotency evidence permits a retry only "
            "while the observed provider revision is still current"
        ),
        retry_precondition_revision=evidence.readback_revision,
        retry_effect_sha256=operation.effect_sha256,
    )


def apply_conditional_retry(
    operation: ExternalOperation,
    decision: ReconciliationDecision,
    provider_state: VersionedProviderState,
) -> VersionedProviderState:
    """Atomically apply a retry only if the provider revision still matches readback.

    This models a provider-side conditional write (for example, compare-and-set or
    an If-Match-style contract). A local "freshness check" followed by an
    unconditional write would retain the same time-of-check/time-of-use race and
    is intentionally not modeled as safe.
    """

    expected_revision = decision.retry_precondition_revision
    if not decision.should_retry or decision.action != "retry_exact_operation":
        raise RetrySafetyUnknown("reconciliation decision does not authorize a retry")
    if expected_revision is None:
        raise RetrySafetyUnknown("retry decision is missing a provider revision precondition")
    if decision.retry_effect_sha256 != operation.effect_sha256:
        raise ReconciliationConflict("retry decision is not bound to the exact operation effect")
    if provider_state.revision != expected_revision:
        raise StaleProviderReadback(
            "provider revision changed after the absence readback; retry must be reconciled again"
        )

    for existing in provider_state.effects:
        if (
            existing.provider == operation.provider
            and existing.operation_id == operation.operation_id
        ):
            raise ReconciliationConflict(
                "provider state contradicts the absence readback for this operation identity"
            )

    return VersionedProviderState(
        revision=provider_state.revision + 1,
        effects=provider_state.effects + (operation,),
    )
