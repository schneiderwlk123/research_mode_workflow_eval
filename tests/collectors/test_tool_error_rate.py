"""工具错误率双模式规范化与归因目录 v1 的行为测试。

覆盖:聊天载荷代理口径(失败信号、error_code 空串=成功硬边界、code:1 降级分流、
不可解析载荷诊断、子 Agent/respond/孤立结果排除)、研究显式终态口径(callID 生命
周期合并、error/completed/pending 分类、冲突终态)、归因目录命中与 unclassified、
敏感正文隔离与输入不变。
"""

import copy

from collectors.tool_error_rate import (
    ATTRIBUTION_CATALOG_VERSION,
    CHAT_TERMINAL_SEMANTICS,
    FAILURE_SIGNAL_RULES_VERSION,
    RESEARCH_TERMINAL_SEMANTICS,
    classify_tool_error,
    normalize_chat_tool_error_rate,
    normalize_research_tool_error_rate,
)


def test_chat_classifies_error_degraded_and_success_payloads() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "tool_a"),
                        _chat_call("call-2", "tool_b"),
                        _chat_call("call-3", "tool_c"),
                        _chat_call("call-4", "tool_d"),
                    ]
                }
            },
            {
                "content": {
                    "tool_result": [
                        _chat_result("call-1", "tool_a", result={"errorcode": "0", "data": [1]}),
                        _chat_result(
                            "call-2",
                            "tool_b",
                            result={
                                "errorcode": "500",
                                "message": "MCP error -32001: Request timed out",
                            },
                        ),
                        _chat_result(
                            "call-3",
                            "tool_c",
                            result={"code": 1, "msg": "校验说明: 部分维度不受支持,无法完整回答"},
                        ),
                    ]
                }
            },
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert normalized["normalization_error"] is None
    assert normalized["mode"] == "chat"
    assert normalized["terminal_semantics"] == CHAT_TERMINAL_SEMANTICS
    assert normalized["attribution_catalog_version"] == ATTRIBUTION_CATALOG_VERSION
    assert normalized["failure_signal_rules_version"] == FAILURE_SIGNAL_RULES_VERSION
    assert normalized["tool_calls"] == (
        {
            "call_id": "call-1",
            "tool_name": "tool_a",
            "terminal_state": "success",
            "attribution": None,
        },
        {
            "call_id": "call-2",
            "tool_name": "tool_b",
            "terminal_state": "error",
            "attribution": "infrastructure_failure",
        },
        {
            "call_id": "call-3",
            "tool_name": "tool_c",
            "terminal_state": "degraded",
            "attribution": None,
        },
    )
    assert normalized["diagnostics"]["infrastructure_error_count"] == 1
    assert normalized["diagnostics"]["degraded_result_count"] == 1
    # call-4 无关联结果:无终态,不进分母。
    assert normalized["diagnostics"]["terminal_missing_count"] == 1


def test_chat_empty_string_error_code_is_success_hard_boundary() -> None:
    """实勘教训:41 条 error_code:"" 曾被负向断言误判为失败,必须锁死为成功。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "finance_query_agent"),
                        _chat_call("call-2", "tool_b"),
                    ],
                    "tool_result": [
                        _chat_result("call-1", "finance_query_agent", result={"error_code": ""}),
                        _chat_result("call-2", "tool_b", result={"errorcode": "", "data": {}}),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert normalized["normalization_error"] is None
    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success", "success"]
    assert normalized["diagnostics"]["model_misuse_error_count"] == 0
    assert normalized["diagnostics"]["infrastructure_error_count"] == 0
    assert normalized["diagnostics"]["data_availability_error_count"] == 0
    assert normalized["diagnostics"]["unclassified_error_count"] == 0


def test_chat_status_error_and_success_false_are_failures() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "tool_a"),
                        _chat_call("call-2", "tool_b"),
                        _chat_call("call-3", "tool_c"),
                    ],
                    "tool_result": [
                        _chat_result("call-1", "tool_a", result={"status": "error"}),
                        _chat_result("call-2", "tool_b", result={"success": False}),
                        _chat_result("call-3", "tool_c", result={"status": "TIMED_OUT"}),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == [
        "error",
        "error",
        "error",
    ]
    # 无错误文本可归因时记 unclassified,只诊断不猜测。
    assert normalized["diagnostics"]["unclassified_error_count"] == 3


def test_chat_degraded_diverts_before_failure_signal() -> None:
    """code:1 降级即使携带失败信号字段也不入硬失败分子,仍进分母。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "get_nl2sql_result")],
                    "tool_result": [
                        _chat_result(
                            "call-1",
                            "get_nl2sql_result",
                            result={
                                "code": 1,
                                "success": False,
                                "msg": "校验说明: 该口径不受支持",
                            },
                        )
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["degraded"]
    assert normalized["diagnostics"]["degraded_result_count"] == 1
    assert normalized["diagnostics"]["unclassified_error_count"] == 0


def test_chat_string_payload_is_undeterminable_and_not_estimated() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "tool_a"),
                        _chat_call("call-2", "tool_b"),
                    ],
                    "tool_result": [
                        _chat_result("call-1", "tool_a", result="plain string payload"),
                        _chat_result("call-2", "tool_b", result={"errorcode": "0"}),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert normalized["normalization_error"] is None
    assert [call["call_id"] for call in normalized["tool_calls"]] == ["call-2"]
    assert normalized["diagnostics"]["undeterminable_payload_count"] == 1
    assert normalized["diagnostics"]["terminal_missing_count"] == 1

    only_strings = normalize_chat_tool_error_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [_chat_call("call-1", "tool_a")],
                        "tool_result": [_chat_result("call-1", "tool_a", result="text")],
                    }
                }
            ]
        )
    )
    assert only_strings["tool_calls"] == ()
    assert only_strings["normalization_error"] == "no_terminal_tool_calls"


def test_chat_excludes_subagent_delegation_respond_and_orphan_results() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        {"taskId": "worker-1", "agentName": "worker_search"},
                        _chat_call("call-1", "respond"),
                        _chat_call("call-2", "tool_a"),
                    ],
                    "tool_result": [
                        _chat_result("orphan", "tool_a", result={"errorcode": "500"}),
                        _chat_result("call-2", "tool_a", result={"errorcode": "0"}),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert normalized["normalization_error"] is None
    assert [call["call_id"] for call in normalized["tool_calls"]] == ["call-2"]
    assert normalized["diagnostics"]["excluded_agent_delegation_count"] == 1
    assert normalized["diagnostics"]["excluded_agent_return_count"] == 1
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_chat_duplicate_observations_resolve_worst_case_with_conflict_diagnostic() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "tool_a")],
                    "tool_result": [
                        _chat_result("call-1", "tool_a", result={"errorcode": "0"}),
                        _chat_result(
                            "call-1", "tool_a", result={"errorcode": "500", "msg": "boom"}
                        ),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["error"]
    assert normalized["diagnostics"]["duplicate_observation_count"] == 1
    assert normalized["diagnostics"]["conflicting_terminal_state_count"] == 1


def test_chat_structural_errors_are_isolated() -> None:
    missing_id = normalize_chat_tool_error_rate(
        _chat_trace([{"content": {"tool_call": [{"toolName": "tool_a"}]}}])
    )
    missing_name = normalize_chat_tool_error_rate(
        _chat_trace([{"content": {"tool_call": [{"toolId": "call-1"}]}}])
    )
    conflict = normalize_chat_tool_error_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [
                            _chat_call("call-1", "tool_a"),
                            _chat_call("call-1", "tool_b"),
                        ]
                    }
                }
            ]
        )
    )
    result_conflict = normalize_chat_tool_error_rate(
        _chat_trace(
            [
                {
                    "content": {
                        "tool_call": [_chat_call("call-1", "tool_a")],
                        "tool_result": [
                            _chat_result("call-1", "tool_b", result={"errorcode": "0"})
                        ],
                    }
                }
            ]
        )
    )
    invalid = {"canvas": {"meta": {"traceId": "chat-1"}, "agents": "invalid"}}
    legacy = {"traceId": "legacy", "tool_call_queue": []}
    missing_trace: dict[str, object] = {"canvas": {"meta": {}, "agents": []}}

    assert missing_id["normalization_error"] == "missing_call_id"
    assert missing_name["normalization_error"] == "missing_tool_name"
    assert conflict["normalization_error"] == "conflicting_call_definition"
    assert result_conflict["normalization_error"] == "conflicting_call_definition"
    assert normalize_chat_tool_error_rate(invalid)["normalization_error"] == (
        "invalid_chat_trace_structure"
    )
    assert normalize_chat_tool_error_rate(legacy)["normalization_error"] == (
        "invalid_chat_trace_structure"
    )
    assert (
        normalize_chat_tool_error_rate(missing_trace)["normalization_error"] == "missing_trace_id"
    )


def test_chat_no_tool_calls_reason() -> None:
    normalized = normalize_chat_tool_error_rate(
        {"canvas": {"meta": {"traceId": "chat-1"}, "agents": [{"messages": []}]}}
    )

    assert normalized["tool_calls"] == ()
    assert normalized["normalization_error"] == "no_tool_calls"


def test_chat_sensitive_payload_never_reaches_result_and_input_not_mutated() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "tool_a")],
                    "tool_result": [
                        _chat_result(
                            "call-1",
                            "tool_a",
                            result={"errorcode": "500", "message": "SENSITIVE_ERROR_TEXT"},
                        )
                    ],
                }
            }
        ]
    )
    original = copy.deepcopy(trace)

    normalized = normalize_chat_tool_error_rate(trace)

    assert "SENSITIVE_ERROR_TEXT" not in repr(normalized)
    assert trace == original


def test_chat_nested_tool_result_envelope_failure_and_degraded() -> None:
    """实勘结构:失败/降级信号在 {"tool_result": {...}, "doc_id": ...} 内层。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "get_stock_details"),
                        _chat_call("call-2", "get_nl2sql_result"),
                        _chat_call("call-3", "annual_report_extract"),
                    ],
                    "tool_result": [
                        {
                            "toolId": "call-1",
                            "toolName": "get_stock_details",
                            "success": True,
                            "result": {
                                "tool_result": {
                                    "code": "500",
                                    "msg": "queries 参数最多支持 10 个标的",
                                    "errorcode": "500",
                                },
                                "doc_id": "139",
                            },
                        },
                        {
                            "toolId": "call-2",
                            "toolName": "get_nl2sql_result",
                            "success": True,
                            "result": {
                                "tool_result": {
                                    "code": 1,
                                    "msg": "",
                                    "data": "校验说明：核心指标不受支持，无法完整回答。",
                                },
                                "doc_id": "5",
                            },
                        },
                        {
                            "toolId": "call-3",
                            "toolName": "annual_report_extract",
                            "success": True,
                            "result": {
                                "tool_result": {
                                    "code": "500",
                                    "msg": "未找到相关年报数据，请检查股票代码和业绩期",
                                    "errorcode": "500",
                                }
                            },
                        },
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert normalized["normalization_error"] is None
    assert [(call["terminal_state"], call["attribution"]) for call in normalized["tool_calls"]] == [
        ("error", "model_misuse"),
        ("degraded", None),
        ("error", "data_availability"),
    ]
    assert normalized["diagnostics"]["model_misuse_error_count"] == 1
    assert normalized["diagnostics"]["data_availability_error_count"] == 1
    assert normalized["diagnostics"]["degraded_result_count"] == 1


def test_chat_entry_level_failure_without_payload_is_error() -> None:
    """实勘结构:"Tool not found" 只有入口级 success/error,无任何载荷。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "searchIndustry_indicators")],
                    "tool_result": [
                        {
                            "toolId": "call-1",
                            "toolName": "searchIndustry_indicators",
                            "success": False,
                            "error": "Tool 'searchIndustry_indicators' not found",
                        }
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    # 2026-09-21 确认:"Tool '<名称>' not found" 属模型侧误用,目录 v1 窄正则命中。
    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["error"]
    assert normalized["tool_calls"][0]["attribution"] == "model_misuse"
    assert normalized["diagnostics"]["model_misuse_error_count"] == 1


def test_chat_list_payload_is_success_terminal() -> None:
    """检索类工具的 rawResult 为结构化 chunk 列表,属可解析成功终态。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "searchComeinResource")],
                    "tool_result": [
                        {
                            "toolId": "call-1",
                            "toolName": "searchComeinResource",
                            "rawResult": [{"doc_id": "0", "chunk_text": "…"}],
                        }
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success"]
    assert normalized["diagnostics"]["undeterminable_payload_count"] == 0
    assert normalized["diagnostics"]["terminal_missing_count"] == 0


def test_chat_json_encoded_string_payload_is_classified() -> None:
    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [
                        _chat_call("call-1", "annual_report_extract"),
                        _chat_call("call-2", "tool_b"),
                    ],
                    "tool_result": [
                        _chat_result(
                            "call-1",
                            "annual_report_extract",
                            result='{"errorcode": "500", "msg": "所有年报提取均失败"}',
                        ),
                        _chat_result("call-2", "tool_b", result="worker 的散文校验结论"),
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [(call["terminal_state"], call["attribution"]) for call in normalized["tool_calls"]] == [
        ("error", "infrastructure_failure")
    ]
    assert normalized["diagnostics"]["undeterminable_payload_count"] == 1


def test_chat_async_pending_code2_is_success() -> None:
    """finance_query_agent 的 code:2 "执行中" 回执不是失败信号。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "finance_query_agent")],
                    "tool_result": [
                        {
                            "toolId": "call-1",
                            "toolName": "finance_query_agent",
                            "success": True,
                            "result": {
                                "tool_result": {
                                    "code": 2,
                                    "msg": "查询后台执行中，请稍后通过查询结果工具获取结果。",
                                    "data": "",
                                    "task_id": "70aea6a0",
                                },
                                "doc_id": "2",
                            },
                        }
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success"]


def test_chat_entry_success_true_without_payload_is_success() -> None:
    """direct_answer 类结果只有入口级 success:true 无载荷,按成功终态处理。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "direct_answer")],
                    "tool_result": [
                        {"toolId": "call-1", "toolName": "direct_answer", "success": True}
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success"]
    assert normalized["diagnostics"]["undeterminable_payload_count"] == 0


def test_chat_nested_debug_trace_empty_error_code_is_not_signal() -> None:
    """实勘:空串 error_code 深埋 debug_trace.nl2sql.nodes[],不得触发失败。"""

    trace = _chat_trace(
        [
            {
                "content": {
                    "tool_call": [_chat_call("call-1", "finance_query_agent")],
                    "tool_result": [
                        {
                            "toolId": "call-1",
                            "toolName": "finance_query_agent",
                            "success": True,
                            "result": {
                                "tool_result": {
                                    "code": 1,
                                    "data": "共 332 条记录",
                                    "debug_trace": {
                                        "nl2sql": {
                                            "nodes": [
                                                {"error_code": "", "error": None, "sql": "SELECT 1"}
                                            ]
                                        }
                                    },
                                },
                                "doc_id": "2",
                            },
                        }
                    ],
                }
            }
        ]
    )

    normalized = normalize_chat_tool_error_rate(trace)

    # code:1 但无"校验说明"降级文本、无顶层失败信号:按成功处理。
    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success"]


def test_research_classifies_error_completed_and_pending_terminals() -> None:
    events = [
        _research_event("call-1", "edit", "error", error="Found multiple matches for oldString"),
        _research_event("call-2", "grep", "completed"),
        _research_event("call-3", "bash", "pending"),
        _research_event("call-3", "bash", "running"),
    ]

    normalized = normalize_research_tool_error_rate(events)

    assert normalized["normalization_error"] is None
    assert normalized["mode"] == "research"
    assert normalized["terminal_semantics"] == RESEARCH_TERMINAL_SEMANTICS
    assert normalized["failure_signal_rules_version"] is None
    assert normalized["tool_calls"] == (
        {
            "call_id": "call-1",
            "tool_name": "edit",
            "terminal_state": "error",
            "attribution": "model_misuse",
        },
        {
            "call_id": "call-2",
            "tool_name": "grep",
            "terminal_state": "success",
            "attribution": None,
        },
    )
    assert normalized["diagnostics"]["model_misuse_error_count"] == 1
    assert normalized["diagnostics"]["terminal_missing_count"] == 1


def test_research_merges_lifecycle_and_counts_duplicate_status_once() -> None:
    events = [
        _research_event("call-1", "word", "running"),
        _research_event("call-1", "word", "error", error="MCP error -32001: Request timed out"),
        _research_event("call-1", "word", "error", error="MCP error -32001: Request timed out"),
    ]

    normalized = normalize_research_tool_error_rate(events)

    assert len(normalized["tool_calls"]) == 1
    assert normalized["tool_calls"][0]["attribution"] == "infrastructure_failure"
    assert normalized["diagnostics"]["duplicate_observation_count"] == 1


def test_research_error_wins_over_completed_with_conflict_diagnostic() -> None:
    events = [
        _research_event("call-1", "tool_a", "completed"),
        _research_event("call-1", "tool_a", "error", error="socket connection was closed"),
    ]

    normalized = normalize_research_tool_error_rate(events)

    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["error"]
    assert normalized["tool_calls"][0]["attribution"] == "infrastructure_failure"
    assert normalized["diagnostics"]["conflicting_terminal_state_count"] == 1


def test_research_error_without_text_is_unclassified() -> None:
    events = [_research_event("call-1", "tool_a", "error")]

    normalized = normalize_research_tool_error_rate(events)

    assert normalized["tool_calls"][0]["attribution"] == "unclassified"
    assert normalized["diagnostics"]["unclassified_error_count"] == 1


def test_research_structural_errors_are_isolated() -> None:
    missing_id = normalize_research_tool_error_rate([_research_event(None, "tool_a", "completed")])
    conflict = normalize_research_tool_error_rate(
        [
            _research_event("call-1", "tool_a", "running"),
            _research_event("call-1", "tool_b", "completed"),
        ]
    )
    missing_name = normalize_research_tool_error_rate(
        [
            {
                "type": "message.part.updated",
                "part": {"type": "tool", "callID": "c", "state": {"status": "completed"}},
            }
        ]
    )

    assert missing_id["normalization_error"] == "missing_call_id"
    assert conflict["normalization_error"] == "conflicting_call_definition"
    assert missing_name["normalization_error"] == "missing_tool_name"


def test_research_no_tool_calls_and_no_terminal_reasons() -> None:
    no_tools = normalize_research_tool_error_rate(
        [{"type": "turn.start", "userMessageID": "msg-1", "sessionID": "ses-1"}]
    )
    all_pending = normalize_research_tool_error_rate(
        [_research_event("call-1", "tool_a", "pending")]
    )

    assert no_tools["normalization_error"] == "no_tool_calls"
    assert all_pending["tool_calls"] == ()
    assert all_pending["normalization_error"] == "no_terminal_tool_calls"
    assert all_pending["diagnostics"]["terminal_missing_count"] == 1


def test_research_malformed_state_is_diagnostic_not_failure() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {"type": "tool", "callID": "call-1", "tool": "tool_a", "state": "broken"},
        },
        _research_event("call-1", "tool_a", "completed"),
        "not-an-event",
    ]

    normalized = normalize_research_tool_error_rate(events)

    assert normalized["normalization_error"] is None
    assert [call["terminal_state"] for call in normalized["tool_calls"]] == ["success"]
    assert normalized["diagnostics"]["malformed_event_count"] == 2


def test_research_trace_id_resolution_and_input_not_mutated() -> None:
    events = [
        {"type": "turn.start", "sessionID": "ses-1", "userMessageID": "msg-1"},
        _research_event("call-1", "tool_a", "completed"),
    ]
    original = copy.deepcopy(events)

    normalized = normalize_research_tool_error_rate(events)
    explicit = normalize_research_tool_error_rate(
        events, session_id="ses-x", user_message_id="msg-x"
    )

    assert normalized["trace_id"] == "ses-1:msg-1"
    assert explicit["trace_id"] == "ses-x:msg-x"
    assert events == original


def test_research_sensitive_error_text_never_reaches_result() -> None:
    events = [
        _research_event("call-1", "tool_a", "error", error="SENSITIVE_RAW_ERROR secret=query文本")
    ]

    normalized = normalize_research_tool_error_rate(events)

    assert "SENSITIVE_RAW_ERROR" not in repr(normalized)
    assert "query文本" not in repr(normalized)


def test_classify_tool_error_catalog_v1_rules() -> None:
    assert classify_tool_error("Found multiple matches for oldString: xxx") == "model_misuse"
    assert classify_tool_error("INPUT_INTERRUPTED: stream truncated") == "model_misuse"
    assert classify_tool_error("batch 需要非空的 ops 数组") == "model_misuse"
    assert classify_tool_error("Path must be workspace-relative") == "model_misuse"
    assert classify_tool_error("Access denied outside mounted tool roots") == "model_misuse"
    assert classify_tool_error("queries 参数最多支持 10 个标的") == "model_misuse"
    # 窄正则:调用不存在的工具;裸 "not found" 数据侧文案不得误命中。
    assert classify_tool_error("Tool 'searchIndustry_indicators' not found") == "model_misuse"
    assert classify_tool_error("file not found") == "unclassified"
    assert classify_tool_error("no records found") == "unclassified"
    assert classify_tool_error("MCP error -32001: Request timed out") == "infrastructure_failure"
    assert classify_tool_error("socket connection was closed") == "infrastructure_failure"
    assert classify_tool_error("Document API request timed out") == "infrastructure_failure"
    assert classify_tool_error("code 500: 所有年报提取均失败") == "infrastructure_failure"
    assert classify_tool_error("未找到相关年报数据") == "data_availability"
    assert classify_tool_error("该文件/文件夹已被删除或移动") == "data_availability"
    assert classify_tool_error(None) == "unclassified"
    assert classify_tool_error("完全未知的错误") == "unclassified"
    # 业务错误码 11410 优先于文本规则。
    assert classify_tool_error("unknown", business_code=11410) == "data_availability"
    assert classify_tool_error("unknown", business_code="11410") == "data_availability"
    assert classify_tool_error("unknown", business_code=11442) == "unclassified"
    # 大小写与空白差异不影响英文规则命中。
    assert classify_tool_error("  found   MULTIPLE matches for oldString ") == "model_misuse"


def _chat_trace(messages: list[dict[str, object]]) -> dict[str, object]:
    midpoint = len(messages) // 2
    return {
        "canvas": {
            "meta": {"traceId": "chat-1"},
            "agents": [
                {"messages": messages[:midpoint]},
                {"messages": messages[midpoint:]},
            ],
        }
    }


def _chat_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _chat_result(call_id: str, tool_name: str, *, result: object) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "result": result}


def _research_event(
    call_id: str | None,
    tool_name: str,
    status: str,
    *,
    error: str | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {"status": status}
    if error is not None:
        state["error"] = error
    part: dict[str, object] = {"type": "tool", "tool": tool_name, "state": state}
    if call_id is not None:
        part["callID"] = call_id
    return {"type": "message.part.updated", "part": part}
