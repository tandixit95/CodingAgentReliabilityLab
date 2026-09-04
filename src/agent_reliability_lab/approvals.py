from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalAction(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    EXECUTE = "execute"


class ApprovalConflict(ValueError):
    """Raised when approval state no longer matches the exact requested effect."""


class ApprovalRequired(ValueError):
    """Raised when execution is attempted before an approval decision exists."""


class ApprovalDenied(ValueError):
    """Raised when execution is attempted after an exact request was denied."""


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    """One deterministic approval-lifecycle event for a tool side effect."""

    sequence: int
    action: ApprovalAction
    operation_id: str
    tool: str
    payload: str


@dataclass(frozen=True, slots=True)
class ApprovalReplayResult:
    """Strict projection of an approval interruption and later replay."""

    executed: tuple[str, ...]
    approved: tuple[str, ...]
    denied: tuple[str, ...]


def _validate_order(events: tuple[ApprovalEvent, ...]) -> None:
    sequences = [event.sequence for event in events]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValueError(
            "approval events must have unique monotonically increasing sequence numbers"
        )


def replay_approval(
    events: list[ApprovalEvent] | tuple[ApprovalEvent, ...],
) -> ApprovalReplayResult:
    """Replay an approval lifecycle with exact operation binding.

    Approval is bound to ``operation_id`` plus the exact tool and payload that
    were requested. Execution before a decision, execution after denial, or
    reusing an operation identity for a changed effect fails closed.
    """

    materialized = tuple(events)
    _validate_order(materialized)

    requests: dict[str, tuple[str, str]] = {}
    decisions: dict[str, ApprovalAction] = {}
    executed: list[str] = []

    for event in materialized:
        identity = (event.tool, event.payload)
        requested = requests.get(event.operation_id)

        if event.action is ApprovalAction.REQUESTED:
            if requested is None:
                requests[event.operation_id] = identity
                continue
            if requested != identity:
                raise ApprovalConflict(
                    f"operation changed after approval request: {event.operation_id}"
                )
            continue

        if requested is None:
            raise ApprovalConflict(f"approval event has no matching request: {event.operation_id}")
        if requested != identity:
            raise ApprovalConflict(
                f"approval binding does not match requested effect: {event.operation_id}"
            )

        if event.action in (ApprovalAction.APPROVED, ApprovalAction.DENIED):
            previous = decisions.get(event.operation_id)
            if previous is not None and previous is not event.action:
                raise ApprovalConflict(
                    f"approval decision changed for operation: {event.operation_id}"
                )
            decisions[event.operation_id] = event.action
            continue

        if event.action is ApprovalAction.EXECUTE:
            decision = decisions.get(event.operation_id)
            if decision is None:
                raise ApprovalRequired(
                    f"operation cannot execute before approval: {event.operation_id}"
                )
            if decision is ApprovalAction.DENIED:
                raise ApprovalDenied(f"operation was denied: {event.operation_id}")
            if event.operation_id not in executed:
                executed.append(event.operation_id)
            continue

        raise AssertionError(f"unhandled approval action: {event.action}")

    approved = tuple(
        operation_id
        for operation_id, decision in decisions.items()
        if decision is ApprovalAction.APPROVED
    )
    denied = tuple(
        operation_id
        for operation_id, decision in decisions.items()
        if decision is ApprovalAction.DENIED
    )
    return ApprovalReplayResult(executed=tuple(executed), approved=approved, denied=denied)
