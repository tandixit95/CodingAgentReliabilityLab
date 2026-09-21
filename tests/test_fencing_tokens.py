import multiprocessing

import pytest

from agent_reliability_lab.fencing import (
    FencedState,
    FencingConflict,
    FencingToken,
    StaleFencingToken,
    acquire_authority,
    fenced_write,
)


def _acquire_in_process(database, resource, operation_id, start_event, queue):
    from agent_reliability_lab.fencing import PersistentFencingStore

    start_event.wait(timeout=10)
    token = PersistentFencingStore(database).acquire(resource, operation_id)
    queue.put((token.resource, token.operation_id, token.epoch))


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


def test_persistent_epoch_survives_store_restart(tmp_path):
    from agent_reliability_lab.fencing import PersistentFencingStore

    database = tmp_path / "fencing.sqlite3"
    first_store = PersistentFencingStore(database)
    stale = first_store.acquire("repo", "publish")

    restarted_store = PersistentFencingStore(database)
    current = restarted_store.acquire("repo", "publish")

    with pytest.raises(StaleFencingToken, match="superseded"):
        first_store.write(stale, "stale publication")

    restarted_store.write(current, "current publication")
    assert PersistentFencingStore(database).state("repo") == FencedState(
        "repo", epoch=2, operation_id="publish", value="current publication"
    )


def test_persistent_epoch_never_resets_when_resource_is_reacquired(tmp_path):
    from agent_reliability_lab.fencing import PersistentFencingStore

    database = tmp_path / "fencing.sqlite3"
    epochs = []
    for _ in range(4):
        store = PersistentFencingStore(database)
        epochs.append(store.acquire("worktree", "edit").epoch)

    assert epochs == [1, 2, 3, 4]


def test_persistent_store_fails_closed_for_unknown_resource(tmp_path):
    from agent_reliability_lab.fencing import PersistentFencingStore

    store = PersistentFencingStore(tmp_path / "fencing.sqlite3")
    token = FencingToken("missing", "write", 1)

    with pytest.raises(FencingConflict, match="no current authority"):
        store.write(token, "payload")


def test_persistent_store_serializes_competing_process_authority(tmp_path):
    from agent_reliability_lab.fencing import PersistentFencingStore

    database = tmp_path / "fencing.sqlite3"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(
            target=_acquire_in_process,
            args=(database, "shared-repo", f"worker-{index}", start_event, queue),
        )
        for index in range(6)
    ]
    for process in processes:
        process.start()
    start_event.set()

    results = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    tokens = [FencingToken(*result) for result in results]
    assert sorted(token.epoch for token in tokens) == [1, 2, 3, 4, 5, 6]

    current = max(tokens, key=lambda token: token.epoch)
    stale = min(tokens, key=lambda token: token.epoch)
    store = PersistentFencingStore(database)
    with pytest.raises(StaleFencingToken, match="superseded"):
        store.write(stale, "stale process result")

    store.write(current, "winning process result")
    assert store.state("shared-repo") == FencedState(
        "shared-repo",
        epoch=6,
        operation_id=current.operation_id,
        value="winning process result",
    )
