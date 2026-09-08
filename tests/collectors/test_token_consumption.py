from copy import deepcopy

from collectors.token_consumption import (
    normalize_chat_token_consumption,
    normalize_research_token_consumption,
)


def test_chat_sums_input_and_output_per_model_step_without_cache_or_answer() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "durationSeconds": 1,
                                "usageTokens": {
                                    "input_tokens": 100,
                                    "output_tokens": 20,
                                    "cache_tokens": "IGNORED",
                                },
                            }
                        },
                        {"content": {"tool_result": [{"result": "SENSITIVE"}]}},
                        {
                            "content": {
                                "usageTokens": {
                                    "input_tokens": 40,
                                    "output_tokens": 10,
                                }
                            }
                        },
                    ]
                }
            ],
            "answer": {
                "answer": "SENSITIVE",
                "usageTokens": {"input_tokens": 40, "output_tokens": 10},
            },
        }
    }
    original = deepcopy(trace)

    normalized = normalize_chat_token_consumption(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "step_token_totals": (120, 50),
        "diagnostics": {
            "valid_step_count": 2,
            "token_missing_count": 0,
            "duplicate_token_snapshot_count": 0,
        },
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_partial_and_absent_token_coverage_are_not_computable() -> None:
    partial = normalize_chat_token_consumption(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "durationSeconds": 1,
                                    "usageTokens": {
                                        "input_tokens": 10,
                                        "output_tokens": 2,
                                    },
                                }
                            },
                            {"content": {"durationSeconds": 1}},
                        ]
                    }
                ],
            }
        }
    )
    absent = normalize_chat_token_consumption(
        {
            "canvas": {
                "meta": {"traceId": "trace-2"},
                "agents": [{"messages": [{"content": {"durationSeconds": 1}}]}],
            }
        }
    )

    assert partial["step_token_totals"] == (12,)
    assert partial["diagnostics"]["token_missing_count"] == 1
    assert partial["normalization_error"] == "incomplete_token_coverage"
    assert absent["step_token_totals"] == ()
    assert absent["diagnostics"]["token_missing_count"] == 1
    assert absent["normalization_error"] == "no_observable_token_usage"


def test_chat_rejects_legacy_and_invalid_token_counts() -> None:
    legacy = normalize_chat_token_consumption({"agents": []})
    invalid = normalize_chat_token_consumption(
        {
            "canvas": {
                "meta": {"traceId": "trace-1"},
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "usageTokens": {
                                        "input_tokens": True,
                                        "output_tokens": 2,
                                    }
                                }
                            }
                        ]
                    }
                ],
            }
        }
    )

    assert legacy["normalization_error"] == "invalid_chat_trace_structure"
    assert invalid["normalization_error"] == "invalid_token_count"


def test_research_uses_unique_step_finish_total_and_ignores_components() -> None:
    finish = {
        "type": "message.part.updated",
        "part": {
            "type": "step-finish",
            "messageID": "assistant-1",
            "tokens": {
                "input": 1,
                "output": 2,
                "total": 100,
                "cache": "IGNORED",
            },
        },
    }
    events = [
        {
            "type": "turn.start",
            "sessionID": "session-1",
            "userMessageID": "user-1",
        },
        finish,
        deepcopy(finish),
        {
            "type": "message.updated",
            "info": {"id": "assistant-1", "tokens": {"total": 100}},
        },
        {"type": "llm.response", "loop": 1, "result": {"usage": {"total": 100}}},
    ]

    normalized = normalize_research_token_consumption(events)

    assert normalized["trace_id"] == "session-1:user-1"
    assert normalized["step_token_totals"] == (100,)
    assert normalized["diagnostics"] == {
        "valid_step_count": 1,
        "token_missing_count": 0,
        "duplicate_token_snapshot_count": 1,
    }
    assert normalized["normalization_error"] is None


def test_research_partial_missing_tokens_is_not_computable() -> None:
    normalized = normalize_research_token_consumption(
        [
            {
                "type": "message.part.updated",
                "part": {
                    "type": "step-finish",
                    "messageID": "assistant-1",
                    "tokens": {"total": 100},
                },
            },
            {
                "type": "message.part.updated",
                "part": {"type": "step-finish", "messageID": "assistant-2"},
            },
        ]
    )

    assert normalized["step_token_totals"] == (100,)
    assert normalized["diagnostics"]["token_missing_count"] == 1
    assert normalized["normalization_error"] == "incomplete_token_coverage"


def test_research_rejects_missing_id_invalid_and_conflicting_snapshots() -> None:
    missing_id = normalize_research_token_consumption(
        [
            {
                "type": "message.part.updated",
                "part": {"type": "step-finish", "tokens": {"total": 1}},
            }
        ]
    )
    invalid = normalize_research_token_consumption(
        [
            {
                "type": "message.part.updated",
                "part": {
                    "type": "step-finish",
                    "messageID": "assistant-1",
                    "tokens": {"total": -1},
                },
            }
        ]
    )
    conflicting = normalize_research_token_consumption(
        [
            {
                "type": "message.part.updated",
                "part": {
                    "type": "step-finish",
                    "messageID": "assistant-1",
                    "tokens": {"total": 1},
                },
            },
            {
                "type": "message.part.updated",
                "part": {
                    "type": "step-finish",
                    "messageID": "assistant-1",
                    "tokens": {"total": 2},
                },
            },
        ]
    )

    assert missing_id["normalization_error"] == "missing_step_id"
    assert invalid["normalization_error"] == "invalid_token_count"
    assert conflicting["normalization_error"] == "conflicting_token_snapshot"
