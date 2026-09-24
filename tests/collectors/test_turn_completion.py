"""回合完成率双模式规范化行为测试。

覆盖: 聊天代理口径(非空正文/固定失败模板/空白与缺失正文/结构非法)、研究
turn.end 闭环判定(含续跑多 turn.start、重复与冲突边界、耗时诊断、ID 不匹配
隔离)、敏感正文隔离与输入不可变。
"""

from copy import deepcopy

from collectors.turn_completion import (
    FAILURE_TEMPLATE_CATALOG_VERSION,
    normalize_chat_turn_completion,
    normalize_research_turn_completion,
)


def _chat_trace(answer_text: object) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "answer": {"answer": answer_text},
        }
    }


def test_chat_non_empty_answer_completes_without_mutation() -> None:
    trace = _chat_trace("# 研报摘要 SENSITIVE")
    original = deepcopy(trace)

    normalized = normalize_chat_turn_completion(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "turn_completed": 1,
        "diagnostics": {
            "failure_template_hit_count": 0,
            "missing_or_empty_answer_count": 0,
            "duplicate_turn_end_count": 0,
            "mismatched_turn_end_count": 0,
            "duration_diagnostic": None,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_failure_template_answer_is_zero() -> None:
    # 模板匹配为剥空白后的全文精确匹配(目录 v1),命中不保留模板原文。
    normalized = normalize_chat_turn_completion(_chat_trace("  模型服务暂时不可用，请稍后重试  "))

    assert normalized["turn_completed"] == 0
    assert normalized["diagnostics"]["failure_template_hit_count"] == 1
    assert "模型服务" not in repr(normalized)


def test_chat_blank_missing_and_non_string_answers_are_zero() -> None:
    blank = normalize_chat_turn_completion(_chat_trace("   \n "))
    non_string = normalize_chat_turn_completion(_chat_trace(123))
    missing_answer_key = normalize_chat_turn_completion({"canvas": {"meta": {"traceId": "t"}}})
    answer_not_mapping = normalize_chat_turn_completion(
        {"canvas": {"meta": {"traceId": "t"}, "answer": "文本"}}
    )

    for normalized in (blank, non_string, missing_answer_key, answer_not_mapping):
        assert normalized["turn_completed"] == 0
        assert normalized["diagnostics"]["missing_or_empty_answer_count"] == 1
        assert normalized["normalization_error"] is None


def test_chat_invalid_structure_is_explicit_error() -> None:
    legacy = normalize_chat_turn_completion({"traceId": "legacy", "answer": {"answer": "x"}})
    no_canvas = normalize_chat_turn_completion({})
    bad_meta = normalize_chat_turn_completion({"canvas": {"meta": "not-a-mapping"}})

    for normalized in (legacy, no_canvas, bad_meta):
        assert normalized["normalization_error"] == "invalid_chat_trace_structure"
        assert normalized["turn_completed"] is None


def _turn_start(user_message_id: str = "msg-1", session_id: str = "ses-1") -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "ts": "2026-09-11T14:00:20.662Z",
    }


def _turn_end(
    user_message_id: str = "msg-1",
    session_id: str = "ses-1",
    duration_ms: object = 1_076_153,
) -> dict[str, object]:
    return {
        "type": "turn.end",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "durationMs": duration_ms,
        "ts": "2026-09-11T14:18:16.812Z",
    }


def test_research_closed_turn_computes_one_without_mutation() -> None:
    events = [
        _turn_start(),
        {"type": "message.part.updated", "part": {"type": "text", "text": "SENSITIVE"}},
        _turn_end(),
    ]
    original = deepcopy(events)

    normalized = normalize_research_turn_completion(events)

    assert normalized == {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "turn_completed": 1,
        "diagnostics": {
            "failure_template_hit_count": 0,
            "missing_or_empty_answer_count": 0,
            "duplicate_turn_end_count": 0,
            "mismatched_turn_end_count": 0,
            "duration_diagnostic": None,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert events == original


def test_research_unclosed_turn_is_zero_not_missing() -> None:
    normalized = normalize_research_turn_completion(
        [_turn_start(), {"type": "message.part.updated", "part": {"type": "step-start"}}]
    )

    assert normalized["turn_completed"] == 0
    assert normalized["normalization_error"] is None


def test_research_continuation_turn_starts_do_not_break_closure() -> None:
    # 续跑段携带同 userMessageID 的额外 turn.start(无 query),闭环判定不受影响。
    events = [_turn_start(), _turn_start(), _turn_end()]

    normalized = normalize_research_turn_completion(events)

    assert normalized["turn_completed"] == 1
    assert normalized["normalization_error"] is None


def test_research_missing_turn_start_is_not_computable() -> None:
    normalized = normalize_research_turn_completion([{"type": "llm.response"}])

    assert normalized["turn_completed"] is None
    assert normalized["normalization_error"] == "missing_turn_boundary"


def test_research_duration_diagnostics_do_not_block_closure() -> None:
    missing = normalize_research_turn_completion([_turn_start(), _turn_end(duration_ms=None)])
    negative = normalize_research_turn_completion([_turn_start(), _turn_end(duration_ms=-5)])
    non_numeric = normalize_research_turn_completion([_turn_start(), _turn_end(duration_ms="abc")])
    bool_duration = normalize_research_turn_completion([_turn_start(), _turn_end(duration_ms=True)])

    assert missing["turn_completed"] == 1
    assert missing["diagnostics"]["duration_diagnostic"] == "missing_duration_ms"
    for normalized in (negative, non_numeric, bool_duration):
        assert normalized["turn_completed"] == 1
        assert normalized["diagnostics"]["duration_diagnostic"] == "invalid_duration_ms"


def test_research_duplicate_and_conflicting_turn_end() -> None:
    duplicate = normalize_research_turn_completion([_turn_start(), _turn_end(), _turn_end()])
    conflicting = normalize_research_turn_completion(
        [_turn_start(), _turn_end(), _turn_end(duration_ms=999)]
    )

    assert duplicate["turn_completed"] == 1
    assert duplicate["diagnostics"]["duplicate_turn_end_count"] == 1
    assert conflicting["normalization_error"] == "conflicting_turn_boundary"
    assert conflicting["turn_completed"] is None


def test_research_conflicting_turn_start_ids_are_isolated() -> None:
    normalized = normalize_research_turn_completion(
        [_turn_start(), _turn_start(user_message_id="msg-2"), _turn_end()]
    )

    assert normalized["normalization_error"] == "conflicting_turn_boundary"
    assert normalized["turn_completed"] is None


def test_research_mismatched_turn_end_is_ignored_with_diagnostic() -> None:
    normalized = normalize_research_turn_completion(
        [_turn_start(), _turn_end(user_message_id="msg-other")]
    )

    # ID 不匹配的 turn.end 不证明本回合闭环,只进诊断,不得静默丢弃。
    assert normalized["turn_completed"] == 0
    assert normalized["diagnostics"]["mismatched_turn_end_count"] == 1
    assert normalized["normalization_error"] is None


def test_research_invalid_sequence_is_explicit_error() -> None:
    normalized = normalize_research_turn_completion("not-a-sequence")

    assert normalized["normalization_error"] == "invalid_research_trace_structure"
    assert normalized["turn_completed"] is None


def test_template_catalog_is_versioned_and_non_empty() -> None:
    assert FAILURE_TEMPLATE_CATALOG_VERSION == "failure_template_catalog_v1"
