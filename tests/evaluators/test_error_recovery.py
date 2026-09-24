"""错误自恢复率评测器的行为测试。

覆盖:紧凑 JSON 值列结构与键序、恢复合成规则(闭环 ∧ 非空正文)、不可计算
原因码(no_error_events / no_error_events_observable / missing_turn_boundary)、
结构错误映射、降级语义标签校验、had_error 与错误计数守恒校验、非法标志值
拒绝、输入不变,以及与研究 collector 的端到端一致性。
"""

import copy
import json
from typing import cast

import pytest

from collectors.error_recovery import normalize_research_error_recovery
from evaluators.base import EvaluationStatus
from evaluators.error_recovery import ErrorRecoveryRateEvaluator

_VALUE_KEYS = (
    "error_recovered",
    "had_error",
    "llm_error_count",
    "tool_error_call_count",
    "retry_recovered_error_count",
)


def _normalized(**overrides: object) -> dict[str, object]:
    """构造 had_error=1、闭环、有正文的合法研究规范化输入。"""

    trace: dict[str, object] = {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "recovery_semantics": "degraded_v1",
        "had_error": 1,
        "turn_completed": 1,
        "body_present": 1,
        "body_channel": "final_text",
        "diagnostics": {
            "llm_error_event_count": 2,
            "tool_error_call_count": 1,
            "retry_recovered_error_count": 1,
            "retry_unpaired_error_count": 1,
            "llm_loop_missing_error_count": 0,
        },
        "normalization_error": None,
    }
    trace.update(overrides)
    return trace


def test_computes_recovered_flag_and_compact_json_value() -> None:
    result = ErrorRecoveryRateEvaluator().evaluate(_normalized())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metric_name == "error_recovery_rate"
    assert result.evaluator_version == "1.1.0"
    assert isinstance(result.value, str)
    array = json.loads(result.value)
    # 2026-09-21 需求方确认:单元格统一 [] 包围,单元素 JSON 数组承载固定键序对象。
    assert isinstance(array, list) and len(array) == 1
    parsed = array[0]
    assert tuple(parsed) == _VALUE_KEYS
    assert parsed == {
        "error_recovered": 1,
        "had_error": 1,
        "llm_error_count": 2,
        "tool_error_call_count": 1,
        "retry_recovered_error_count": 1,
    }
    assert result.metadata["recovery_semantics"] == "degraded_v1"
    assert result.metadata["turn_completed"] == 1
    assert result.metadata["body_present"] == 1
    assert result.metadata["body_channel"] == "final_text"
    assert result.metadata["retry_unpaired_error_count"] == 1


@pytest.mark.parametrize(
    ("turn_completed", "body_present", "expected"),
    ((0, 1, 0), (1, 0, 0), (0, 0, 0)),
)
def test_not_recovered_without_closure_or_body(
    turn_completed: int, body_present: int, expected: int
) -> None:
    result = ErrorRecoveryRateEvaluator().evaluate(
        _normalized(turn_completed=turn_completed, body_present=body_present)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert json.loads(cast(str, result.value))[0]["error_recovered"] == expected


def test_not_computable_reasons_are_preserved() -> None:
    evaluator = ErrorRecoveryRateEvaluator()
    cases = (
        _normalized(had_error=0, normalization_error="no_error_events"),
        _normalized(
            mode="chat",
            recovery_semantics=None,
            had_error=None,
            turn_completed=None,
            body_present=None,
            body_channel=None,
            normalization_error="no_error_events_observable",
        ),
        _normalized(had_error=None, normalization_error="missing_turn_boundary"),
    )
    for trace, expected_reason in zip(
        cases, ("no_error_events", "no_error_events_observable", "missing_turn_boundary")
    ):
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.value is None
        assert result.metadata["reason"] == expected_reason


@pytest.mark.parametrize(
    "reason",
    (
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    ),
)
def test_structural_reasons_map_to_error_status(reason: str) -> None:
    result = ErrorRecoveryRateEvaluator().evaluate(
        _normalized(had_error=None, normalization_error=reason)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == reason


def test_unknown_reason_and_missing_reason_are_invalid_trace() -> None:
    evaluator = ErrorRecoveryRateEvaluator()
    unknown = evaluator.evaluate(_normalized(normalization_error="something_else"))
    # had_error=0 却没有原因码:规范化与评测器契约脱节。
    inconsistent = evaluator.evaluate(_normalized(had_error=0))

    assert unknown.status is EvaluationStatus.ERROR
    assert unknown.metadata["reason"] == "invalid_normalized_trace"
    assert inconsistent.status is EvaluationStatus.ERROR


def test_semantics_declaration_is_validated() -> None:
    evaluator = ErrorRecoveryRateEvaluator()
    wrong_semantics = evaluator.evaluate(_normalized(recovery_semantics="payload_proxy_v1"))
    missing_semantics = evaluator.evaluate(_normalized(recovery_semantics=None))

    assert wrong_semantics.status is EvaluationStatus.ERROR
    assert wrong_semantics.metadata["reason"] == "invalid_normalized_trace"
    assert missing_semantics.status is EvaluationStatus.ERROR


def test_had_error_conservation_is_validated() -> None:
    evaluator = ErrorRecoveryRateEvaluator()
    diagnostics = {
        "llm_error_event_count": 0,
        "tool_error_call_count": 0,
        "retry_recovered_error_count": 0,
        "retry_unpaired_error_count": 0,
        "llm_loop_missing_error_count": 0,
    }
    # had_error=1 但两层错误计数均为 0:守恒关系被破坏。
    result = evaluator.evaluate(_normalized(diagnostics=diagnostics))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_invalid_flags_and_counts_are_rejected() -> None:
    evaluator = ErrorRecoveryRateEvaluator()
    bad_diagnostics = cast(
        dict[str, int],
        {
            "llm_error_event_count": -1,
            "tool_error_call_count": 0,
            "retry_recovered_error_count": 0,
            "retry_unpaired_error_count": 0,
            "llm_loop_missing_error_count": 0,
        },
    )
    variants = (
        _normalized(had_error=2),
        _normalized(turn_completed=None),
        _normalized(body_present="yes"),
        _normalized(diagnostics=bad_diagnostics),
        _normalized(mode="hybrid"),
    )
    for trace in variants[:-1]:
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
    # 未知模式在结构合法时按既有约定为 NOT_COMPUTABLE。
    hybrid = evaluator.evaluate(variants[-1])
    assert hybrid.status is EvaluationStatus.NOT_COMPUTABLE
    assert hybrid.metadata["reason"] == "unsupported_trace_mode"


def test_research_end_to_end_with_collector() -> None:
    events = [
        {
            "type": "turn.start",
            "sessionID": "ses-1",
            "userMessageID": "msg-1",
            "ts": "2026-09-11T01:30:00.000Z",
        },
        {
            "type": "llm.error",
            "loop": 1,
            "error": {"value": {"error": "timeout awaiting response headers"}},
        },
        {"type": "llm.response", "loop": 1, "result": {"finishReason": "tool-calls"}},
        {"type": "message.updated", "info": {"role": "assistant", "id": "a-1"}},
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "a-1", "text": "最终交付正文。"},
        },
        {
            "type": "turn.end",
            "sessionID": "ses-1",
            "userMessageID": "msg-1",
            "durationMs": 7_000,
        },
    ]

    result = ErrorRecoveryRateEvaluator().evaluate(normalize_research_error_recovery(events))

    assert result.status is EvaluationStatus.COMPUTED
    parsed = json.loads(cast(str, result.value))[0]
    assert parsed == {
        "error_recovered": 1,
        "had_error": 1,
        "llm_error_count": 1,
        "tool_error_call_count": 0,
        "retry_recovered_error_count": 1,
    }
    assert result.metadata["trace_id"] == "ses-1:msg-1"


def test_input_is_not_mutated() -> None:
    trace = _normalized()
    original = copy.deepcopy(trace)

    ErrorRecoveryRateEvaluator().evaluate(trace)

    assert trace == original
