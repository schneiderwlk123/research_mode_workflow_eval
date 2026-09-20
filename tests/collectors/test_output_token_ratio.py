from collectors.output_token_ratio import (
    normalize_chat_output_token_ratio,
    normalize_research_output_token_ratio,
)


def test_chat_sums_output_and_total_from_same_model_steps() -> None:
    normalized = normalize_chat_output_token_ratio(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {"content": {"usageTokens": {"input_tokens": 80, "output_tokens": 20}}},
                            {"content": {"tool_result": [{"result": "SENSITIVE"}]}},
                            {
                                "content": {
                                    "durationSeconds": 1,
                                    "usageTokens": {"input_tokens": 30, "output_tokens": 10},
                                }
                            },
                        ]
                    }
                ],
                "answer": {"usageTokens": {"input_tokens": 30, "output_tokens": 10}},
            }
        }
    )

    assert normalized["output_token_consumption"] == 30
    assert normalized["total_token_consumption"] == 140
    assert normalized["normalization_error"] is None
    assert "SENSITIVE" not in repr(normalized)


def test_research_deduplicates_equal_snapshots_and_rejects_conflicts() -> None:
    equal = normalize_research_output_token_ratio([_step("s1", 20, 100), _step("s1", 20, 100)])
    conflict = normalize_research_output_token_ratio([_step("s1", 20, 100), _step("s1", 21, 100)])

    assert equal["output_token_consumption"] == 20
    assert equal["total_token_consumption"] == 100
    assert equal["diagnostics"]["duplicate_token_snapshot_count"] == 1
    assert conflict["normalization_error"] == "conflicting_token_snapshot"


def test_partial_coverage_and_inconsistent_components_are_isolated() -> None:
    partial = normalize_research_output_token_ratio([_step("s1", 20, 100), _step("s2", None, 50)])
    inconsistent = normalize_research_output_token_ratio([_step("s1", 101, 100)])

    assert partial["normalization_error"] == "incomplete_token_coverage"
    assert inconsistent["normalization_error"] == "inconsistent_token_components"


def _step(message_id: str, output: int | None, total: int) -> dict[str, object]:
    tokens: dict[str, object] = {"total": total}
    if output is not None:
        tokens["output"] = output
    return {
        "type": "message.part.updated",
        "part": {"type": "step-finish", "messageID": message_id, "tokens": tokens},
    }
