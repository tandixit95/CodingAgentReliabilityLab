from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_reliability_lab.approvals import ApprovalConflict, ApprovalDenied, ApprovalRequired
from agent_reliability_lab.durable import DurableSandbox, LocalOperation
from agent_reliability_lab.effects import IdempotencyConflict

OP = LocalOperation("op-1", "record_note", "a harmless local note")
CHILD = """
import os, sys
from agent_reliability_lab.durable import DurableSandbox, LocalOperation
store = DurableSandbox(sys.argv[1])
op = LocalOperation('op-1', 'record_note', 'a harmless local note')
store.request(op)
store.decide(op, 'approved')
def checkpoint(stage):
    if stage == sys.argv[2]:
        os._exit(73)
print(int(store.execute(op, _checkpoint=checkpoint)), flush=True)
"""


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def test_restart_preserves_exact_effect(tmp_path):
    db = tmp_path / "sandbox.sqlite3"
    store = DurableSandbox(db)
    store.request(OP)
    store.decide(OP, "approved")
    assert store.execute(OP)
    restarted = DurableSandbox(db)
    restarted.request(OP)
    restarted.decide(OP, "approved")
    assert not restarted.execute(OP)
    assert restarted.notes() == ((OP.operation_id, OP.payload),)


def test_approval_and_denial_survive_restart(tmp_path):
    db = tmp_path / "sandbox.sqlite3"
    store = DurableSandbox(db)
    with pytest.raises(ApprovalRequired):
        store.execute(OP)
    store.request(OP)
    with pytest.raises(ApprovalRequired):
        store.execute(OP)
    store.decide(OP, "denied")
    restarted = DurableSandbox(db)
    restarted.request(OP)
    with pytest.raises(ApprovalDenied):
        restarted.execute(OP)
    with pytest.raises(ApprovalConflict):
        restarted.decide(OP, "approved")
    assert restarted.notes() == ()


@pytest.mark.parametrize("action", ["request", "decide", "execute"])
def test_changed_payload_cannot_reuse_approval(tmp_path, action):
    store = DurableSandbox(tmp_path / "sandbox.sqlite3")
    store.request(OP)
    store.decide(OP, "approved")
    changed = LocalOperation(OP.operation_id, OP.tool, "changed")
    with pytest.raises(IdempotencyConflict):
        if action == "decide":
            store.decide(changed, "approved")
        else:
            getattr(store, action)(changed)
    assert store.notes() == ()


def test_invalid_identity_tool_decision_and_schema_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        LocalOperation(" ", "record_note", "x")
    with pytest.raises(ValueError):
        LocalOperation("x", "send_email", "x")
    with pytest.raises(TypeError):
        LocalOperation("x", "record_note", {})
    with pytest.raises(ValueError):
        DurableSandbox(":memory:")
    db = tmp_path / "sandbox.sqlite3"
    store = DurableSandbox(db)
    with pytest.raises(ValueError):
        store.decide(OP, "maybe")
    with pytest.raises(ApprovalRequired):
        store.decide(OP, "approved")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        DurableSandbox(db)


@pytest.mark.parametrize(
    "stage", ["before_effect", "after_effect", "before_commit", "after_commit"]
)
def test_real_process_exit_and_restart(tmp_path, stage):
    db = tmp_path / "sandbox.sqlite3"
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(db), stage],
        env=child_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 73, result.stderr
    recovered = DurableSandbox(db)
    already_committed = stage == "after_commit"
    assert len(recovered.notes()) == int(already_committed)
    assert recovered.execute(OP) is not already_committed
    assert not recovered.execute(OP)
    assert recovered.notes() == ((OP.operation_id, OP.payload),)


def test_concurrent_processes_apply_one_local_effect(tmp_path):
    db = tmp_path / "sandbox.sqlite3"
    DurableSandbox(db)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, str(db), "never"],
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    try:
        outputs = [p.communicate(timeout=25) for p in processes]
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill()
                p.wait()
    assert all(p.returncode == 0 for p in processes), outputs
    assert sorted(out.strip() for out, _ in outputs) == ["0", "0", "0", "0", "0", "1"]
    assert DurableSandbox(db).notes() == ((OP.operation_id, OP.payload),)


def test_naive_split_transaction_duplicates_after_real_crash(tmp_path):
    db = tmp_path / "naive.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE effects (operation_id TEXT)")
        conn.execute("CREATE TABLE completed (operation_id TEXT PRIMARY KEY)")
    naive = """
import os, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute("INSERT INTO effects VALUES ('op-1')")
os._exit(73)  # effect committed, but acknowledgement never recorded
"""
    result = subprocess.run([sys.executable, "-c", naive, str(db)], timeout=25, check=False)
    assert result.returncode == 73
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM completed").fetchone()[0] == 0
        conn.execute("INSERT INTO effects VALUES ('op-1')")
        conn.execute("INSERT INTO completed VALUES ('op-1')")
        assert conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 2
