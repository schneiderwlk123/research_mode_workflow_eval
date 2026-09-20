import csv
import json
from pathlib import Path

from scripts.summarize_metric_table import summarize_logs


def test_writes_chat_and_research_rows_with_only_registered_metric_columns(
    tmp_path: Path,
) -> None:
    manual_path = tmp_path / "mcp_tool_manual.json"
    manual_path.write_text(
        json.dumps(
            {
                "manual_version": "0.1.0",
                "tools": [
                    {"name": "search", "description": "搜索工具", "parameters": []},
                    {"name": "excel", "description": "表格工具", "parameters": []},
                ],
            }
        ),
        encoding="utf-8",
    )
    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(
        json.dumps(
            [
                _chat_trace(
                    "trace-1",
                    [
                        {"toolId": "c1", "toolName": "search", "arguments": {}},
                        {"toolId": "c2", "toolName": "search", "arguments": {}},
                    ],
                    user_id="user-1",
                    session_id="session-1",
                ),
                _chat_trace("trace-2", [], session_id="session-2"),
            ]
        ),
        encoding="utf-8",
    )
    research_path.write_text(
        json.dumps(
            [
                _research_turn_event("turn.start", timestamp=1_000),
                {
                    **_research_event("r1", "excel", "pending"),
                    "headers": {"userid": "user-r", "traceid": "trace-r"},
                    "sessionID": "session-r",
                    "userMessageID": "message-r",
                },
                _research_event("r1", "excel", "completed"),
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "messageID": "step-r",
                        "callID": "r2",
                        "tool": "search",
                        "state": {
                            "status": "completed",
                            "time": {"start": 1_700_000_000_000, "end": 1_700_000_001_000},
                            "input": {},
                            "metadata": {
                                "chunkList": [
                                    {"chunkId": "c1", "chunkText": "毛利率50%，环比持平。"}
                                ]
                            },
                        },
                    },
                },
                _research_step_finish("step-r", 300),
                {
                    "type": "message.updated",
                    "info": {"role": "assistant", "id": "assistant-r"},
                },
                {
                    "type": "message.part.updated",
                    "part": {"type": "text", "messageID": "assistant-r", "text": "毛利率50%。"},
                },
                _research_turn_event("turn.end", timestamp=1_007, duration_ms=7_000),
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_logs(
        chat_paths=(chat_path,),
        research_paths=(research_path,),
        output_path=output_path,
        manual_path=manual_path,
    )

    with output_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0] == {
        "user_id": "user-1",
        "session_id": "session-1",
        "log_id": "trace-1",
        "mode": "chat",
        "tool_calls_count": "2",
        "per_tool_repeat_call_count": (
            '[{"tool_name":"search","call_count":2,"repeat_call_count":1}]'
        ),
        "per_tool_call_tokens": ('[{"tool_name":"search","per_tool_call_tokens":30.0}]'),
        "total_token_consumption": "30",
        "max_tool_token_share": ('[{"tool_name":"search","max_tool_token_share":1.0}]'),
        "output_token_ratio": "0.3333333333333333",
        "cache_input_token_ratio": "0.75",
        "per_tool_call_latency_ms": ('[{"tool_name":"search","per_tool_call_latency_ms":1000.0}]'),
        "end_to_end_latency_ms": "5000",
        "max_tool_latency_share": ('[{"tool_name":"search","max_tool_latency_share":1.0}]'),
        "mcp_tool_call_inflation_rate": "0.5",
        "answer_evidence_grounding_rate": "",
    }
    assert rows[1] == {
        "user_id": "",
        "session_id": "session-2",
        "log_id": "trace-2",
        "mode": "chat",
        "tool_calls_count": "0",
        "per_tool_repeat_call_count": "[]",
        "per_tool_call_tokens": "",
        "total_token_consumption": "30",
        "max_tool_token_share": "",
        "output_token_ratio": "0.3333333333333333",
        "cache_input_token_ratio": "0.75",
        "per_tool_call_latency_ms": "",
        "end_to_end_latency_ms": "5000",
        "max_tool_latency_share": "",
        "mcp_tool_call_inflation_rate": "",
        "answer_evidence_grounding_rate": "",
    }
    assert rows[2]["user_id"] == "user-r"
    assert rows[2]["session_id"] == "session-r"
    assert rows[2]["log_id"] == "message-r"
    assert rows[2]["mode"] == "research"
    assert rows[2]["tool_calls_count"] == "2"
    assert rows[2]["per_tool_repeat_call_count"] == "[]"
    assert rows[2]["per_tool_call_tokens"] == (
        '[{"tool_name":"excel","per_tool_call_tokens":300.0},'
        '{"tool_name":"search","per_tool_call_tokens":300.0}]'
    )
    assert rows[2]["total_token_consumption"] == "300"
    assert rows[2]["max_tool_token_share"] == (
        '[{"tool_name":"excel","max_tool_token_share":1.0},'
        '{"tool_name":"search","max_tool_token_share":1.0}]'
    )
    assert rows[2]["output_token_ratio"] == "0.1"
    assert rows[2]["cache_input_token_ratio"] == "0.8461538461538461"
    assert rows[2]["per_tool_call_latency_ms"] == (
        '[{"tool_name":"excel","per_tool_call_latency_ms":1000.0},'
        '{"tool_name":"search","per_tool_call_latency_ms":1000.0}]'
    )
    assert rows[2]["end_to_end_latency_ms"] == "7000"
    assert rows[2]["max_tool_latency_share"] == (
        '[{"tool_name":"excel","max_tool_latency_share":0.5},'
        '{"tool_name":"search","max_tool_latency_share":0.5}]'
    )
    assert rows[0]["mcp_tool_call_inflation_rate"] == "0.5"
    assert rows[1]["mcp_tool_call_inflation_rate"] == ""
    assert rows[2]["mcp_tool_call_inflation_rate"] == "0.0"
    # 第一条聊天 Trace 无数值回答，锚定率不可计算写空；研究回合含数值与 chunk 证据。
    assert rows[0]["answer_evidence_grounding_rate"] == ""
    assert rows[1]["answer_evidence_grounding_rate"] == ""
    assert rows[2]["answer_evidence_grounding_rate"] == "1.0"
    assert summary == {"row_count": 3, "issue_count": 7, "metric_count": 12}


def test_answer_evidence_grounding_rate_column_computes_both_modes(
    tmp_path: Path,
) -> None:
    """数值锚定率列：聊天取 canvas 回答与工具结果，研究取最终 assistant 文本与 chunk 证据。"""

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(
        json.dumps(
            [
                _grounding_chat_trace("trace-grounded", "毛利率50%。", "毛利率50%，环比持平。"),
                _grounding_chat_trace("trace-ungrounded", "毛利率50%。", "毛利率35%，环比下降。"),
            ]
        ),
        encoding="utf-8",
    )
    research_path.write_text(
        json.dumps(
            [
                _research_turn_event("turn.start", timestamp=1_000),
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "tool": "search",
                        "callID": "call-1",
                        "state": {
                            "status": "completed",
                            "metadata": {
                                "chunkList": [{"chunkId": "c1", "chunkText": "毛利率50%。"}]
                            },
                        },
                    },
                },
                {
                    "type": "message.updated",
                    "info": {"role": "assistant", "id": "assistant-r"},
                },
                {
                    "type": "message.part.updated",
                    "part": {"type": "text", "messageID": "assistant-r", "text": "毛利率50%。"},
                },
                _research_turn_event("turn.end", timestamp=1_007, duration_ms=7_000),
            ]
        ),
        encoding="utf-8",
    )

    summarize_logs(
        chat_paths=(chat_path,),
        research_paths=(research_path,),
        output_path=output_path,
    )

    with output_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["answer_evidence_grounding_rate"] == "1.0"
    assert rows[1]["answer_evidence_grounding_rate"] == "0.0"
    assert rows[2]["answer_evidence_grounding_rate"] == "1.0"


def _grounding_chat_trace(trace_id: str, answer: str, evidence: str) -> dict[str, object]:
    """带回答与单条成功工具结果的锚定率最小聊天 Trace。"""

    return {
        "canvas": {
            "meta": {"traceId": trace_id, "create_time": 1_000},
            "answer": {"answer": answer, "create_time": 1_005},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c1", "toolName": "search", "arguments": {}}
                                ],
                                "tool_result": [
                                    {"toolId": "c1", "toolName": "search", "result": evidence}
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }


def test_research_log_id_uses_user_message_id_instead_of_trace_or_step_id(
    tmp_path: Path,
) -> None:
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    research_path.write_text(
        json.dumps(
            [
                {
                    "headers": {"userid": "user-r", "traceid": "entry-trace"},
                    "body": {"sessionId": "session-r", "messageID": "message-r"},
                    "part": {
                        "type": "step-finish",
                        "messageID": "step-message",
                        "state": {"metadata": {"traceId": "nested-trace"}},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    summarize_logs(research_paths=(research_path,), output_path=output_path)

    with output_path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["user_id"] == "user-r"
    assert row["session_id"] == "session-r"
    assert row["log_id"] == "message-r"


def test_invalid_metric_result_writes_empty_value_without_extra_columns(tmp_path: Path) -> None:
    chat_path = tmp_path / "chat.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(json.dumps([{"canvas": {"meta": {}, "agents": []}}]), encoding="utf-8")

    summary = summarize_logs(chat_paths=(chat_path,), output_path=output_path)

    with output_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["log_id"].startswith("chat:")
    assert rows[0]["log_id"].endswith(":000001")
    assert rows[0]["mode"] == "chat"
    assert rows[0]["tool_calls_count"] == ""
    assert rows[0]["per_tool_repeat_call_count"] == ""
    assert rows[0]["per_tool_call_tokens"] == ""
    assert rows[0]["total_token_consumption"] == ""
    assert rows[0]["max_tool_token_share"] == ""
    assert rows[0]["output_token_ratio"] == ""
    assert rows[0]["cache_input_token_ratio"] == ""
    assert rows[0]["per_tool_call_latency_ms"] == ""
    assert rows[0]["end_to_end_latency_ms"] == ""
    assert rows[0]["max_tool_latency_share"] == ""
    assert rows[0]["mcp_tool_call_inflation_rate"] == ""
    assert rows[0]["answer_evidence_grounding_rate"] == ""
    assert summary["issue_count"] == 12


def _chat_trace(
    trace_id: str,
    calls: list[dict[str, object]],
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {"traceId": trace_id, "create_time": 1_000}
    if session_id is not None:
        meta["sessionId"] = session_id
    return {
        **({"userId": user_id} if user_id is not None else {}),
        "canvas": {
            "meta": meta,
            "answer": {"create_time": 1_005},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": calls,
                                "tool_result": [
                                    {
                                        "toolId": call["toolId"],
                                        "toolName": call["toolName"],
                                        "durationSeconds": 1,
                                    }
                                    for call in calls
                                ],
                                "usageTokens": {
                                    "input_tokens": 20,
                                    "output_tokens": 10,
                                    "cache_tokens": 15,
                                },
                            }
                        }
                    ]
                }
            ],
        },
    }


def _research_event(call_id: str, tool_name: str, status: str) -> dict[str, object]:
    start = 1_700_000_000_000
    time = {"start": start}
    if status == "completed":
        time["end"] = start + 1000
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "messageID": "step-r",
            "callID": call_id,
            "tool": tool_name,
            "state": {"status": status, "time": time, "input": {}},
        },
    }


def _research_step_finish(message_id: str, total: int) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "step-finish",
            "messageID": message_id,
            "tokens": {
                "input": 40,
                "output": 30,
                "reasoning": 10,
                "cache": {"read": 200, "write": 20},
                "total": total,
            },
        },
    }


def _research_turn_event(
    event_type: str, *, timestamp: int, duration_ms: int | None = None
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": event_type,
        "sessionID": "session-r",
        "userMessageID": "message-r",
        "ts": timestamp,
    }
    if duration_ms is not None:
        event["durationMs"] = duration_ms
    return event
