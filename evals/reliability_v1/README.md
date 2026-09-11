# Frozen reliability evaluation v1

This namespace freezes the first aggregate reliability evaluation for Coding Agent Reliability Lab **before any aggregate score is generated**.

## What is frozen

- **11 machine-readable scenarios** in `SCENARIOS.json`, covering event identity, replay idempotency, terminal finality, approval binding, durable restart, checkpoint lineage, cross-system readback, stale-readback revision control, evidence supersession across restart, repeated lost-ack convergence, and competing retry authority.
- **Five aggregate gates** in `PROTOCOL.json`: scenario pass rate, safety pass rate, convergence pass rate, fail-closed pass rate must each equal `1.0`; duplicate-effect violations must equal `0`.
- **Two independent normalized executions** are required, and their result JSON must be byte-identical before an aggregate result can be published.
- The scenario manifest is pinned by SHA-256: `5ca2ab5cd388037d056420e678004dd9909e479c11b84fbd6424b7a7d06c9044`.

## Freeze boundary

The protocol status is `frozen_unexecuted`. `RESULTS.json` is intentionally absent. The freeze verifier fails if an aggregate result already exists or if the scenario set, metrics, execution controls, or claim boundary drift.

```bash
python evals/reliability_v1/verify_protocol.py
```

The executable runner exists in `agent_reliability_lab.evaluation`, but the frozen suite is **not executed as part of this freeze milestone**. A later run must first verify the remote freeze commit, execute the exact suite twice, compare normalized bytes, and only then evaluate the predeclared gates.

## Claim boundary

This evaluation is a deterministic local reliability lab. Even a perfect aggregate result would not establish production reliability, exactly-once remote effects, distributed consensus, model quality, throughput, latency, or real-provider behavior. SQLite-backed and in-memory provider models remain bounded simulations.
