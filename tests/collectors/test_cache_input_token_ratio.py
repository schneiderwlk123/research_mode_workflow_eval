from collectors.cache_input_token_ratio import (
    normalize_chat_cache_input_token_ratio,
    normalize_research_cache_input_token_ratio,
)


def test_chat_sums_cache_and_effective_input_from_model_steps() -> None:
    normalized = normalize_chat_cache_input_token_ratio(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "usageTokens": {
                                        "input_tokens": 100,
                                        "output_tokens": 20,
                                        "cache_tokens": 60,
                                    }
                                }
                            },
                            {"content": {"tool_result": [{"result": "SENSITIVE"}]}},
                            {
                                "content": {
                                    "durationSeconds": 1,
                                    "usageTokens": {
                                        "input_tokens": 50,
                                        "output_tokens": 10,
                                        "cache_tokens": 30,
                                    },
                                }
                            },
                        ]
                    }
                ],
                "answer": {
                    "usageTokens": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                        "cache_tokens": 30,
                    }
                },
            }
        }
    )

    assert normalized["cache_token_consumption"] == 90
    assert normalized["effective_input_token_consumption"] == 150
    assert normalized["normalization_error"] is None
    assert "SENSITIVE" not in repr(normalized)


def test_research_uses_input_plus_cache_and_deduplicates_equal_snapshots() -> None:
    normalized = normalize_research_cache_input_token_ratio(
        [_step("s1", 40, 30, 10, 200, 20, 300), _step("s1", 40, 30, 10, 200, 20, 300)]
    )

    assert normalized["cache_token_consumption"] == 220
    assert normalized["effective_input_token_consumption"] == 260
    assert normalized["diagnostics"]["duplicate_token_snapshot_count"] == 1
    assert normalized["normalization_error"] is None


def test_partial_coverage_is_not_estimated() -> None:
    normalized = normalize_chat_cache_input_token_ratio(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {"content": {"usageTokens": {"input_tokens": 100, "cache_tokens": 60}}},
                            {
                                "content": {
                                    "durationSeconds": 1,
                                    "usageTokens": {"input_tokens": 50},
                                }
                            },
                        ]
                    }
                ],
            }
        }
    )

    assert normalized["normalization_error"] == "incomplete_cache_token_coverage"
    assert normalized["diagnostics"]["token_missing_count"] == 1


def test_research_rejects_inconsistent_breakdown_and_conflicting_snapshot() -> None:
    inconsistent = normalize_research_cache_input_token_ratio(
        [_step("s1", 40, 30, 10, 200, 20, 301)]
    )
    conflicting = normalize_research_cache_input_token_ratio(
        [_step("s1", 40, 30, 10, 200, 20, 300), _step("s1", 41, 30, 10, 200, 20, 301)]
    )

    assert inconsistent["normalization_error"] == "inconsistent_token_breakdown"
    assert conflicting["normalization_error"] == "conflicting_token_snapshot"


def test_chat_rejects_cache_greater_than_included_input() -> None:
    normalized = normalize_chat_cache_input_token_ratio(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {"content": {"usageTokens": {"input_tokens": 10, "cache_tokens": 11}}}
                        ]
                    }
                ],
            }
        }
    )

    assert normalized["normalization_error"] == "inconsistent_cache_components"


def _step(
    message_id: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cache_read: int,
    cache_write: int,
    total: int,
) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "step-finish",
            "messageID": message_id,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "reasoning": reasoning_tokens,
                "cache": {"read": cache_read, "write": cache_write},
                "total": total,
            },
        },
    }
