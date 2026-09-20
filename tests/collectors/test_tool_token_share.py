from collectors.tool_token_share import (
    normalize_chat_tool_token_share,
    normalize_research_tool_token_share,
)


def test_chat_combines_tool_attribution_with_trace_total_and_allows_zero_numerator() -> None:
    normalized = normalize_chat_tool_token_share(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [{"toolId": "c1", "toolName": "search"}],
                                    "usageTokens": {"input_tokens": 0, "output_tokens": 0},
                                }
                            },
                            {"content": {"usageTokens": {"input_tokens": 90, "output_tokens": 10}}},
                        ]
                    }
                ],
            }
        }
    )

    assert normalized["total_token_consumption"] == 100
    assert normalized["per_tool_token_totals"] == (
        {"tool_name": "search", "tool_associated_token_total": 0},
    )
    assert normalized["normalization_error"] is None


def test_research_uses_all_steps_for_denominator_and_tool_steps_for_numerator() -> None:
    events = [
        _tool_event("step-1", "c1", "search"),
        _step_finish("step-1", 60),
        _step_finish("step-2", 40),
    ]

    normalized = normalize_research_tool_token_share(events)

    assert normalized["total_token_consumption"] == 100
    assert normalized["per_tool_token_totals"] == (
        {"tool_name": "search", "tool_associated_token_total": 60},
    )
    assert normalized["normalization_error"] is None


def test_no_tools_and_incomplete_coverage_are_not_computable() -> None:
    no_tools = normalize_chat_tool_token_share(
        {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}
    )
    incomplete = normalize_research_tool_token_share(
        [_tool_event("step-1", "c1", "search"), _step_finish("step-1", None)]
    )

    assert no_tools["normalization_error"] == "no_tool_calls"
    assert incomplete["normalization_error"] == "incomplete_token_coverage"


def _tool_event(message_id: str, call_id: str, tool_name: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "messageID": message_id,
            "callID": call_id,
            "tool": tool_name,
        },
    }


def _step_finish(message_id: str, total: int | None) -> dict[str, object]:
    part: dict[str, object] = {"type": "step-finish", "messageID": message_id}
    if total is not None:
        part["tokens"] = {"total": total}
    return {"type": "message.part.updated", "part": part}
