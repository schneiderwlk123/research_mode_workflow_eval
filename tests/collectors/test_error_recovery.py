"""错误自恢复率双模式规范化的行为测试(稳定性维度 7.5)。

覆盖:聊天固定不可计算与结构校验、研究侧四组件组合(llm.error 计数、工具 error
终态、turn.end 闭环、正文通道)、had_error/error_recovered 判定输入、loop 级重试
配对(同 loop 多错误、严格"之后"、finishReason=error 不配对、loop 缺失不可配对)、
缺回合边界、组件结构错误透传、无错误事件、敏感正文隔离与输入不变。
"""

import copy

from collectors.error_recovery import (
    RECOVERY_SEMANTICS,
    normalize_chat_error_recovery,
    normalize_research_error_recovery,
)


def test_chat_is_fixed_not_computable_and_validates_structure() -> None:
    trace = {"canvas": {"meta": {"traceId": "chat-1"}, "agents": []}}

    normalized = normalize_chat_error_recovery(trace)

    assert normalized["mode"] == "chat"
    assert normalized["normalization_error"] == "no_error_events_observable"
    assert normalized["recovery_semantics"] is None
    assert normalized["had_error"] is None
    assert normalized["turn_completed"] is None
    assert normalized["body_present"] is None
    assert normalized["diagnostics"]["llm_error_event_count"] == 0

    invalid = normalize_chat_error_recovery({"no_canvas": True})
    assert invalid["normalization_error"] == "invalid_chat_trace_structure"


def test_research_llm_error_recovered_with_same_loop_retry_pairing() -> None:
    """35e9abf0 实证形态:loop 1 两条 error 后同 loop 成功响应,回合闭环有正文。"""

    events = [
        _turn_start(),
        _llm_error(loop=1),
        _llm_error(loop=1),
        _llm_response(loop=1),
        _llm_error(loop=2),
        _llm_response(loop=2, finish="stop"),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "最终结论:毛利率50%。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["normalization_error"] is None
    assert normalized["mode"] == "research"
    assert normalized["recovery_semantics"] == RECOVERY_SEMANTICS
    assert normalized["trace_id"] == "ses-1:msg-1"
    assert normalized["had_error"] == 1
    assert normalized["turn_completed"] == 1
    assert normalized["body_present"] == 1
    assert normalized["body_channel"] == "final_text"
    assert normalized["diagnostics"]["llm_error_event_count"] == 3
    assert normalized["diagnostics"]["tool_error_call_count"] == 0
    # 三条 error 各自独立配对:loop1×2 与 loop2 均有同 loop 后继成功响应。
    assert normalized["diagnostics"]["retry_recovered_error_count"] == 3
    assert normalized["diagnostics"]["retry_unpaired_error_count"] == 0
    assert normalized["diagnostics"]["llm_loop_missing_error_count"] == 0


def test_research_unpaired_error_and_loop_missing_are_diagnosed() -> None:
    events = [
        _turn_start(),
        # loop 7 无同 loop 后继成功响应(931da0c5 error@loop 11 形态)。
        _llm_error(loop=7),
        _llm_response(loop=8),
        # loop 缺失(实勘 2/9):不可配对,只进诊断。
        _llm_error(),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "交付正文。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["had_error"] == 1
    assert normalized["diagnostics"]["llm_error_event_count"] == 2
    assert normalized["diagnostics"]["retry_recovered_error_count"] == 0
    assert normalized["diagnostics"]["retry_unpaired_error_count"] == 1
    assert normalized["diagnostics"]["llm_loop_missing_error_count"] == 1


def test_research_finish_reason_error_response_is_not_a_retry_success() -> None:
    events = [
        _turn_start(),
        _llm_error(loop=3),
        _llm_response(loop=3, finish="error"),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "正文。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["diagnostics"]["retry_recovered_error_count"] == 0
    assert normalized["diagnostics"]["retry_unpaired_error_count"] == 1


def test_research_response_before_error_does_not_pair() -> None:
    """配对要求成功响应严格出现在 error 之后(事件序位置)。"""

    events = [
        _turn_start(),
        _llm_response(loop=2, finish="stop"),
        _llm_error(loop=2),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "正文。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["diagnostics"]["retry_recovered_error_count"] == 0
    assert normalized["diagnostics"]["retry_unpaired_error_count"] == 1


def test_research_tool_error_alone_sets_had_error() -> None:
    events = [
        _turn_start(),
        _tool_event("c-1", "edit", "error", error="Found multiple matches for oldString"),
        _tool_event("c-2", "grep", "completed"),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "正文。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["normalization_error"] is None
    assert normalized["had_error"] == 1
    assert normalized["diagnostics"]["llm_error_event_count"] == 0
    assert normalized["diagnostics"]["tool_error_call_count"] == 1


def test_research_no_error_events_is_not_computable() -> None:
    events = [
        _turn_start(),
        _tool_event("c-1", "grep", "completed"),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "正文。"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["normalization_error"] == "no_error_events"
    assert normalized["had_error"] == 0
    # 闭环与正文证据仍然输出,供报告层诊断;主值由评测器判 NOT_COMPUTABLE。
    assert normalized["turn_completed"] == 1
    assert normalized["body_present"] == 1


def test_research_unclosed_turn_yields_zero_completion_evidence() -> None:
    """e7b0e1fd 形态:错误后无 turn.end,闭环证据为 0(未兜住)。"""

    events = [
        _turn_start(),
        _llm_error(loop=None),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "中途正文。"),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["normalization_error"] is None
    assert normalized["had_error"] == 1
    assert normalized["turn_completed"] == 0
    assert normalized["diagnostics"]["llm_loop_missing_error_count"] == 1


def test_research_missing_body_channel_keeps_zero_body_present() -> None:
    events = [
        _turn_start(),
        _tool_event("c-1", "grep", "error", error="socket connection was closed"),
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["had_error"] == 1
    assert normalized["turn_completed"] == 1
    assert normalized["body_present"] == 0
    assert normalized["body_channel"] == "missing"


def test_research_write_artifact_body_channel() -> None:
    events = [
        _turn_start(),
        _llm_error(loop=1),
        _llm_response(loop=1),
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "c-w",
                "tool": "write",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "report.md", "content": "# 交付报告\n毛利率50%。"},
                },
            },
        },
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    assert normalized["body_present"] == 1
    assert normalized["body_channel"] == "write_artifact"


def test_research_missing_turn_boundary_is_not_computable() -> None:
    events = [_llm_error(loop=1), _turn_end()]

    normalized = normalize_research_error_recovery(events)

    assert normalized["normalization_error"] == "missing_turn_boundary"
    assert normalized["had_error"] is None


def test_research_component_structural_error_propagates() -> None:
    events = [
        _turn_start(),
        {
            "type": "message.part.updated",
            "part": {"type": "tool", "tool": "grep", "state": {"status": "completed"}},
        },
        _turn_end(),
    ]

    normalized = normalize_research_error_recovery(events)

    # 工具 part 缺 callID:7.7 组件的结构错误原样透传,不得静默降级。
    assert normalized["normalization_error"] == "missing_call_id"
    assert normalized["had_error"] is None


def test_research_invalid_input_is_error() -> None:
    normalized = normalize_research_error_recovery("not-events")

    assert normalized["normalization_error"] == "invalid_research_trace_structure"


def test_research_sensitive_text_isolated_and_input_not_mutated() -> None:
    events = [
        _turn_start(),
        _llm_error(loop=1, text="SENSITIVE_LLM_ERROR prompt is too long"),
        _llm_response(loop=1),
        _assistant_updated("a-1"),
        _assistant_text("a-1", "SENSITIVE_ANSWER_BODY 敏感回答正文。"),
        _turn_end(),
    ]
    original = copy.deepcopy(events)

    normalized = normalize_research_error_recovery(events)

    assert "SENSITIVE_LLM_ERROR" not in repr(normalized)
    assert "SENSITIVE_ANSWER_BODY" not in repr(normalized)
    assert events == original


def _turn_start(sid: str = "ses-1", umid: str = "msg-1") -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": sid,
        "userMessageID": umid,
        "ts": "2026-09-11T01:30:00.000Z",
    }


def _turn_end(duration_ms: int = 7_000) -> dict[str, object]:
    return {
        "type": "turn.end",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "ts": "2026-09-11T01:37:00.000Z",
        "durationMs": duration_ms,
    }


def _llm_error(
    loop: int | None = None, text: str = "timeout awaiting response headers"
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "llm.error",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "ts": "2026-09-11T01:31:00.000Z",
        "error": {"value": {"error": text}},
    }
    if loop is not None:
        event["loop"] = loop
    return event


def _llm_response(loop: int, finish: str = "tool-calls") -> dict[str, object]:
    return {
        "type": "llm.response",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "loop": loop,
        "ts": "2026-09-11T01:32:00.000Z",
        "result": {"finishReason": finish},
    }


def _tool_event(
    call_id: str, tool_name: str, status: str, *, error: str | None = None
) -> dict[str, object]:
    state: dict[str, object] = {"status": status}
    if error is not None:
        state["error"] = error
    return {
        "type": "message.part.updated",
        "part": {"type": "tool", "callID": call_id, "tool": tool_name, "state": state},
    }


def _assistant_updated(message_id: str) -> dict[str, object]:
    return {"type": "message.updated", "info": {"role": "assistant", "id": message_id}}


def _assistant_text(message_id: str, text: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {"type": "text", "messageID": message_id, "text": text},
    }
