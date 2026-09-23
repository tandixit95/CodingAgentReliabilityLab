"""Deterministic fencing-token model for preventing stale executor writes.

This is a local model, not a distributed lock service.  The invariant is that
an authority epoch only increases and every state mutation is conditional on
the exact current epoch.  A worker that resumes after its lease was superseded
therefore cannot commit merely because it still holds old local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class StaleFencingToken(RuntimeError):
    """Raised when a superseded executor attempts to mutate shared state."""


class FencingConflict(ValueError):
    """Raised when a token is used for the wrong resource or operation."""


@dataclass(frozen=True, slots=True)
class FencingToken:
    resource: str
    operation_id: str
    epoch: int
    store_generation: str | None = None

    def __post_init__(self) -> None:
        if not self.resource.strip() or not self.operation_id.strip():
            raise ValueError("resource and operation_id must be nonempty")
        if self.epoch <= 0:
            raise ValueError("epoch must be positive")
        if self.store_generation is not None and not self.store_generation.strip():
            raise ValueError("store_generation must be nonempty when present")


@dataclass(frozen=True, slots=True)
class FencedState:
    resource: str
    epoch: int = 0
    operation_id: str | None = None
    value: str | None = None

    def __post_init__(self) -> None:
        if not self.resource.strip():
            raise ValueError("resource must be nonempty")
        if self.epoch < 0:
            raise ValueError("epoch must be nonnegative")


def acquire_authority(state: FencedState, operation_id: str) -> tuple[FencedState, FencingToken]:
    """Issue a monotonically newer authority token for one exact operation."""

    if not operation_id.strip():
        raise ValueError("operation_id must be nonempty")
    epoch = state.epoch + 1
    token = FencingToken(state.resource, operation_id, epoch)
    return FencedState(state.resource, epoch, operation_id, state.value), token


def fenced_write(state: FencedState, token: FencingToken, value: str) -> FencedState:
    """Commit only for the exact current resource, operation, and authority epoch."""

    if token.resource != state.resource:
        raise FencingConflict("fencing token belongs to a different resource")
    if token.epoch != state.epoch:
        raise StaleFencingToken(
            "fencing token was superseded; stale executor must re-acquire authority"
        )
    if token.operation_id != state.operation_id:
        raise FencingConflict("fencing token is not bound to the current operation")
    return FencedState(state.resource, state.epoch, state.operation_id, value)


class PersistentFencingStore:
    """SQLite-backed fencing epochs that survive executor restart.

    Authority acquisition and fenced mutation both serialize through the same
    database row.  This models durable write-side fencing on one local SQLite
    store; it is not a distributed lease or consensus service.
    """

    def __init__(self, database: str | Path) -> None:
        import sqlite3

        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fencing_store_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO fencing_store_metadata(singleton, generation)
                VALUES (1, lower(hex(randomblob(16))))
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fencing_state (
                    resource TEXT PRIMARY KEY,
                    epoch INTEGER NOT NULL CHECK (epoch >= 0),
                    operation_id TEXT,
                    value TEXT
                )
                """
            )
            self.store_generation = connection.execute(
                "SELECT generation FROM fencing_store_metadata WHERE singleton = 1"
            ).fetchone()[0]

    def acquire(self, resource: str, operation_id: str) -> FencingToken:
        import sqlite3

        if not resource.strip() or not operation_id.strip():
            raise ValueError("resource and operation_id must be nonempty")
        with sqlite3.connect(self.database, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = connection.execute(
                "SELECT generation FROM fencing_store_metadata WHERE singleton = 1"
            ).fetchone()
            if generation is None or self.store_generation != generation[0]:
                connection.rollback()
                raise FencingConflict(
                    "authority store was replaced; reopen it before acquiring authority"
                )
            row = connection.execute(
                "SELECT epoch FROM fencing_state WHERE resource = ?", (resource,)
            ).fetchone()
            epoch = (row[0] if row else 0) + 1
            connection.execute(
                """
                INSERT INTO fencing_state(resource, epoch, operation_id, value)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(resource) DO UPDATE SET
                    epoch = excluded.epoch,
                    operation_id = excluded.operation_id
                """,
                (resource, epoch, operation_id),
            )
            connection.commit()
        return FencingToken(resource, operation_id, epoch, self.store_generation)

    def write(self, token: FencingToken, value: str) -> None:
        import sqlite3

        with sqlite3.connect(self.database, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = connection.execute(
                "SELECT generation FROM fencing_store_metadata WHERE singleton = 1"
            ).fetchone()
            if generation is None or token.store_generation != generation[0]:
                connection.rollback()
                raise FencingConflict(
                    "fencing token belongs to a different authority-store generation"
                )
            row = connection.execute(
                "SELECT epoch, operation_id FROM fencing_state WHERE resource = ?",
                (token.resource,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise FencingConflict("fencing resource has no current authority")
            epoch, operation_id = row
            if token.epoch != epoch:
                connection.rollback()
                raise StaleFencingToken(
                    "fencing token was superseded; stale executor must re-acquire authority"
                )
            if token.operation_id != operation_id:
                connection.rollback()
                raise FencingConflict("fencing token is not bound to the current operation")
            connection.execute(
                "UPDATE fencing_state SET value = ? WHERE resource = ?", (value, token.resource)
            )
            connection.commit()

    def state(self, resource: str) -> FencedState:
        import sqlite3

        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT epoch, operation_id, value FROM fencing_state WHERE resource = ?",
                (resource,),
            ).fetchone()
        if row is None:
            return FencedState(resource)
        return FencedState(resource, row[0], row[1], row[2])
