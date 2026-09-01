from agent_reliability_lab import Event, EventKind, RunState, replay


def scenario():
    return [
        Event(1, "parent", "turn-parent", EventKind.OUTPUT, "parent partial"),
        Event(2, "child", "turn-child", EventKind.COMPLETED, "child final"),
        Event(3, "parent", "turn-parent", EventKind.COMPLETED, "parent final"),
    ]


def initial():
    return RunState(active_run_id="parent", active_turn_id="turn-parent")


def test_naive_projection_allows_child_to_overwrite_parent_before_parent_completion():
    partial = replay(scenario()[:2], initial(), mode="naive")
    assert partial.state.terminal is True
    assert partial.state.output == "child final"
    assert partial.state.accepted_events == (1, 2)


def test_scoped_projection_ignores_child_completion():
    partial = replay(scenario()[:2], initial(), mode="scoped")
    assert partial.state.terminal is False
    assert partial.state.output == "parent partial"
    assert partial.state.accepted_events == (1,)
    assert partial.state.ignored_events == (2,)


def test_scoped_projection_reaches_parent_terminal_state_deterministically():
    result = replay(scenario(), initial(), mode="scoped")
    assert result.state.terminal is True
    assert result.state.failed is False
    assert result.state.output == "parent final"
    assert result.state.accepted_events == (1, 3)
    assert result.state.ignored_events == (2,)


def test_replay_rejects_ambiguous_event_order():
    bad = [
        Event(2, "parent", "turn-parent", EventKind.OUTPUT, "later"),
        Event(1, "parent", "turn-parent", EventKind.OUTPUT, "earlier"),
    ]
    try:
        replay(bad, initial())
    except ValueError as exc:
        assert "monotonically increasing" in str(exc)
    else:
        raise AssertionError("ambiguous ordering should fail closed")
