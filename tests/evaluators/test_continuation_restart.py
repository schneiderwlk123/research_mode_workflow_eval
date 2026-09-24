"""续跑重启次数评测器行为测试。

覆盖: 研究模式双因素紧凑 JSON 主值(含未闭环与合法零值)、聊天/缺边界不可
计算、冲突与非法结构错误、规范化输入严格校验(两因素互斥、计数自洽、聊天
无 error 违约、类型)、元数据语义标签与诊断透传、敏感信息隔离。
"""

from collectors.continuation_restart import (
    normalize_chat_continuation_restart,
    normalize_research_continuation_restart,
)
from evaluators.base import EvaluationStatus
from evaluators.continuation_restart import ContinuationRestartCountEvaluator


def _normalized(
    *,
    mode: str = "research",
    trace_id: str | None = "ses-1:msg-1",
    restart_count: int | None = 1,
    turn_completed: int | None = 1,
    turn_start_count: int | None = None,
    duplicate: int = 0,
    invalid_ts: int = 0,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "restart_count": restart_count,
        "turn_completed": turn_completed,
        "diagnostics": {
            "turn_start_count": (restart_count + 1)
            if turn_start_count is None and restart_count is not None
            else (turn_start_count or 0),
            "duplicate_turn_start_count": duplicate,
            "invalid_ts_turn_start_count": invalid_ts,
        },
        "normalization_error": error,
    }


def test_research_two_factor_json_is_computed_with_semantics() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=2, turn_completed=0)
    )

    assert result.metric_name == "continuation_restart_count"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == '[{"continuation_restart_count":2,"turn_completed":0}]'
    assert result.metadata["restart_semantics"] == "turn_start_event"
    assert result.metadata["completion_semantics"] == "turn_end_event"
    assert result.metadata["mode"] == "research"
    assert result.metadata["turn_start_count"] == 3


def test_zero_restart_closed_turn_is_computed_not_not_computable() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=0, turn_completed=1)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == '[{"continuation_restart_count":0,"turn_completed":1}]'


def test_collector_research_output_flows_through() -> None:
    events = [
        {"type": "turn.start", "sessionID": "s", "userMessageID": "m", "ts": 1757601000000},
        {"type": "turn.start", "sessionID": "s", "userMessageID": "m", "ts": 1757602000000},
    ]
    result = ContinuationRestartCountEvaluator().evaluate(
        normalize_research_continuation_restart(events)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == '[{"continuation_restart_count":1,"turn_completed":0}]'


def test_chat_is_not_computable_with_fixed_reason() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        normalize_chat_continuation_restart({"canvas": {"meta": {"traceId": "t-1"}}})
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_continuation_mechanism"
    assert result.metadata["restart_semantics"] == "no_continuation_mechanism"
    assert result.metadata["trace_id"] == "t-1"


def test_missing_turn_boundary_is_not_computable() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(
            restart_count=None,
            turn_completed=None,
            turn_start_count=0,
            error="missing_turn_boundary",
        )
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "missing_turn_boundary"


def test_conflict_and_invalid_structure_are_errors() -> None:
    conflicting = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=None, turn_completed=None, error="conflicting_turn_boundary")
    )
    invalid = ContinuationRestartCountEvaluator().evaluate(
        _normalized(
            restart_count=None,
            turn_completed=None,
            error="invalid_research_trace_structure",
        )
    )

    assert conflicting.status is EvaluationStatus.ERROR
    assert conflicting.metadata["reason"] == "conflicting_turn_boundary"
    assert invalid.status is EvaluationStatus.ERROR


def test_unknown_reason_is_invalid_normalized_trace() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=None, turn_completed=None, error="some_unknown_reason")
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata == {"reason": "invalid_normalized_trace"}


def test_inconsistent_count_relation_is_rejected() -> None:
    # restart_count 必须等于 turn_start_count − 1,否则规范化契约被破坏。
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=3, turn_start_count=2)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_factors_and_error_are_mutually_exclusive() -> None:
    # 两因素必须同时出现或同时缺席;有 error 不得携带任一因素。
    partial = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=1, turn_completed=None)
    )
    with_value = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=1, turn_completed=1, error="missing_turn_boundary")
    )
    without_value = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=None, turn_completed=None)
    )

    for result in (partial, with_value, without_value):
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"


def test_chat_without_error_violates_contract() -> None:
    # 聊天模式契约上恒携带 no_continuation_mechanism,无 error 的 chat 结果被拒绝。
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(mode="chat", trace_id="t-1", restart_count=0, turn_completed=1)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_invalid_types_and_negative_counts_are_rejected() -> None:
    bad_inputs: list[object] = [
        "not-a-mapping",
        _normalized(mode="unknown"),
        {**_normalized(), "restart_count": True},
        {**_normalized(), "restart_count": -1},
        {**_normalized(), "restart_count": 1.5},
        {**_normalized(), "turn_completed": True},
        {**_normalized(), "turn_completed": 2},
        {**_normalized(), "turn_completed": -1},
        {**_normalized(), "trace_id": 123},
        {**_normalized(), "diagnostics": "not-a-mapping"},
        _normalized(duplicate=-1),
        _normalized(invalid_ts=-1),
        {**_normalized(), "diagnostics": {"turn_start_count": 2, "duplicate_turn_start_count": 0}},
    ]
    evaluator = ContinuationRestartCountEvaluator()

    for bad in bad_inputs:
        result = evaluator.evaluate(bad)  # type: ignore[arg-type]
        assert result.status is EvaluationStatus.ERROR, bad
        assert result.metadata["reason"] == "invalid_normalized_trace", bad


def test_diagnostics_flow_into_metadata_without_bodies() -> None:
    result = ContinuationRestartCountEvaluator().evaluate(
        _normalized(restart_count=2, turn_completed=1, duplicate=1, invalid_ts=1)
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["duplicate_turn_start_count"] == 1
    assert result.metadata["invalid_ts_turn_start_count"] == 1
    assert "SENSITIVE" not in repr(result.metadata)
