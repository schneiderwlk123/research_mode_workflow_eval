"""上下文压缩事件数双模式规范化行为测试。

覆盖: 聊天固定不可计算(结构合法与非法区分)、研究零压缩合法值、auto=true
唯一 part 计数、同 part.id 重复投影去重、auto=false 手动压缩诊断分流、
auto 缺失/非布尔不猜测、part.id 缺失保守计数、同 id 冲突 auto 隔离、
message.updated 压缩投影仅诊断、非序列/非映射事件、敏感正文隔离与输入不变。
"""

from copy import deepcopy

from collectors.compaction_event_count import (
    normalize_chat_compaction_events,
    normalize_research_compaction_events,
)


def _chat_trace() -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "answer": {"answer": "SENSITIVE"},
        }
    }


def test_chat_is_not_computable_with_channel_reason() -> None:
    # 聊天 canvas 无压缩事件通道;通道不存在不等于无压缩,禁止记 0。
    trace = _chat_trace()
    original = deepcopy(trace)

    normalized = normalize_chat_compaction_events(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "compaction_event_count": None,
        "diagnostics": {
            "duplicate_compaction_projection_count": 0,
            "manual_compaction_count": 0,
            "auto_flag_invalid_count": 0,
            "missing_compaction_part_id_count": 0,
            "message_compaction_projection_count": 0,
        },
        "normalization_error": "no_compaction_events",
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_invalid_structure_is_explicit_error() -> None:
    for trace in ({}, {"traceId": "legacy"}, {"canvas": {"meta": "not-a-mapping"}}):
        normalized = normalize_chat_compaction_events(trace)
        assert normalized["mode"] == "chat"
        assert normalized["compaction_event_count"] is None
        assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def _turn_start(user_message_id: str = "msg-1", session_id: str = "ses-1") -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "ts": "2026-09-11T14:00:20.662Z",
    }


def _compaction_part(
    part_id: object = "prt_1",
    auto: object = True,
    session_id: str = "ses-1",
    message_id: str = "msg-step-1",
) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "sessionID": session_id,
        "part": {
            "id": part_id,
            "messageID": message_id,
            "sessionID": session_id,
            "type": "compaction",
            "auto": auto,
        },
    }


def test_research_zero_compaction_is_computed_zero() -> None:
    events = [
        _turn_start(),
        {"type": "message.part.updated", "part": {"type": "text", "text": "SENSITIVE"}},
        {"type": "turn.end", "sessionID": "ses-1", "userMessageID": "msg-1", "durationMs": 1},
    ]
    original = deepcopy(events)

    normalized = normalize_research_compaction_events(events)

    assert normalized == {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "compaction_event_count": 0,
        "diagnostics": {
            "duplicate_compaction_projection_count": 0,
            "manual_compaction_count": 0,
            "auto_flag_invalid_count": 0,
            "missing_compaction_part_id_count": 0,
            "message_compaction_projection_count": 0,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert events == original


def test_research_unique_auto_parts_are_counted() -> None:
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1"),
        _compaction_part(part_id="prt_2"),
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 2
    assert normalized["normalization_error"] is None


def test_research_duplicate_projection_is_deduped_by_part_id() -> None:
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1"),
        _compaction_part(part_id="prt_1"),
        _compaction_part(part_id="prt_2"),
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 2
    assert normalized["diagnostics"]["duplicate_compaction_projection_count"] == 1


def test_research_manual_compaction_excluded_with_diagnostic() -> None:
    # auto == false 是手动/显式压缩: 不进主值,单独诊断(权威口径 7.6)。
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1", auto=False),
        _compaction_part(part_id="prt_2", auto=True),
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 1
    assert normalized["diagnostics"]["manual_compaction_count"] == 1


def test_research_invalid_auto_flag_is_not_guessed() -> None:
    # auto 缺失/非布尔无法证明是自动压缩: 不进主值也不进手动计数,单独诊断。
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1", auto=None),
        _compaction_part(part_id="prt_2", auto="true"),
        {"type": "message.part.updated", "part": {"id": "prt_3", "type": "compaction"}},
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 0
    assert normalized["diagnostics"]["auto_flag_invalid_count"] == 3
    assert normalized["diagnostics"]["manual_compaction_count"] == 0


def test_research_missing_part_id_counts_conservatively() -> None:
    # part.id 缺失无法去重: 保守计为独立事件(宁多计不漏计),进诊断。
    events = [
        _turn_start(),
        _compaction_part(part_id=None),
        _compaction_part(part_id=""),
        _compaction_part(part_id="prt_1"),
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 3
    assert normalized["diagnostics"]["missing_compaction_part_id_count"] == 2


def test_research_conflicting_auto_for_same_part_id_is_isolated() -> None:
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1", auto=True),
        _compaction_part(part_id="prt_1", auto=False),
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["normalization_error"] == "conflicting_compaction_definition"
    assert normalized["compaction_event_count"] is None


def test_research_message_channel_projection_is_diagnostic_only() -> None:
    # message.updated info.mode == "compaction" 是摘要消息跨通道投影: 只诊断不计数。
    events = [
        _turn_start(),
        _compaction_part(part_id="prt_1"),
        {
            "type": "message.updated",
            "info": {"role": "assistant", "mode": "compaction", "agent": "compaction"},
        },
    ]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 1
    assert normalized["diagnostics"]["message_compaction_projection_count"] == 1


def test_research_without_turn_start_still_counts_with_fallback_trace_id() -> None:
    # 与 7.4 同口径: 信任单回合契约,不重复校验边界;trace_id 尽力回退到 sessionID。
    events = [_compaction_part(part_id="prt_1", session_id="ses-9")]

    normalized = normalize_research_compaction_events(events)

    assert normalized["compaction_event_count"] == 1
    assert normalized["trace_id"] == "ses-9"
    assert normalized["normalization_error"] is None


def test_research_invalid_sequence_is_explicit_error() -> None:
    normalized = normalize_research_compaction_events("not-a-sequence")

    assert normalized["normalization_error"] == "invalid_research_trace_structure"
    assert normalized["compaction_event_count"] is None


def test_research_non_mapping_and_malformed_events_are_skipped() -> None:
    events = [
        None,
        "junk",
        42,
        _turn_start(),
        {"type": "message.part.updated"},
        {"type": "message.part.updated", "part": "not-a-mapping"},
        {"type": "message.part.updated", "part": {"type": "compaction", "auto": True}},
    ]

    normalized = normalize_research_compaction_events(events)

    # 最后一条无 part.id: 保守计 1 并进缺失 id 诊断;其余非法事件跳过。
    assert normalized["compaction_event_count"] == 1
    assert normalized["diagnostics"]["missing_compaction_part_id_count"] == 1
    assert normalized["normalization_error"] is None
