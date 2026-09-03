from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from .events import Event, EventKind

ReducerMode = Literal["naive", "scoped"]


class TerminalStateConflict(ValueError):
    """Raised when an active turn emits incompatible events after termination."""


@dataclass(frozen=True, slots=True)
class RunState:
    active_run_id: str
    active_turn_id: str
    output: str = ""
    terminal: bool = False
    failed: bool = False
    accepted_events: tuple[int, ...] = ()
    ignored_events: tuple[int, ...] = ()
    terminal_kind: EventKind | None = None
    terminal_payload: str = ""


@dataclass(frozen=True, slots=True)
class SimulationResult:
    mode: ReducerMode
    state: RunState


def _belongs_to_active_turn(state: RunState, event: Event) -> bool:
    return event.run_id == state.active_run_id and event.turn_id == state.active_turn_id


def _handle_event_after_terminal(state: RunState, event: Event) -> RunState:
    if event.kind is state.terminal_kind and event.payload == state.terminal_payload:
        return replace(state, ignored_events=state.ignored_events + (event.sequence,))

    prior = state.terminal_kind.value if state.terminal_kind is not None else "unknown"
    raise TerminalStateConflict(
        "active turn emitted an incompatible event after termination: "
        f"existing={prior!r} incoming={event.kind.value!r}"
    )


def _apply_event(state: RunState, event: Event, mode: ReducerMode) -> RunState:
    if mode == "scoped" and not _belongs_to_active_turn(state, event):
        return replace(state, ignored_events=state.ignored_events + (event.sequence,))
    if mode == "scoped" and state.terminal:
        return _handle_event_after_terminal(state, event)

    next_state = replace(state, accepted_events=state.accepted_events + (event.sequence,))
    if event.kind is EventKind.OUTPUT:
        return replace(next_state, output=event.payload)
    if event.kind is EventKind.COMPLETED:
        return replace(
            next_state,
            terminal=True,
            terminal_kind=event.kind,
            terminal_payload=event.payload,
            output=event.payload or next_state.output,
        )
    if event.kind is EventKind.FAILED:
        return replace(
            next_state,
            terminal=True,
            failed=True,
            terminal_kind=event.kind,
            terminal_payload=event.payload,
            output=event.payload,
        )
    raise AssertionError(f"unhandled event kind: {event.kind}")


def replay(
    events: Iterable[Event], initial: RunState, mode: ReducerMode = "scoped"
) -> SimulationResult:
    """Replay events deterministically in sequence order.

    ``naive`` models the orchestration bug where every completion on a shared
    event stream can terminate the active parent. ``scoped`` accepts events
    only from the active run/turn identity and fails closed if that turn emits
    a non-identical event after reaching a terminal state. Exact terminal
    replays are treated as duplicates and ignored.
    """

    if mode not in ("naive", "scoped"):
        raise ValueError(f"unsupported reducer mode: {mode}")

    materialized = tuple(events)
    sequences = [event.sequence for event in materialized]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValueError("events must have unique monotonically increasing sequence numbers")

    state = initial
    for event in materialized:
        state = _apply_event(state, event, mode)
    return SimulationResult(mode=mode, state=state)
