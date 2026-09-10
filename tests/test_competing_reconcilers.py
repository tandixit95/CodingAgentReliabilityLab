from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agent_reliability_lab.evidence_lineage import ReconciliationEvidenceStore
from agent_reliability_lab.reconciliation import (
    ExternalOperation,
    ProviderReadbackState,
    StaleProviderReadback,
    VersionedProviderStore,
)

OP = ExternalOperation("provider-a", "op-race", "create_record", "payload-v1")

WORKER = r"""
import json, os, sys, time
from pathlib import Path
from agent_reliability_lab.evidence_lineage import ReconciliationEvidenceStore
from agent_reliability_lab.reconciliation import ExternalOperation, StaleProviderReadback, VersionedProviderStore

operation = ExternalOperation('provider-a', 'op-race', 'create_record', 'payload-v1')
evidence = ReconciliationEvidenceStore(sys.argv[1])
provider = VersionedProviderStore(sys.argv[2])
retry = evidence.current(operation)
Path(sys.argv[3]).write_text('ready')
while not Path(sys.argv[4]).exists():
    time.sleep(0.005)
try:
    provider.conditional_apply(operation, retry.decision)
    outcome = 'committed'
except StaleProviderReadback:
    outcome = 'stale'
readback = provider.read(operation)
current = evidence.record(
    operation,
    f'worker-{os.getpid()}-present-{readback.readback_revision}',
    readback,
)
completion = evidence.mark_reconciled(operation, current.evidence_id, current.decision)
print(json.dumps({'outcome': outcome, 'revision': completion.revision}), flush=True)
"""


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def test_two_restarted_reconcilers_commit_once_then_converge(tmp_path):
    evidence_db = tmp_path / "evidence.sqlite3"
    provider_db = tmp_path / "provider.sqlite3"
    provider = VersionedProviderStore(provider_db)
    initial = provider.read(OP)
    assert initial.readback is ProviderReadbackState.ABSENT
    assert initial.readback_revision == 0
    ReconciliationEvidenceStore(evidence_db).record(OP, "absent-0", initial)

    start = tmp_path / "start"
    ready = [tmp_path / "ready-1", tmp_path / "ready-2"]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                WORKER,
                str(evidence_db),
                str(provider_db),
                str(marker),
                str(start),
            ],
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for marker in ready
    ]
    try:
        deadline = time.monotonic() + 10
        while not all(marker.exists() for marker in ready):
            if time.monotonic() >= deadline:
                raise AssertionError("workers did not reach the competing-retry barrier")
            time.sleep(0.01)
        start.write_text("go")
        outputs = [process.communicate(timeout=25) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert all(process.returncode == 0 for process in processes), outputs
    results = [json.loads(stdout) for stdout, _ in outputs]
    assert sorted(result["outcome"] for result in results) == ["committed", "stale"]
    assert {result["revision"] for result in results} == {1}

    restarted_provider = VersionedProviderStore(provider_db)
    final_provider = restarted_provider.snapshot(OP.provider)
    assert final_provider.revision == 1
    assert final_provider.effects == (OP,)

    restarted_evidence = ReconciliationEvidenceStore(evidence_db)
    head = restarted_evidence.current(OP)
    completion = restarted_evidence.completion(OP)
    assert head.revision == 1
    assert head.decision.action == "accept_remote_commit"
    assert head.decision.should_retry is False
    assert completion is not None
    assert completion.revision == 1


def test_equivalent_same_revision_readbacks_from_independent_workers_are_idempotent(tmp_path):
    store = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3")
    provider = VersionedProviderStore(tmp_path / "provider.sqlite3")
    first = store.record(OP, "worker-a-read-0", provider.read(OP))
    second = store.record(OP, "worker-b-read-0", provider.read(OP))
    assert second == first


def test_provider_store_rejects_stale_second_conditional_retry(tmp_path):
    provider = VersionedProviderStore(tmp_path / "provider.sqlite3")
    evidence = provider.read(OP)
    retry = ReconciliationEvidenceStore(tmp_path / "evidence.sqlite3").record(
        OP, "absent-0", evidence
    )
    committed = provider.conditional_apply(OP, retry.decision)
    assert committed.revision == 1
    assert committed.effects == (OP,)
    try:
        provider.conditional_apply(OP, retry.decision)
    except StaleProviderReadback as exc:
        assert "revision changed" in str(exc)
    else:
        raise AssertionError("stale competing retry should fail closed")
    assert provider.snapshot(OP.provider).effects == (OP,)
