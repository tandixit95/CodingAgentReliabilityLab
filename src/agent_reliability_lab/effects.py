from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReplayMode = Literal["naive", "idempotent"]


@dataclass(frozen=True, slots=True)
class EffectRequest:
    """One replayable side-effect request emitted by a coding-agent runtime."""

    sequence: int
    operation_id: str
    tool: str
    payload: str


@dataclass(frozen=True, slots=True)
class EffectReplayResult:
    """Deterministic projection of side effects applied during replay."""

    mode: ReplayMode
    applied: tuple[EffectRequest, ...]
    duplicates: tuple[EffectRequest, ...]


class IdempotencyConflict(ValueError):
    """Raised when one operation identity is reused for a different effect."""


def _validate_order(requests: tuple[EffectRequest, ...]) -> None:
    sequences = [request.sequence for request in requests]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValueError(
            "effect requests must have unique monotonically increasing sequence numbers"
        )


def replay_effects(
    requests: list[EffectRequest] | tuple[EffectRequest, ...],
    mode: ReplayMode = "idempotent",
) -> EffectReplayResult:
    """Replay tool side effects with or without an idempotency ledger.

    A common failure window is: a tool effect succeeds, but the runtime crashes
    before it durably records the acknowledgement. On retry, the same logical
    operation is emitted again. ``naive`` applies both requests. ``idempotent``
    records the first operation identity and suppresses exact retries.

    Reusing an operation identity for a different tool or payload fails closed;
    silently accepting that conflict would make replay semantics ambiguous.
    """

    if mode not in ("naive", "idempotent"):
        raise ValueError(f"unsupported replay mode: {mode}")

    materialized = tuple(requests)
    _validate_order(materialized)

    applied: list[EffectRequest] = []
    duplicates: list[EffectRequest] = []
    ledger: dict[str, tuple[str, str]] = {}

    for request in materialized:
        if mode == "naive":
            applied.append(request)
            continue

        identity = (request.tool, request.payload)
        previous = ledger.get(request.operation_id)
        if previous is None:
            ledger[request.operation_id] = identity
            applied.append(request)
            continue
        if previous != identity:
            raise IdempotencyConflict(
                f"operation_id was reused for a different tool effect: {request.operation_id}"
            )
        duplicates.append(request)

    return EffectReplayResult(mode=mode, applied=tuple(applied), duplicates=tuple(duplicates))
