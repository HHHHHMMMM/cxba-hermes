from types import SimpleNamespace

from agent.conversation_loop import (
    _emit_usage_updated,
    _record_response_usage,
    _usage_updated_payload,
)


def _agent(**overrides):
    events = []
    values = {
        "model": "qwen3.6-27b",
        "session_input_tokens": 367_000,
        "session_output_tokens": 6_300,
        "session_reasoning_tokens": 1_330,
        "session_prompt_tokens": 367_000,
        "session_completion_tokens": 6_300,
        "session_total_tokens": 373_300,
        "session_api_calls": 16,
        "session_cache_read_tokens": 330_000,
        "session_cache_write_tokens": 0,
        "context_compressor": SimpleNamespace(
            last_prompt_tokens=63_076,
            context_length=131_072,
            compression_count=2,
        ),
        "event_callback": lambda event_type, payload: events.append((event_type, payload)),
    }
    values.update(overrides)
    return SimpleNamespace(**values), events


def test_builds_cumulative_usage_snapshot_for_live_ui():
    agent, _events = _agent()

    assert _usage_updated_payload(agent) == {
        "model": "qwen3.6-27b",
        "input": 367_000,
        "output": 6_300,
        "reasoning": 1_330,
        "prompt": 367_000,
        "completion": 6_300,
        "total": 373_300,
        "calls": 16,
        "cache_read": 330_000,
        "cache_write": 0,
        "context_used": 63_076,
        "context_max": 131_072,
        "context_percent": 48,
        "compressions": 2,
    }


def test_emits_usage_event_and_fails_open_when_observer_breaks():
    agent, events = _agent()
    _emit_usage_updated(agent)

    assert events == [("usage.updated", _usage_updated_payload(agent))]

    broken, _ = _agent(event_callback=lambda _event_type, _payload: (_ for _ in ()).throw(RuntimeError("broken")))
    _emit_usage_updated(broken)


def test_records_each_provider_response_before_emitting_live_totals():
    agent, events = _agent(
        session_input_tokens=100,
        session_output_tokens=20,
        session_reasoning_tokens=0,
        session_prompt_tokens=100,
        session_completion_tokens=20,
        session_total_tokens=120,
        session_api_calls=1,
        session_cache_read_tokens=0,
        session_cache_write_tokens=0,
    )
    usage = SimpleNamespace(
        prompt_tokens=200,
        input_tokens=200,
        output_tokens=40,
        total_tokens=240,
        cache_read_tokens=150,
        cache_write_tokens=10,
        reasoning_tokens=5,
    )

    _record_response_usage(agent, usage)

    assert agent.session_api_calls == 2
    assert agent.session_input_tokens == 300
    assert agent.session_output_tokens == 60
    assert agent.session_cache_read_tokens == 150
    assert agent.session_cache_write_tokens == 10
    assert agent.session_reasoning_tokens == 5
    assert events[-1][0] == "usage.updated"
    assert events[-1][1]["calls"] == 2
    assert events[-1][1]["input"] == 300
