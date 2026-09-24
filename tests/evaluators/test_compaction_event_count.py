"""上下文压缩事件数评测器行为测试。

覆盖: 研究模式非负整数主值与合法零值、聊天/结构非法/冲突错误、规范化输入
严格校验(互斥、类型、聊天无 error 违约)、元数据语义标签与诊断透传、
collector 输出直通、敏感信息隔离。
"""

from collectors.compaction_event_count import (
    normalize_chat_compaction_events,
    normalize_research_compaction_events,
)
from evaluators.base import EvaluationStatus
from evaluators.compaction_event_count import CompactionEventCountEvaluator


def _normalized(
    *,
    mode: str = "research",
    trace_id: str | None = "ses-1:msg-1",
    count: int | None = 2,
    duplicate: int = 0,
    manual: int = 0,
    auto_invalid: int = 0,
    missing_id: int = 0,
    message_projection: int = 0,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "compaction_event_count": count,
        "diagnostics": {
            "duplicate_compaction_projection_count": duplicate,
            "manual_compaction_count": manual,
            "auto_flag_invalid_count": auto_invalid,
            "missing_compaction_part_id_count": missing_id,
            "message_compaction_projection_count": message_projection,
        },
        "normalization_error": error,
    }


def test_research_count_is_computed_with_semantics() -> None:
    result = CompactionEventCountEvaluator().evaluate(_normalized(count=4))

    assert result.metric_name == "compaction_event_count"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 4
    assert result.metadata["compaction_semantics"] == "compaction_part_event"
    assert result.metadata["mode"] == "research"
    assert result.metadata["trace_id"] == "ses-1:msg-1"


def test_zero_compaction_is_computed_not_not_computable() -> None:
    result = CompactionEventCountEvaluator().evaluate(_normalized(count=0))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0


def test_collector_research_output_flows_through() -> None:
    events = [
        {"type": "turn.start", "sessionID": "s", "userMessageID": "m", "ts": 1757601000000},
        {
            "type": "message.part.updated",
            "part": {"id": "prt_1", "type": "compaction", "auto": True},
        },
    ]
    result = CompactionEventCountEvaluator().evaluate(normalize_research_compaction_events(events))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1


def test_chat_is_not_computable_with_fixed_reason() -> None:
    result = CompactionEventCountEvaluator().evaluate(
        normalize_chat_compaction_events({"canvas": {"meta": {"traceId": "t-1"}}})
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_compaction_events"
    assert result.metadata["compaction_semantics"] == "no_compaction_channel"
    assert result.metadata["trace_id"] == "t-1"


def test_structure_and_conflict_reasons_are_errors() -> None:
    evaluator = CompactionEventCountEvaluator()
    chat_invalid = evaluator.evaluate(
        _normalized(mode="chat", trace_id=None, count=None, error="invalid_chat_trace_structure")
    )
    research_invalid = evaluator.evaluate(
        _normalized(count=None, error="invalid_research_trace_structure")
    )
    conflicting = evaluator.evaluate(
        _normalized(count=None, error="conflicting_compaction_definition")
    )

    for result in (chat_invalid, research_invalid, conflicting):
        assert result.status is EvaluationStatus.ERROR
    assert conflicting.metadata["reason"] == "conflicting_compaction_definition"


def test_unknown_reason_is_invalid_normalized_trace() -> None:
    result = CompactionEventCountEvaluator().evaluate(
        _normalized(count=None, error="some_unknown_reason")
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata == {"reason": "invalid_normalized_trace"}


def test_value_and_error_are_mutually_exclusive() -> None:
    with_value = CompactionEventCountEvaluator().evaluate(
        _normalized(count=1, error="invalid_research_trace_structure")
    )
    without_value = CompactionEventCountEvaluator().evaluate(_normalized(count=None))

    for result in (with_value, without_value):
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"


def test_chat_without_error_violates_contract() -> None:
    # 聊天模式契约上恒携带 no_compaction_events,无 error 的 chat 结果被拒绝。
    result = CompactionEventCountEvaluator().evaluate(
        _normalized(mode="chat", trace_id="t-1", count=0)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_invalid_types_and_negative_counts_are_rejected() -> None:
    bad_inputs: list[object] = [
        "not-a-mapping",
        _normalized(mode="unknown"),
        {**_normalized(), "compaction_event_count": True},
        {**_normalized(), "compaction_event_count": -1},
        {**_normalized(), "compaction_event_count": 1.5},
        {**_normalized(), "trace_id": 123},
        {**_normalized(), "diagnostics": "not-a-mapping"},
        _normalized(duplicate=-1),
        _normalized(manual=-1),
        _normalized(auto_invalid=-1),
        _normalized(missing_id=-1),
        _normalized(message_projection=-1),
        {
            **_normalized(),
            "diagnostics": {"duplicate_compaction_projection_count": 0},
        },
    ]
    evaluator = CompactionEventCountEvaluator()

    for bad in bad_inputs:
        result = evaluator.evaluate(bad)  # type: ignore[arg-type]
        assert result.status is EvaluationStatus.ERROR, bad
        assert result.metadata["reason"] == "invalid_normalized_trace", bad


def test_diagnostics_flow_into_metadata_without_bodies() -> None:
    result = CompactionEventCountEvaluator().evaluate(
        _normalized(
            count=2, duplicate=1, manual=1, auto_invalid=1, missing_id=1, message_projection=2
        )
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["duplicate_compaction_projection_count"] == 1
    assert result.metadata["manual_compaction_count"] == 1
    assert result.metadata["auto_flag_invalid_count"] == 1
    assert result.metadata["missing_compaction_part_id_count"] == 1
    assert result.metadata["message_compaction_projection_count"] == 2
    assert "SENSITIVE" not in repr(result.metadata)
