"""高危回合画像双模式组合规范化行为测试(稳定性维度 7.11)。

覆盖: 聊天依赖透传不可计算与结构非法区分、研究无 tool.debug 时 step 数再高
也不可计算(重试条件不可评估)、四组件组合(step 计数/7.8 重试率/7.6 压缩/
7.1 闭环)、step-start 按 messageID 去重与缺失保守计数、组件结构错误透传
隔离、缺回合边界时闭环特征为 None 不阻断主值、profile_version 声明、
敏感正文隔离与输入不变。
"""

from copy import deepcopy

from collectors.high_risk_turn_profile import (
    HIGH_RISK_PROFILE_VERSION,
    normalize_chat_high_risk_turn_profile,
    normalize_research_high_risk_turn_profile,
)


def _chat_trace() -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "answer": {"answer": "SENSITIVE"},
        }
    }


def _turn_start(user_message_id: str = "msg-1", session_id: str = "ses-1") -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "ts": "2026-09-11T14:00:20.662Z",
    }


def _turn_end(duration_ms: object = 1000) -> dict[str, object]:
    return {
        "type": "turn.end",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "durationMs": duration_ms,
        "ts": "2026-09-11T14:20:20.662Z",
    }


def _step_start(message_id: object) -> dict[str, object]:
    part: dict[str, object] = {
        "id": f"prt_{message_id}",
        "type": "step-start",
        "sessionID": "ses-1",
    }
    if message_id is not None:
        part["messageID"] = message_id
    return {"type": "message.part.updated", "sessionID": "ses-1", "part": part}


def _compaction(part_id: str = "prt_c1", auto: object = True) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "sessionID": "ses-1",
        "part": {
            "id": part_id,
            "messageID": "m-c1",
            "sessionID": "ses-1",
            "type": "compaction",
            "auto": auto,
        },
    }


def _tool_part(call_id: str = "call-1") -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "sessionID": "ses-1",
        "part": {
            "type": "tool",
            "tool": "document_search",
            "callID": call_id,
            "state": {"status": "completed", "output": "SENSITIVE-OUTPUT"},
        },
    }


def _tool_debug(
    request_ids: list[str],
    call_id: str = "call-1",
) -> dict[str, object]:
    return {
        "type": "tool.debug",
        "callID": call_id,
        "tool": "document_search",
        "sessionID": "ses-1",
        "debug": {
            "apiCalls": [
                {
                    "endpoint": "/json/inner/SENSITIVE-PATH",
                    "httpStatus": 200,
                    "request": {"requestId": rid},
                    "response": {"code": 200},
                }
                for rid in request_ids
            ]
        },
    }


def test_chat_dependency_passes_through_not_computable() -> None:
    # 聊天侧 7.8 依赖不可计算 → 画像透传同一原因;禁止以其他特征替代。
    trace = _chat_trace()
    original = deepcopy(trace)

    normalized = normalize_chat_high_risk_turn_profile(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "profile_version": None,
        "step_count": None,
        "api_retry_rate": None,
        "api_retry_count": None,
        "api_call_count": None,
        "compaction_event_count": None,
        "turn_completed": None,
        "diagnostics": {
            "duplicate_step_start_projection_count": 0,
            "missing_step_message_id_count": 0,
        },
        "normalization_error": "no_tool_debug_events",
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_invalid_structure_is_explicit_error() -> None:
    for trace in ({}, {"canvas": {"meta": "not-a-mapping"}}):
        normalized = normalize_chat_high_risk_turn_profile(trace)
        assert normalized["mode"] == "chat"
        assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_invalid_structure_is_explicit_error() -> None:
    for events in ("not-a-sequence", None, {"type": "turn.start"}):
        normalized = normalize_research_high_risk_turn_profile(events)  # type: ignore[arg-type]
        assert normalized["mode"] == "research"
        assert normalized["normalization_error"] == "invalid_research_trace_structure"


def test_research_without_tool_debug_is_not_computable_regardless_of_steps() -> None:
    # step 数再高,重试条件不可评估就不可计算(no_tool_debug_events 不代表无重试)。
    events = [_turn_start(), *[_step_start(f"step-{i}") for i in range(60)], _tool_part()]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] == "no_tool_debug_events"
    assert normalized["step_count"] is None
    assert normalized["profile_version"] == HIGH_RISK_PROFILE_VERSION


def test_research_empty_api_calls_passes_through() -> None:
    events = [_turn_start(), _tool_part(), _tool_debug([])]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] == "no_observable_api_calls"


def test_research_full_composition() -> None:
    # 31 个唯一 step(含 1 次重复投影)+ 重试率 1/2=0.5 + 压缩 1 次 + 闭环。
    events = [
        _turn_start(),
        *[_step_start(f"step-{i}") for i in range(31)],
        _step_start("step-0"),
        _tool_part(),
        _tool_debug(["req-aaaa-1", "req-aaaa-2"]),
        _compaction(),
        _turn_end(),
    ]
    original = deepcopy(events)

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized == {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "profile_version": HIGH_RISK_PROFILE_VERSION,
        "step_count": 31,
        "api_retry_rate": 0.5,
        "api_retry_count": 1,
        "api_call_count": 2,
        "compaction_event_count": 1,
        "turn_completed": 1,
        "diagnostics": {
            "duplicate_step_start_projection_count": 1,
            "missing_step_message_id_count": 0,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert events == original


def test_step_without_message_id_is_counted_conservatively() -> None:
    # 缺 messageID 无法去重:保守计为独立 step(宁多计不漏计,与 7.3/7.6 同方向)。
    events = [
        _turn_start(),
        _step_start("step-1"),
        _step_start(None),
        _step_start(None),
        _tool_part(),
        _tool_debug(["req-aaaa-1"]),
    ]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["step_count"] == 3
    assert normalized["diagnostics"]["missing_step_message_id_count"] == 2


def test_conflicting_turn_boundary_is_isolated() -> None:
    # 不同 ID 的 turn.start 属入口切分违约:边界证据损坏,整条隔离。
    events = [
        _turn_start("msg-1"),
        _turn_start("msg-2"),
        _tool_part(),
        _tool_debug(["req-aaaa-1"]),
    ]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
    assert normalized["step_count"] is None


def test_missing_turn_boundary_keeps_profile_computable() -> None:
    # 闭环只是特征项不是画像条件:缺回合边界时 turn_completed 为 None,主值照算。
    events = [
        {
            "type": "message.part.updated",
            "sessionID": "ses-9",
            "part": {"type": "step-start", "messageID": "s1"},
        },
        {
            "type": "tool.debug",
            "callID": "call-1",
            "sessionID": "ses-9",
            "debug": {
                "apiCalls": [{"request": {"requestId": "req-aaaa-1"}, "response": {"code": 200}}]
            },
        },
        {
            "type": "message.part.updated",
            "sessionID": "ses-9",
            "part": {"type": "tool", "callID": "call-1", "state": {"status": "completed"}},
        },
    ]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] is None
    assert normalized["turn_completed"] is None
    assert normalized["step_count"] == 1
    assert normalized["api_retry_rate"] == 0.0
    assert normalized["trace_id"] == "ses-9"


def test_conflicting_compaction_definition_is_isolated() -> None:
    # 同一 part.id 出现不同 auto 分类属证据冲突:透传隔离(7.6 契约)。
    events = [
        _turn_start(),
        _tool_part(),
        _tool_debug(["req-aaaa-1"]),
        _compaction(part_id="prt_c1", auto=True),
        _compaction(part_id="prt_c1", auto=False),
    ]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] == "conflicting_compaction_definition"
    assert normalized["step_count"] is None


def test_unclosed_turn_is_computable_with_zero_completion() -> None:
    # 未闭环回合同样输出画像(死亡回合正是筛查对象),turn_completed=0。
    events = [
        _turn_start(),
        _step_start("step-1"),
        _tool_part(),
        _tool_debug(["req-aaaa-1", "req-aaaa-2"]),
    ]

    normalized = normalize_research_high_risk_turn_profile(events)

    assert normalized["normalization_error"] is None
    assert normalized["turn_completed"] == 0
    assert normalized["api_retry_rate"] == 0.5
