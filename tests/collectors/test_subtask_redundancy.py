from copy import deepcopy

import pytest

from collectors.subtask_redundancy import (
    normalize_chat_subtask_redundancy,
    normalize_research_subtask_redundancy,
)


def test_chat_counts_exact_and_high_confidence_similar_redundancy() -> None:
    trace = _canvas_trace(
        [
            _subtask("查询公司2023年至2025年营业收入以及同比增长率和年度变化趋势并整理为年度表格"),
            _subtask(
                "查询公司 2023 年至 2025 年营业收入以及同比增长率和年度变化趋势并整理为年度表格。"
            ),
            _subtask(
                "查询该公司2023年至2025年营业收入以及同比增长率和年度变化趋势并整理为年度表格"
            ),
            _subtask("查询公司2023年至2026年营业收入以及同比增长率和年度变化趋势并整理为年度表格"),
        ]
    )
    original = deepcopy(trace)

    normalized = normalize_chat_subtask_redundancy(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "total_subtask_count": 4,
        "redundant_subtask_count": 2,
        "algorithm_version": "character_bigram_jaccard_v1",
        "similarity_threshold": 0.9,
        "max_novelty_ratio": 0.1,
        "diagnostics": {
            "exact_duplicate_subtask_count": 1,
            "similar_redundant_subtask_count": 1,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    assert trace == original


def test_chat_scans_all_agents_and_messages_without_pre_deduplication() -> None:
    normalized = normalize_chat_subtask_redundancy(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {"messages": [{"content": {"tool_call": [_subtask("查询行情")]}}]},
                    {
                        "messages": [
                            {"content": {"tool_call": []}},
                            {"content": {"tool_call": [_subtask("查询行情")]}},
                        ]
                    },
                ],
            }
        }
    )

    assert normalized["total_subtask_count"] == 2
    assert normalized["redundant_subtask_count"] == 1
    assert normalized["diagnostics"]["exact_duplicate_subtask_count"] == 1


def test_chat_accepts_json_arguments_and_task_fallback() -> None:
    normalized = normalize_chat_subtask_redundancy(
        _canvas_trace(
            [
                {
                    "agentName": "worker_search",
                    "toolName": "agent_dispatch",
                    "arguments": '{"task":"兼容任务正文"}',
                }
            ]
        )
    )

    assert normalized["total_subtask_count"] == 1
    assert normalized["normalization_error"] is None
    assert "兼容任务正文" not in repr(normalized)


def test_chat_keeps_sensitive_subtask_text_out_of_normalized_result() -> None:
    normalized = normalize_chat_subtask_redundancy(
        _canvas_trace([_subtask("不得进入结果的敏感查询 2025"), _subtask("查询其他指标")])
    )

    assert "不得进入结果" not in repr(normalized)


@pytest.mark.parametrize(
    "arguments",
    [
        {"image": "private-image"},
        "not-json",
        [],
    ],
)
def test_chat_invalid_subtask_event_is_not_silently_dropped(arguments: object) -> None:
    normalized = normalize_chat_subtask_redundancy(
        _canvas_trace([_subtask("有效任务"), {"agentName": "worker", "arguments": arguments}])
    )

    assert normalized["total_subtask_count"] == 1
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert normalized["normalization_error"] == "invalid_subtask_event"


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": []}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1"}, "agents": "invalid"}},
            "invalid_chat_trace_structure",
        ),
        (
            {
                "canvas": {
                    "meta": {"traceId": "trace-1"},
                    "agents": [{"messages": [{"content": {"tool_call": "invalid"}}]}],
                }
            },
            "invalid_chat_trace_structure",
        ),
    ],
)
def test_chat_invalid_canvas_structure_is_isolated(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_subtask_redundancy(trace)

    assert normalized["normalization_error"] == reason


def test_chat_ignores_ordinary_tools_and_reports_no_subtasks() -> None:
    normalized = normalize_chat_subtask_redundancy(
        _canvas_trace([{"toolId": "call-1", "toolName": "quote", "arguments": {}}])
    )

    assert normalized["total_subtask_count"] == 0
    assert normalized["normalization_error"] is None


def test_legacy_chat_tool_call_queue_is_not_supported() -> None:
    normalized = normalize_chat_subtask_redundancy(
        {
            "traceId": "legacy-trace",
            "tool_call_queue": [
                {
                    "event_type": "call_sub_agent_begin",
                    "request_payload": {"arguments": {"query": "查询行情"}},
                }
            ],
        }
    )

    assert normalized["total_subtask_count"] == 0
    assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_is_not_computable_without_confirmed_structured_subtasks() -> None:
    normalized = normalize_research_subtask_redundancy(
        [
            {
                "sessionID": "session-1",
                "userMessageID": "message-1",
                "body": {"task": "不得进入结果的研究任务"},
            }
        ]
    )

    assert normalized["trace_id"] == "session-1:message-1"
    assert normalized["mode"] == "research"
    assert normalized["total_subtask_count"] == 0
    assert normalized["normalization_error"] == "missing_structured_subtasks"
    assert "不得进入结果" not in repr(normalized)


def _subtask(text: str) -> dict[str, object]:
    return {"agentName": "worker_search", "arguments": {"query": text}}


def _canvas_trace(calls: list[dict[str, object]]) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [{"messages": [{"content": {"tool_call": calls}}]}],
        }
    }
