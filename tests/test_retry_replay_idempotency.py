import pytest

from agent_reliability_lab import EffectRequest, IdempotencyConflict, replay_effects


def retry_after_lost_ack_scenario():
    return [
        EffectRequest(1, "write:plan", "write_file", "plan v1"),
        EffectRequest(2, "write:plan", "write_file", "plan v1"),
    ]


def test_naive_replay_applies_the_same_effect_twice_after_lost_ack():
    result = replay_effects(retry_after_lost_ack_scenario(), mode="naive")
    assert [request.sequence for request in result.applied] == [1, 2]
    assert result.duplicates == ()


def test_idempotent_replay_suppresses_the_exact_retry():
    result = replay_effects(retry_after_lost_ack_scenario(), mode="idempotent")
    assert [request.sequence for request in result.applied] == [1]
    assert [request.sequence for request in result.duplicates] == [2]


def test_idempotency_key_reuse_for_different_payload_fails_closed():
    requests = [
        EffectRequest(1, "write:plan", "write_file", "plan v1"),
        EffectRequest(2, "write:plan", "write_file", "plan v2"),
    ]
    with pytest.raises(IdempotencyConflict, match="reused for a different tool effect"):
        replay_effects(requests)


def test_effect_replay_rejects_ambiguous_request_order():
    requests = [
        EffectRequest(2, "op:2", "write_file", "later"),
        EffectRequest(1, "op:1", "write_file", "earlier"),
    ]
    with pytest.raises(ValueError, match="monotonically increasing"):
        replay_effects(requests)
