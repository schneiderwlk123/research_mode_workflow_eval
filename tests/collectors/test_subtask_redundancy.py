from copy import deepcopy

from collectors.subtask_redundancy import (
    normalize_chat_subtask_redundancy,
    normalize_research_subtask_redundancy,
)


def _subtask(task_name: str, query: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"taskName": task_name}
    if query is not None:
        payload["arguments"] = {"query": query}
    return {"event_type": "call_sub_agent_begin", "request_payload": payload}


def test_chat_counts_exact_and_high_confidence_similar_redundancy() -> None:
    trace: dict[str, object] = {
        "traceId": "trace-1",
        "tool_call_queue": [
            _subtask("查询公司2023年至2025年营业收入以及同比增长率和年度变化趋势并整理为年度表格"),
            _subtask(
                "查询公司 2023 年至 2025 年营业收入以及同比增长率和年度变化趋势并整理为年度表格。"
            ),
            _subtask(
                "查询该公司2023年至2025年营业收入以及同比增长率和年度变化趋势并整理为年度表格"
            ),
            _subtask("查询公司2023年至2026年营业收入以及同比增长率和年度变化趋势并整理为年度表格"),
        ],
    }
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


def test_chat_keeps_sensitive_subtask_text_out_of_normalized_result() -> None:
    normalized = normalize_chat_subtask_redundancy(
        {
            "traceId": "trace-secret",
            "tool_call_queue": [
                _subtask("查询敏感公司", "不得进入结果的敏感查询 2025"),
                _subtask("查询其他指标"),
            ],
        }
    )

    assert "不得进入结果" not in repr(normalized)
    assert "敏感公司" not in repr(normalized)


def test_chat_accepts_task_description_and_arguments_task_compatibility_fields() -> None:
    normalized = normalize_chat_subtask_redundancy(
        {
            "traceId": "trace-compat",
            "tool_call_queue": [
                {
                    "event_type": "call_sub_agent_begin",
                    "request_payload": {
                        "taskDescription": "兼容任务描述",
                        "arguments": {"task": "兼容任务正文"},
                    },
                }
            ],
        }
    )

    assert normalized["total_subtask_count"] == 1
    assert normalized["normalization_error"] is None
    assert "兼容任务" not in repr(normalized)


def test_chat_invalid_subtask_event_is_not_silently_dropped() -> None:
    normalized = normalize_chat_subtask_redundancy(
        {
            "traceId": "trace-1",
            "tool_call_queue": [
                {"event_type": "call_sub_agent_begin", "request_payload": {}},
            ],
        }
    )

    assert normalized["total_subtask_count"] == 0
    assert normalized["diagnostics"]["malformed_event_count"] == 1
    assert normalized["normalization_error"] == "invalid_subtask_event"


def test_chat_invalid_queue_is_isolated() -> None:
    normalized = normalize_chat_subtask_redundancy({"tool_call_queue": "invalid"})

    assert normalized["normalization_error"] == "invalid_tool_call_queue"
    assert normalized["diagnostics"]["malformed_event_count"] == 1


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
