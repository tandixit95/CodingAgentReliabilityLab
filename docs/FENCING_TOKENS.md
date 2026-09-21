# Fencing stale executors after authority changes

A lease timeout by itself does not make an old worker harmless. A paused worker can resume after another worker has acquired the same resource and still try to commit using stale local state.

This experiment models the missing write-side guard: a monotonically increasing **fencing token**. Every authority acquisition advances the resource epoch. Every mutation must present the exact current resource, operation identity, and epoch. Once newer authority exists, an older worker is rejected even if it later resumes or reconstructs its old token after restart.

The model deliberately separates this from a distributed-lock claim. It does not provide clocks, failure detectors, consensus, or a production lease service. It proves only the deterministic invariant exercised by the tests: **superseded authority cannot mutate the modeled shared state**.

Run the focused proof:

```bash
python -m pytest tests/test_fencing_tokens.py -v
```

The tests cover stale-worker rejection after authority transfer, restart with an old token, cross-resource misuse, changed-operation misuse, and valid repeated writes under the current authority epoch.

## Durable epoch boundary

The in-memory model is useful only while the authoritative state itself survives. If an executor restarts and reconstructs authority from a fresh epoch counter, an old token can become current again. `PersistentFencingStore` therefore stores the resource epoch in SQLite and advances it inside a serialized transaction. A restarted store observes the prior epoch before issuing newer authority, and a stale pre-restart token remains fenced out at the write boundary.

This closes one specific restart hole for a **single local SQLite authority store**. It still does not provide a distributed lease, clock, failure detector, consensus, cross-database atomicity, or protection when an external side effect ignores the fencing token.

## Competing-process boundary

The persistent store is also exercised with six independent Python processes released against the same SQLite resource at once. `BEGIN IMMEDIATE` serializes those authority acquisitions into unique epochs `1..6`; after contention settles, the lowest epoch is rejected as stale and only the highest epoch can write. This demonstrates local cross-process serialization through one SQLite database file. It does **not** extend the claim to multiple database replicas, network partitions, distributed consensus, or external systems that do not enforce the token.
