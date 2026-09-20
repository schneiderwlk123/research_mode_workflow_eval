from copy import deepcopy

from collectors.tool_step_tokens import (
    normalize_chat_tool_step_tokens,
    normalize_research_tool_step_tokens,
)


def test_chat_associates_full_step_tokens_once_per_distinct_tool_name() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c1", "toolName": "search"},
                                    {"toolId": "c2", "toolName": "search"},
                                    {"toolId": "c3", "toolName": "quote"},
                                    {"agentName": "worker"},
                                    {"toolId": "c4", "toolName": "respond"},
                                ],
                                "usageTokens": {"input_tokens": 100, "output_tokens": 20},
                                "text": "SENSITIVE",
                            }
                        },
                        {
                            "content": {
                                "tool_call": [{"toolId": "c5", "toolName": "search"}],
                            }
                        },
                    ]
                }
            ],
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_tool_step_tokens(trace)

    assert normalized["per_tool_statistics"] == (
        {
            "tool_name": "quote",
            "call_count": 1,
            "tool_step_count": 1,
            "single_tool_step_count": 0,
            "multi_tool_step_count": 1,
            "token_observed_count": 1,
            "token_missing_count": 0,
            "tool_associated_token_total": 120,
        },
        {
            "tool_name": "search",
            "call_count": 3,
            "tool_step_count": 2,
            "single_tool_step_count": 1,
            "multi_tool_step_count": 1,
            "token_observed_count": 1,
            "token_missing_count": 1,
            "tool_associated_token_total": 120,
        },
    )
    assert normalized["diagnostics"]["shared_token_step_count"] == 1
    assert normalized["normalization_error"] is None
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_research_merges_call_lifecycle_and_joins_step_finish_by_message_id() -> None:
    events = [
        _tool_event("step-1", "c1", "search", "pending"),
        _tool_event("step-1", "c1", "search", "completed"),
        _tool_event("step-1", "c2", "quote", "completed"),
        _step_finish("step-1", 250),
        _tool_event("step-2", "c3", "search", "completed"),
    ]

    normalized = normalize_research_tool_step_tokens(events)

    assert normalized["per_tool_statistics"] == (
        {
            "tool_name": "quote",
            "call_count": 1,
            "tool_step_count": 1,
            "single_tool_step_count": 0,
            "multi_tool_step_count": 1,
            "token_observed_count": 1,
            "token_missing_count": 0,
            "tool_associated_token_total": 250,
        },
        {
            "tool_name": "search",
            "call_count": 2,
            "tool_step_count": 2,
            "single_tool_step_count": 1,
            "multi_tool_step_count": 1,
            "token_observed_count": 1,
            "token_missing_count": 1,
            "tool_associated_token_total": 250,
        },
    )
    assert normalized["diagnostics"]["duplicate_call_observation_count"] == 1
    assert normalized["diagnostics"]["shared_token_step_count"] == 1


def test_research_rejects_missing_step_id_and_conflicting_token_snapshot() -> None:
    missing_step = normalize_research_tool_step_tokens(
        [{"type": "message.part.updated", "part": {"type": "tool", "callID": "c1", "tool": "x"}}]
    )
    conflict = normalize_research_tool_step_tokens(
        [_step_finish("step-1", 10), _step_finish("step-1", 20)]
    )

    assert missing_step["normalization_error"] == "missing_step_id"
    assert conflict["normalization_error"] == "conflicting_token_snapshot"


def test_both_modes_report_no_tool_calls() -> None:
    chat = normalize_chat_tool_step_tokens(
        {"canvas": {"meta": {"traceId": "trace-empty"}, "agents": []}}
    )
    research = normalize_research_tool_step_tokens([])

    assert chat["normalization_error"] == "no_tool_calls"
    assert research["normalization_error"] == "no_tool_calls"


def _tool_event(message_id: str, call_id: str, tool: str, status: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "messageID": message_id,
            "callID": call_id,
            "tool": tool,
            "state": {"status": status, "input": {"secret": "PRIVATE"}},
        },
    }


def _step_finish(message_id: str, total: int) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {"type": "step-finish", "messageID": message_id, "tokens": {"total": total}},
    }
