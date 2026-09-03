import pytest

from agent_reliability_lab import Event, EventKind, RunState, TerminalStateConflict, replay


def initial():
    return RunState(active_run_id="parent", active_turn_id="turn-parent")


def conflicting_terminal_scenario():
    return [
        Event(1, "parent", "turn-parent", EventKind.COMPLETED, "final answer"),
        Event(2, "parent", "turn-parent", EventKind.FAILED, "late failure"),
    ]


def test_naive_projection_allows_a_later_terminal_event_to_overwrite_the_first():
    result = replay(conflicting_terminal_scenario(), initial(), mode="naive")
    assert result.state.terminal is True
    assert result.state.failed is True
    assert result.state.output == "late failure"
    assert result.state.accepted_events == (1, 2)


def test_scoped_projection_fails_closed_on_conflicting_terminal_outcomes():
    with pytest.raises(TerminalStateConflict, match="incompatible event after termination"):
        replay(conflicting_terminal_scenario(), initial(), mode="scoped")


def test_scoped_projection_ignores_an_exact_terminal_replay():
    events = [
        Event(1, "parent", "turn-parent", EventKind.COMPLETED, "final answer"),
        Event(2, "parent", "turn-parent", EventKind.COMPLETED, "final answer"),
    ]
    result = replay(events, initial(), mode="scoped")
    assert result.state.terminal is True
    assert result.state.failed is False
    assert result.state.output == "final answer"
    assert result.state.terminal_kind is EventKind.COMPLETED
    assert result.state.accepted_events == (1,)
    assert result.state.ignored_events == (2,)


def test_scoped_projection_rejects_same_turn_output_after_terminal_state():
    events = [
        Event(1, "parent", "turn-parent", EventKind.COMPLETED, "final answer"),
        Event(2, "parent", "turn-parent", EventKind.OUTPUT, "late output"),
    ]
    with pytest.raises(TerminalStateConflict, match="incoming='output'"):
        replay(events, initial(), mode="scoped")


def test_scoped_projection_rejects_same_terminal_kind_with_different_payload():
    events = [
        Event(1, "parent", "turn-parent", EventKind.COMPLETED, "answer v1"),
        Event(2, "parent", "turn-parent", EventKind.COMPLETED, "answer v2"),
    ]
    with pytest.raises(TerminalStateConflict, match="existing='completed' incoming='completed'"):
        replay(events, initial(), mode="scoped")


def test_scoped_projection_still_ignores_foreign_events_after_terminal_state():
    events = [
        Event(1, "parent", "turn-parent", EventKind.COMPLETED, "final answer"),
        Event(2, "child", "turn-child", EventKind.FAILED, "child failure"),
    ]
    result = replay(events, initial(), mode="scoped")
    assert result.state.failed is False
    assert result.state.output == "final answer"
    assert result.state.accepted_events == (1,)
    assert result.state.ignored_events == (2,)
