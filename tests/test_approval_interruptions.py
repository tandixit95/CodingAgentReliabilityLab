import pytest

from agent_reliability_lab import (
    ApprovalAction,
    ApprovalConflict,
    ApprovalDenied,
    ApprovalEvent,
    ApprovalRequired,
    replay_approval,
)


def event(sequence, action, payload="plan v1", tool="write_file"):
    return ApprovalEvent(sequence, action, "write:plan", tool, payload)


def test_execution_before_approval_fails_closed():
    events = [event(1, ApprovalAction.REQUESTED), event(2, ApprovalAction.EXECUTE)]
    with pytest.raises(ApprovalRequired, match="cannot execute before approval"):
        replay_approval(events)


def test_exact_approval_allows_the_bound_operation_to_execute():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.APPROVED),
        event(3, ApprovalAction.EXECUTE),
    ]
    result = replay_approval(events)
    assert result.approved == ("write:plan",)
    assert result.denied == ()
    assert result.executed == ("write:plan",)


def test_denial_blocks_execution_of_the_exact_request():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.DENIED),
        event(3, ApprovalAction.EXECUTE),
    ]
    with pytest.raises(ApprovalDenied, match="operation was denied"):
        replay_approval(events)


def test_payload_change_after_approval_fails_closed():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.APPROVED),
        event(3, ApprovalAction.EXECUTE, payload="plan v2"),
    ]
    with pytest.raises(ApprovalConflict, match="binding does not match"):
        replay_approval(events)


def test_tool_change_after_approval_fails_closed():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.APPROVED),
        event(3, ApprovalAction.EXECUTE, tool="delete_file"),
    ]
    with pytest.raises(ApprovalConflict, match="binding does not match"):
        replay_approval(events)


def test_conflicting_approval_decision_fails_closed():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.APPROVED),
        event(3, ApprovalAction.DENIED),
    ]
    with pytest.raises(ApprovalConflict, match="decision changed"):
        replay_approval(events)


def test_changed_request_reusing_operation_identity_fails_closed():
    events = [
        event(1, ApprovalAction.REQUESTED),
        event(2, ApprovalAction.REQUESTED, payload="plan v2"),
    ]
    with pytest.raises(ApprovalConflict, match="operation changed"):
        replay_approval(events)


def test_approval_replay_rejects_ambiguous_order():
    events = [event(2, ApprovalAction.REQUESTED), event(1, ApprovalAction.APPROVED)]
    with pytest.raises(ValueError, match="monotonically increasing"):
        replay_approval(events)
