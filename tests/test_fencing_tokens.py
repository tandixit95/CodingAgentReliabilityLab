import pytest

from agent_reliability_lab.fencing import (
    FencedState,
    FencingConflict,
    FencingToken,
    StaleFencingToken,
    acquire_authority,
    fenced_write,
)


def test_superseded_executor_cannot_commit_after_new_authority_is_issued():
    state = FencedState("repo-worktree")
    state, stale_worker = acquire_authority(state, "apply-patch")
    state, current_worker = acquire_authority(state, "apply-patch")

    with pytest.raises(StaleFencingToken, match="superseded"):
        fenced_write(state, stale_worker, "stale result")

    committed = fenced_write(state, current_worker, "current result")
    assert committed.value == "current result"
    assert committed.epoch == 2


def test_restart_with_old_token_stays_fenced_out():
    state, first = acquire_authority(FencedState("sandbox"), "run-tests")
    state, _ = acquire_authority(state, "run-tests")
    restarted_copy = FencingToken(first.resource, first.operation_id, first.epoch)

    with pytest.raises(StaleFencingToken):
        fenced_write(state, restarted_copy, "late completion")


def test_token_cannot_cross_resource_boundary():
    state, token = acquire_authority(FencedState("repo-a"), "commit")
    other = FencedState("repo-b", epoch=state.epoch, operation_id="commit")
    with pytest.raises(FencingConflict, match="different resource"):
        fenced_write(other, token, "payload")


def test_token_cannot_authorize_changed_operation():
    state, token = acquire_authority(FencedState("repo"), "commit-a")
    changed = FencedState("repo", epoch=state.epoch, operation_id="commit-b")
    with pytest.raises(FencingConflict, match="current operation"):
        fenced_write(changed, token, "payload")


def test_current_token_can_write_repeatedly_without_minting_new_authority():
    state, token = acquire_authority(FencedState("repo"), "checkpoint")
    first = fenced_write(state, token, "one")
    second = fenced_write(first, token, "two")
    assert second.value == "two"
    assert second.epoch == token.epoch == 1
