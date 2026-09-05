"""A bounded, transactional sandbox; not exactly-once execution of remote tools.

The only effect is an inert row in the SAME SQLite database as its approval and
completion record. A remote HTTP request or filesystem write would not share this
transaction. Authentication, hostile writers, and power-loss testing are out of scope.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .approvals import ApprovalConflict, ApprovalDenied, ApprovalRequired
from .effects import IdempotencyConflict


@dataclass(frozen=True, slots=True)
class LocalOperation:
    operation_id: str
    tool: str
    payload: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("operation_id must be a nonempty string")
        if self.tool != "record_note":
            raise ValueError("this sandbox supports only the inert record_note tool")
        if not isinstance(self.payload, str):
            raise TypeError("payload must be a string; matching is exact, not semantic")


class DurableSandbox:
    """Persist exact approvals and a local effect atomically across process restarts."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        if self.database == ":memory:":
            raise ValueError("a file-backed database is required for restart recovery")
        with self._transaction() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"unsupported sandbox database schema: {version}")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS operations ("
                "operation_id TEXT PRIMARY KEY NOT NULL, tool TEXT NOT NULL, "
                "payload TEXT NOT NULL, decision TEXT "
                "CHECK (decision IN ('approved', 'denied')), "
                "completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS notes ("
                "operation_id TEXT PRIMARY KEY NOT NULL REFERENCES operations(operation_id), "
                "payload TEXT NOT NULL)"
            )
            conn.execute("PRAGMA user_version=1")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
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
    def _existing(conn: sqlite3.Connection, operation: LocalOperation) -> tuple:
        row = conn.execute(
            "SELECT tool, payload, decision, completed FROM operations WHERE operation_id=?",
            (operation.operation_id,),
        ).fetchone()
        if row is None:
            raise ApprovalRequired("operation must be requested before a decision or execution")
        if row[:2] != (operation.tool, operation.payload):
            raise IdempotencyConflict("operation_id is already bound to a different exact effect")
        return row

    def request(self, operation: LocalOperation) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO operations(operation_id, tool, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(operation_id) DO NOTHING",
                (operation.operation_id, operation.tool, operation.payload),
            )
            self._existing(conn, operation)

    def decide(self, operation: LocalOperation, decision: Literal["approved", "denied"]) -> None:
        if decision not in ("approved", "denied"):
            raise ValueError("decision must be approved or denied")
        with self._transaction() as conn:
            previous = self._existing(conn, operation)[2]
            if previous is not None and previous != decision:
                raise ApprovalConflict("an existing decision cannot be reversed by replay")
            conn.execute(
                "UPDATE operations SET decision=? WHERE operation_id=?",
                (decision, operation.operation_id),
            )

    def execute(
        self, operation: LocalOperation, *, _checkpoint: Callable[[str], None] | None = None
    ) -> bool:
        """Return True for a new effect, False for an exact completed replay.

        ``_checkpoint`` is test instrumentation for terminating a test child at
        precise transaction boundaries. It is not a durable callback mechanism.
        """
        checkpoint = _checkpoint or (lambda stage: None)
        with self._transaction() as conn:
            _, _, decision, completed = self._existing(conn, operation)
            if decision is None:
                raise ApprovalRequired("an exact approval is required")
            if decision == "denied":
                raise ApprovalDenied("the exact operation was denied")
            if completed:
                return False
            checkpoint("before_effect")
            conn.execute(
                "INSERT INTO notes(operation_id, payload) VALUES (?, ?)",
                (operation.operation_id, operation.payload),
            )
            checkpoint("after_effect")
            conn.execute(
                "UPDATE operations SET completed=1 WHERE operation_id=?", (operation.operation_id,)
            )
            checkpoint("before_commit")
        checkpoint("after_commit")
        return True

    def notes(self) -> tuple[tuple[str, str], ...]:
        with self._transaction() as conn:
            return tuple(
                conn.execute("SELECT operation_id, payload FROM notes ORDER BY operation_id")
            )
