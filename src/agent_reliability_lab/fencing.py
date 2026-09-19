"""Deterministic fencing-token model for preventing stale executor writes.

This is a local model, not a distributed lock service.  The invariant is that
an authority epoch only increases and every state mutation is conditional on
the exact current epoch.  A worker that resumes after its lease was superseded
therefore cannot commit merely because it still holds old local state.
"""

from __future__ import annotations

from dataclasses import dataclass


class StaleFencingToken(RuntimeError):
    """Raised when a superseded executor attempts to mutate shared state."""


class FencingConflict(ValueError):
    """Raised when a token is used for the wrong resource or operation."""


@dataclass(frozen=True, slots=True)
class FencingToken:
    resource: str
    operation_id: str
    epoch: int

    def __post_init__(self) -> None:
        if not self.resource.strip() or not self.operation_id.strip():
            raise ValueError("resource and operation_id must be nonempty")
        if self.epoch <= 0:
            raise ValueError("epoch must be positive")


@dataclass(frozen=True, slots=True)
class FencedState:
    resource: str
    epoch: int = 0
    operation_id: str | None = None
    value: str | None = None

    def __post_init__(self) -> None:
        if not self.resource.strip():
            raise ValueError("resource must be nonempty")
        if self.epoch < 0:
            raise ValueError("epoch must be nonnegative")


def acquire_authority(state: FencedState, operation_id: str) -> tuple[FencedState, FencingToken]:
    """Issue a monotonically newer authority token for one exact operation."""

    if not operation_id.strip():
        raise ValueError("operation_id must be nonempty")
    epoch = state.epoch + 1
    token = FencingToken(state.resource, operation_id, epoch)
    return FencedState(state.resource, epoch, operation_id, state.value), token


def fenced_write(state: FencedState, token: FencingToken, value: str) -> FencedState:
    """Commit only for the exact current resource, operation, and authority epoch."""

    if token.resource != state.resource:
        raise FencingConflict("fencing token belongs to a different resource")
    if token.epoch != state.epoch:
        raise StaleFencingToken(
            "fencing token was superseded; stale executor must re-acquire authority"
        )
    if token.operation_id != state.operation_id:
        raise FencingConflict("fencing token is not bound to the current operation")
    return FencedState(state.resource, state.epoch, state.operation_id, value)
