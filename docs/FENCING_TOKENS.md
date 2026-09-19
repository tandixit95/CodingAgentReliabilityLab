# Fencing stale executors after authority changes

A lease timeout by itself does not make an old worker harmless. A paused worker can resume after another worker has acquired the same resource and still try to commit using stale local state.

This experiment models the missing write-side guard: a monotonically increasing **fencing token**. Every authority acquisition advances the resource epoch. Every mutation must present the exact current resource, operation identity, and epoch. Once newer authority exists, an older worker is rejected even if it later resumes or reconstructs its old token after restart.

The model deliberately separates this from a distributed-lock claim. It does not provide clocks, failure detectors, consensus, or a production lease service. It proves only the deterministic invariant exercised by the tests: **superseded authority cannot mutate the modeled shared state**.

Run the focused proof:

```bash
python -m pytest tests/test_fencing_tokens.py -v
```

The tests cover stale-worker rejection after authority transfer, restart with an old token, cross-resource misuse, changed-operation misuse, and valid repeated writes under the current authority epoch.
