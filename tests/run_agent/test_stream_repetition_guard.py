"""Focused tests for degenerate provider stream detection."""

from agent.stream_repetition_guard import StreamRepetitionGuard


def test_ellipsis_repetition_triggers_across_deltas():
    guard = StreamRepetitionGuard()
    assert guard.observe("Preface.\n\n...\n") is None
    for _ in range(14):
        assert guard.observe("\n...\n") is None
    match = guard.observe("\n...\n")
    assert match is not None
    assert match.repetitions == 16


def test_lists_tables_and_code_do_not_trigger():
    samples = [
        "- item\n" * 40,
        "| a | b |\n" * 40,
        "```python\n" + "pass\n" * 40 + "```\n",
        "    return value\n" * 40,
        "}\n" * 40,
    ]
    for sample in samples:
        assert StreamRepetitionGuard().observe(sample) is None


def test_repeated_pair_of_blocks_triggers():
    guard = StreamRepetitionGuard()
    first = (
        "I inspected the available material and recorded the relevant fields, "
        "row ranges, and limitations before deciding what to do next."
    )
    second = (
        "The next operation should use those observations directly instead of "
        "restating the same analysis plan without taking action."
    )
    assert guard.observe(first + "\n\n" + second + "\n\n") is None
    assert guard.observe(first + "\n\n") is None
    match = guard.observe(second + "\n\n")
    assert match is not None
    assert match.repetitions == 2


def test_similar_repeated_chinese_action_intent_triggers():
    guard = StreamRepetitionGuard()
    assert guard.observe("下一步，我需要读取当前材料并记录实际结果。\n\n") is None
    assert guard.observe("下一步，我需要读取当前材料，然后记录实际结果。\n\n") is None
    match = guard.observe("下一步，我需要读取当前材料并且记录实际结果。\n\n")
    assert match is not None
    assert match.repetitions == 3
