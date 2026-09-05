from __future__ import annotations

import pytest

from agent_reliability_lab.recovery import (
    CheckpointConflict,
    CheckpointStore,
    RecoveryCheckpoint,
    RecoveryOperation,
    StaleCheckpoint,
)


def plan() -> tuple[RecoveryOperation, ...]:
    return (
        RecoveryOperation("op-1", "edit", "first"),
        RecoveryOperation("op-2", "test", "second"),
        RecoveryOperation("op-3", "publish", "third"),
    )


def test_restart_resumes_only_from_current_checkpoint(tmp_path):
    db = tmp_path / "recovery.sqlite3"
    store = CheckpointStore(db)
    start = store.start("run-1", plan())
    checkpoint = store.advance(start, "after-op-1", completed_count=1)

    restarted = CheckpointStore(db)
    assert restarted.current("run-1") == checkpoint
    assert [operation.operation_id for operation in restarted.resume(checkpoint, plan())] == [
        "op-2",
        "op-3",
    ]


def test_stale_checkpoint_would_duplicate_partial_progress_but_fails_closed(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    start = store.start("run-1", plan())
    after_one = store.advance(start, "after-op-1", completed_count=1)
    after_two = store.advance(after_one, "after-op-2", completed_count=2)

    naive_remaining_from_stale = plan()[after_one.completed_count :]
    assert naive_remaining_from_stale[0].operation_id == "op-2"
    assert after_two.completed_count == 2  # op-2 has already completed in the current lineage
    with pytest.raises(StaleCheckpoint, match="stale"):
        store.resume(after_one, plan())
    assert [operation.operation_id for operation in store.resume(after_two, plan())] == ["op-3"]


def test_exact_advance_replay_is_idempotent_after_restart(tmp_path):
    db = tmp_path / "recovery.sqlite3"
    store = CheckpointStore(db)
    start = store.start("run-1", plan())
    committed = store.advance(start, "after-op-1", completed_count=1)

    restarted = CheckpointStore(db)
    assert restarted.advance(start, "after-op-1", completed_count=1) == committed
    assert restarted.current("run-1") == committed


def test_checkpoint_identity_cannot_be_reused_for_different_progress(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    start = store.start("run-1", plan())
    store.advance(start, "checkpoint-1", completed_count=1)
    with pytest.raises(CheckpointConflict, match="different progress"):
        store.advance(start, "checkpoint-1", completed_count=2)


def test_fork_from_stale_parent_is_rejected(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    start = store.start("run-1", plan())
    store.advance(start, "checkpoint-1", completed_count=1)
    with pytest.raises(StaleCheckpoint, match="stale"):
        store.advance(start, "forked-checkpoint", completed_count=2)


def test_resume_is_bound_to_exact_operation_payloads(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    checkpoint = store.start("run-1", plan())
    changed = list(plan())
    changed[1] = RecoveryOperation("op-2", "test", "mutated payload")
    with pytest.raises(CheckpointConflict, match="exact bound plan"):
        store.resume(checkpoint, changed)
    with pytest.raises(CheckpointConflict, match="different exact plan"):
        store.start("run-1", changed)


def test_cross_run_or_forged_checkpoint_cannot_resume(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    checkpoint = store.start("run-1", plan())
    store.start("run-2", plan(), checkpoint_id="other-start")
    forged = RecoveryCheckpoint(
        "run-2",
        checkpoint.checkpoint_id,
        checkpoint.sequence,
        checkpoint.parent_checkpoint_id,
        checkpoint.plan_sha256,
        checkpoint.completed_count,
    )
    with pytest.raises(CheckpointConflict, match="persisted lineage"):
        store.resume(forged, plan())


def test_invalid_or_ambiguous_progress_is_rejected(tmp_path):
    store = CheckpointStore(tmp_path / "recovery.sqlite3")
    start = store.start("run-1", plan())
    with pytest.raises(CheckpointConflict, match="advance"):
        store.advance(start, "no-progress", completed_count=0)
    with pytest.raises(CheckpointConflict, match="exceeds"):
        store.advance(start, "too-far", completed_count=4)
    with pytest.raises(TypeError, match="integer"):
        store.advance(start, "fractional", completed_count=1.5)
    duplicate_ids = (
        RecoveryOperation("same", "edit", "one"),
        RecoveryOperation("same", "test", "two"),
    )
    with pytest.raises(ValueError, match="unique"):
        store.start("run-duplicate", duplicate_ids)
