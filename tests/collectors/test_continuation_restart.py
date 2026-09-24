"""续跑重启次数双模式规范化行为测试。

覆盖: 聊天固定不可计算(结构合法与非法均同)、研究单段零重启、续跑多
turn.start 计数、同 ts 重复投影去重、ts 缺失/非法保守计为独立事件、
跨 ID 冲突隔离、无 turn.start 不可计算、非法序列、闭环标记组合(7.1 复用、
turn.end 冲突连带隔离)、敏感正文隔离与输入不变。
"""

from copy import deepcopy

from collectors.continuation_restart import (
    normalize_chat_continuation_restart,
    normalize_research_continuation_restart,
)


def test_chat_is_always_not_computable() -> None:
    # 聊天 canvas 无续跑/重启机制: 结构合法与否都返回同一稳定原因,不解析正文。
    trace: dict[str, object] = {
        "canvas": {"meta": {"traceId": "trace-1"}, "answer": {"answer": "SENSITIVE"}}
    }
    original = deepcopy(trace)

    normalized = normalize_chat_continuation_restart(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "restart_count": None,
        "turn_completed": None,
        "diagnostics": {
            "turn_start_count": 0,
            "duplicate_turn_start_count": 0,
            "invalid_ts_turn_start_count": 0,
        },
        "normalization_error": "no_continuation_mechanism",
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_invalid_structure_keeps_same_reason() -> None:
    for trace in ({}, {"traceId": "legacy"}, "not-a-mapping", None):
        normalized = normalize_chat_continuation_restart(trace)
        assert normalized["mode"] == "chat"
        assert normalized["restart_count"] is None
        assert normalized["turn_completed"] is None
        assert normalized["normalization_error"] == "no_continuation_mechanism"


def _turn_start(
    ts: object = "2026-09-11T14:00:20.662Z",
    user_message_id: str = "msg-1",
    session_id: str = "ses-1",
) -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "ts": ts,
    }


def _turn_end(duration_ms: object = 1_000) -> dict[str, object]:
    return {
        "type": "turn.end",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "durationMs": duration_ms,
        "ts": "2026-09-11T14:18:16.812Z",
    }


def test_research_single_turn_start_is_zero_without_mutation() -> None:
    events = [
        _turn_start(),
        {"type": "message.part.updated", "part": {"type": "text", "text": "SENSITIVE"}},
        _turn_end(),
    ]
    original = deepcopy(events)

    normalized = normalize_research_continuation_restart(events)

    assert normalized == {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "restart_count": 0,
        "turn_completed": 1,
        "diagnostics": {
            "turn_start_count": 1,
            "duplicate_turn_start_count": 0,
            "invalid_ts_turn_start_count": 0,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert events == original


def test_research_continuation_restarts_are_counted() -> None:
    # 续跑段自带同 userMessageID 的 turn.start(不同 ts),重启次数 = 段数 − 1。
    events = [
        _turn_start(ts="2026-09-11T14:00:20.662Z"),
        _turn_start(ts="2026-09-11T14:30:00.000Z"),
        _turn_start(ts=1757601000000),
        _turn_end(),
    ]

    normalized = normalize_research_continuation_restart(events)

    assert normalized["restart_count"] == 2
    assert normalized["turn_completed"] == 1
    assert normalized["diagnostics"]["turn_start_count"] == 3
    assert normalized["normalization_error"] is None


def test_research_unclosed_turn_keeps_restart_count_with_zero_flag() -> None:
    # 未闭环回合两因素照常输出: 重启计数不因未闭环而丢失(7.3 需求口径)。
    events = [
        _turn_start(ts="2026-09-11T14:00:20.662Z"),
        _turn_start(ts="2026-09-11T14:30:00.000Z"),
        {"type": "message.part.updated", "part": {"type": "step-start"}},
    ]

    normalized = normalize_research_continuation_restart(events)

    assert normalized["restart_count"] == 1
    assert normalized["turn_completed"] == 0
    assert normalized["normalization_error"] is None


def test_research_duplicate_projection_is_deduped_by_ts() -> None:
    # 同一事件重复投影(ts 相同)只计一次;不同 ts 才是真重启。
    events = [
        _turn_start(ts="2026-09-11T14:00:20.662Z"),
        _turn_start(ts="2026-09-11T14:00:20.662Z"),
        _turn_start(ts="2026-09-11T14:20:00.000Z"),
        _turn_start(ts="2026-09-11T14:20:00.000Z"),
    ]

    normalized = normalize_research_continuation_restart(events)

    assert normalized["restart_count"] == 1
    assert normalized["diagnostics"]["turn_start_count"] == 2
    assert normalized["diagnostics"]["duplicate_turn_start_count"] == 2


def test_research_invalid_ts_counts_conservatively() -> None:
    # ts 缺失/非法的 turn.start 无法证明是重复投影: 保守计为独立事件并诊断。
    events = [
        _turn_start(ts="2026-09-11T14:00:20.662Z"),
        _turn_start(ts=None),
        _turn_start(ts="abc"),
        _turn_start(ts=True),
    ]

    normalized = normalize_research_continuation_restart(events)

    assert normalized["restart_count"] == 3
    assert normalized["diagnostics"]["turn_start_count"] == 4
    assert normalized["diagnostics"]["invalid_ts_turn_start_count"] == 3
    assert normalized["normalization_error"] is None


def test_research_conflicting_turn_start_ids_are_isolated() -> None:
    normalized = normalize_research_continuation_restart(
        [_turn_start(), _turn_start(user_message_id="msg-2")]
    )

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
    assert normalized["restart_count"] is None
    assert normalized["turn_completed"] is None
    assert normalized["trace_id"] == "ses-1:msg-1"


def test_research_conflicting_session_ids_are_isolated() -> None:
    normalized = normalize_research_continuation_restart(
        [_turn_start(), _turn_start(session_id="ses-2")]
    )

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
    assert normalized["restart_count"] is None


def test_research_conflicting_turn_end_durations_isolate_whole_trace() -> None:
    # 闭环边界证据冲突(7.1 口径)连带隔离整条: 两因素共享回合边界证据。
    normalized = normalize_research_continuation_restart(
        [_turn_start(), _turn_end(duration_ms=1_000), _turn_end(duration_ms=999)]
    )

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
    assert normalized["restart_count"] is None
    assert normalized["turn_completed"] is None


def test_research_missing_turn_start_is_not_computable() -> None:
    normalized = normalize_research_continuation_restart(
        [{"type": "llm.response"}, {"type": "turn.end", "sessionID": "s", "userMessageID": "m"}]
    )

    assert normalized["restart_count"] is None
    assert normalized["turn_completed"] is None
    assert normalized["normalization_error"] == "missing_turn_boundary"
    assert normalized["diagnostics"]["turn_start_count"] == 0


def test_research_invalid_sequence_is_explicit_error() -> None:
    normalized = normalize_research_continuation_restart("not-a-sequence")

    assert normalized["normalization_error"] == "invalid_research_trace_structure"
    assert normalized["restart_count"] is None


def test_research_non_mapping_events_are_skipped() -> None:
    normalized = normalize_research_continuation_restart(
        [None, "junk", _turn_start(), 42, _turn_start(ts="2026-09-11T15:00:00.000Z")]
    )

    assert normalized["restart_count"] == 1
    assert normalized["turn_completed"] == 0
    assert normalized["normalization_error"] is None
