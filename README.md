# Coding Agent Reliability Lab

[![Tests](https://github.com/tandixit95/CodingAgentReliabilityLab/actions/workflows/tests.yml/badge.svg)](https://github.com/tandixit95/CodingAgentReliabilityLab/actions/workflows/tests.yml)

Reproducible failure cases for coding-agent orchestration: stale child events,
conflicting terminal states, replayed tool effects, and approvals that must survive
interruptions without silently authorizing a different operation.

**Start here:** [crash-recovery experiment](docs/DURABLE_EXECUTION.md) ·
[simulation milestones](docs/SIMULATION_MILESTONES.md) · [tests](tests)

## What is implemented

| Boundary | Executable evidence | Scope |
|---|---|---|
| Parent/child event identity | Naive and scoped reducers, terminal-conflict tests | Deterministic event simulation |
| Retry and approval binding | Exact operation/tool/payload matching, sticky denial | Deterministic event simulation |
| Restart recovery | SQLite-backed approvals, effect and completion in one transaction | Inert local database effect only |
| Crash window | Real child-process termination before/after effect and commit | Process crash, not power-loss testing |
| Concurrent replay | Six independently launched worker processes, one recorded effect | One local SQLite database |

The naive split-transaction baseline duplicates the local effect after a crash.
The transactional version either rolls back the uncommitted effect or recognizes
an already committed operation on restart. These are testable engineering
contracts, **not a claim of exactly-once execution for arbitrary remote tools**.

## Run locally

Python 3.11 or newer; no model weights, API keys, or external services required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
ruff check .
ruff format --check .
```

Run only the crash/restart and concurrent-worker experiment:

```bash
python -m pytest tests/test_durable_sandbox.py -v
```

## Minimal durable example

```python
from tempfile import TemporaryDirectory
from pathlib import Path
from agent_reliability_lab import DurableSandbox, LocalOperation

with TemporaryDirectory() as directory:
    database = Path(directory) / "sandbox.sqlite3"
    operation = LocalOperation("op-1", "record_note", "harmless local evidence")
    first = DurableSandbox(database)
    first.request(operation)
    first.decide(operation, "approved")
    assert first.execute(operation) is True

    restarted = DurableSandbox(database)
    assert restarted.execute(operation) is False
    assert restarted.notes() == (("op-1", "harmless local evidence"),)
```

## Limitations and next boundary

The effect is an inert row in the same database as its approval and completion
record. Moving it to an HTTP service, email system, shell command, or separate
filesystem breaks that atomic boundary. Such integrations require an explicit
remote idempotency/reconciliation contract; this lab does not supply one.

There is no production authentication service, distributed consensus, real model
evaluation, external adoption claim, or benchmark of production throughput.
Checkpoint lineage and recovery spanning multiple systems remain future work.

This is an AI-assisted independent engineering project. Implementation and tests
are inspectable; automated validation is not represented as independent human
review. No employer code, data, or private operational telemetry is included.
