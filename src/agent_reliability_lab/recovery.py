"""Durable checkpoint lineage for deterministic partial-progress recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class CheckpointConflict(ValueError):
    """Raised when checkpoint identity or plan binding is contradictory."""


class StaleCheckpoint(ValueError):
    """Raised when recovery tries to resume from a checkpoint that is no longer current."""


@dataclass(frozen=True, slots=True)
class RecoveryOperation:
    """One exact operation in a recoverable run plan."""

    operation_id: str
    tool: str
    payload: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("operation_id must be a nonempty string")
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise ValueError("tool must be a nonempty string")
        if not isinstance(self.payload, str):
            raise TypeError("payload must be a string; recovery matching is exact")


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    """Immutable persisted progress marker for one exact run plan."""

    run_id: str
    checkpoint_id: str
    sequence: int
    parent_checkpoint_id: str | None
    plan_sha256: str
    completed_count: int


class CheckpointStore:
    """Persist a linear checkpoint head and reject stale or conflicting recovery."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database == ":memory:":
            raise ValueError("a file-backed database is required for restart recovery")
        with self._transaction() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"unsupported checkpoint database schema: {version}")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS recovery_runs ("
                "run_id TEXT PRIMARY KEY NOT NULL, plan_json TEXT NOT NULL, "
                "plan_sha256 TEXT NOT NULL, operation_count INTEGER NOT NULL, "
                "head_checkpoint_id TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS recovery_checkpoints ("
                "run_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
                "parent_checkpoint_id TEXT, plan_sha256 TEXT NOT NULL, "
                "completed_count INTEGER NOT NULL, "
                "PRIMARY KEY (run_id, checkpoint_id), UNIQUE (run_id, sequence))"
            )
            conn.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _materialize_plan(
        operations: Iterable[RecoveryOperation],
    ) -> tuple[tuple[RecoveryOperation, ...], str, str]:
        plan = tuple(operations)
        if not plan:
            raise ValueError("recovery plan must contain at least one operation")
        operation_ids = [operation.operation_id for operation in plan]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("recovery plan operation_ids must be unique")
        plan_json = json.dumps(
            [[operation.operation_id, operation.tool, operation.payload] for operation in plan],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        plan_sha256 = hashlib.sha256(plan_json.encode()).hexdigest()
        return plan, plan_json, plan_sha256

    @staticmethod
    def _checkpoint_from_row(row: tuple) -> RecoveryCheckpoint:
        return RecoveryCheckpoint(
            run_id=row[0],
            checkpoint_id=row[1],
            sequence=row[2],
            parent_checkpoint_id=row[3],
            plan_sha256=row[4],
            completed_count=row[5],
        )

    @classmethod
    def _load_checkpoint(
        cls, conn: sqlite3.Connection, run_id: str, checkpoint_id: str
    ) -> RecoveryCheckpoint | None:
        row = conn.execute(
            "SELECT run_id, checkpoint_id, sequence, parent_checkpoint_id, "
            "plan_sha256, completed_count FROM recovery_checkpoints "
            "WHERE run_id=? AND checkpoint_id=?",
            (run_id, checkpoint_id),
        ).fetchone()
        return None if row is None else cls._checkpoint_from_row(row)

    @staticmethod
    def _same_checkpoint(left: RecoveryCheckpoint, right: RecoveryCheckpoint) -> bool:
        return left == right

    def start(
        self,
        run_id: str,
        operations: Iterable[RecoveryOperation],
        *,
        checkpoint_id: str = "start",
    ) -> RecoveryCheckpoint:
        """Start a run or replay its exact initial checkpoint after restart."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a nonempty string")
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise ValueError("checkpoint_id must be a nonempty string")
        plan, plan_json, plan_sha256 = self._materialize_plan(operations)
        initial = RecoveryCheckpoint(run_id, checkpoint_id, 0, None, plan_sha256, 0)
        with self._transaction() as conn:
            run = conn.execute(
                "SELECT plan_json, plan_sha256, operation_count, head_checkpoint_id "
                "FROM recovery_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                conn.execute(
                    "INSERT INTO recovery_runs VALUES (?, ?, ?, ?, ?)",
                    (run_id, plan_json, plan_sha256, len(plan), checkpoint_id),
                )
                conn.execute(
                    "INSERT INTO recovery_checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, checkpoint_id, 0, None, plan_sha256, 0),
                )
                return initial
            if run[:3] != (plan_json, plan_sha256, len(plan)):
                raise CheckpointConflict("run_id is already bound to a different exact plan")
            existing = self._load_checkpoint(conn, run_id, checkpoint_id)
            if existing is None or not self._same_checkpoint(existing, initial):
                raise CheckpointConflict(
                    "initial checkpoint identity conflicts with persisted lineage"
                )
            return existing

    def current(self, run_id: str) -> RecoveryCheckpoint:
        """Return the currently authoritative checkpoint for a run."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT head_checkpoint_id FROM recovery_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise CheckpointConflict(f"unknown recovery run: {run_id}")
            checkpoint = self._load_checkpoint(conn, run_id, row[0])
            if checkpoint is None:
                raise CheckpointConflict("persisted recovery head is missing")
            return checkpoint

    def advance(
        self,
        parent: RecoveryCheckpoint,
        checkpoint_id: str,
        *,
        completed_count: int,
    ) -> RecoveryCheckpoint:
        """Commit a new linear checkpoint after verified partial progress.

        Exact replay of an already committed advance is idempotent. A fork from an
        older parent, a reused checkpoint identity with different contents, progress
        regression, or progress beyond the bound plan fails closed.
        """
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise ValueError("checkpoint_id must be a nonempty string")
        if not isinstance(completed_count, int) or isinstance(completed_count, bool):
            raise TypeError("completed_count must be an integer")
        with self._transaction() as conn:
            run = conn.execute(
                "SELECT plan_sha256, operation_count, head_checkpoint_id "
                "FROM recovery_runs WHERE run_id=?",
                (parent.run_id,),
            ).fetchone()
            if run is None:
                raise CheckpointConflict(f"unknown recovery run: {parent.run_id}")
            plan_sha256, operation_count, head_id = run
            persisted_parent = self._load_checkpoint(conn, parent.run_id, parent.checkpoint_id)
            if persisted_parent is None or not self._same_checkpoint(persisted_parent, parent):
                raise CheckpointConflict("parent checkpoint does not match persisted lineage")
            candidate = RecoveryCheckpoint(
                parent.run_id,
                checkpoint_id,
                parent.sequence + 1,
                parent.checkpoint_id,
                plan_sha256,
                completed_count,
            )
            existing = self._load_checkpoint(conn, parent.run_id, checkpoint_id)
            if existing is not None:
                if self._same_checkpoint(existing, candidate) and head_id == checkpoint_id:
                    return existing
                raise CheckpointConflict("checkpoint_id is already bound to different progress")
            if head_id != parent.checkpoint_id:
                raise StaleCheckpoint(
                    f"checkpoint {parent.checkpoint_id} is stale; current head is {head_id}"
                )
            if parent.plan_sha256 != plan_sha256:
                raise CheckpointConflict("parent checkpoint plan binding does not match the run")
            if completed_count <= parent.completed_count:
                raise CheckpointConflict("checkpoint progress must advance beyond its parent")
            if completed_count > operation_count:
                raise CheckpointConflict("checkpoint progress exceeds the bound recovery plan")
            conn.execute(
                "INSERT INTO recovery_checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate.run_id,
                    candidate.checkpoint_id,
                    candidate.sequence,
                    candidate.parent_checkpoint_id,
                    candidate.plan_sha256,
                    candidate.completed_count,
                ),
            )
            conn.execute(
                "UPDATE recovery_runs SET head_checkpoint_id=? WHERE run_id=?",
                (checkpoint_id, parent.run_id),
            )
            return candidate

    def resume(
        self,
        checkpoint: RecoveryCheckpoint,
        operations: Iterable[RecoveryOperation],
    ) -> tuple[RecoveryOperation, ...]:
        """Return only remaining operations from the exact current checkpoint."""
        plan, plan_json, plan_sha256 = self._materialize_plan(operations)
        with self._transaction() as conn:
            run = conn.execute(
                "SELECT plan_json, plan_sha256, operation_count, head_checkpoint_id "
                "FROM recovery_runs WHERE run_id=?",
                (checkpoint.run_id,),
            ).fetchone()
            if run is None:
                raise CheckpointConflict(f"unknown recovery run: {checkpoint.run_id}")
            if run[:3] != (plan_json, plan_sha256, len(plan)):
                raise CheckpointConflict("resume plan does not match the run's exact bound plan")
            persisted = self._load_checkpoint(conn, checkpoint.run_id, checkpoint.checkpoint_id)
            if persisted is None or not self._same_checkpoint(persisted, checkpoint):
                raise CheckpointConflict("resume checkpoint does not match persisted lineage")
            if run[3] != checkpoint.checkpoint_id:
                raise StaleCheckpoint(
                    f"checkpoint {checkpoint.checkpoint_id} is stale; current head is {run[3]}"
                )
            return plan[checkpoint.completed_count :]
