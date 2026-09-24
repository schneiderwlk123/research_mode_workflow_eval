import csv
import json
from pathlib import Path

from scripts.summarize_metric_table import summarize_logs


def test_writes_chat_and_research_rows_with_only_registered_metric_columns(
    tmp_path: Path,
) -> None:
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
    )

    with output_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0] == {
        "user_id": "user-1",
        "session_id": "session-1",
        "log_id": "trace-1",
        "mode": "chat",
        "tool_calls_count": "2",
        "per_tool_call_tokens": ('[{"tool_name":"search","per_tool_call_tokens":30.0}]'),
        "total_token_consumption": "30",
        "max_tool_token_share": ('[{"tool_name":"search","max_tool_token_share":1.0}]'),
        "output_token_ratio": "0.3333333333333333",
        "cache_input_token_ratio": "0.75",
        "per_tool_call_latency_ms": ('[{"tool_name":"search","per_tool_call_latency_ms":1000.0}]'),
        "end_to_end_latency_ms": "5000",
        "max_tool_latency_share": ('[{"tool_name":"search","max_tool_latency_share":1.0}]'),
        "answer_evidence_grounding_rate": "",
        "turn_completion_rate": "0",
        "continuation_restart_count": "",
        "llm_error_count": "",
        "tool_error_rate": "",
        "tool_error_attribution": "",
        "error_recovery_rate": "",
        "compaction_event_count": "",
        "api_retry_rate": "",
        "api_retry_attribution": "",
    }
    assert rows[1] == {
        "user_id": "",
        "session_id": "session-2",
        "log_id": "trace-2",
        "mode": "chat",
        "tool_calls_count": "0",
        "per_tool_call_tokens": "",
        "total_token_consumption": "30",
        "max_tool_token_share": "",
        "output_token_ratio": "0.3333333333333333",
        "cache_input_token_ratio": "0.75",
        "per_tool_call_latency_ms": "",
        "end_to_end_latency_ms": "5000",
        "max_tool_latency_share": "",
        "answer_evidence_grounding_rate": "",
        "turn_completion_rate": "0",
        "continuation_restart_count": "",
        "llm_error_count": "",
        "tool_error_rate": "",
        "tool_error_attribution": "",
        "error_recovery_rate": "",
        "compaction_event_count": "",
        "api_retry_rate": "",
        "api_retry_attribution": "",
    }
    # citation_traceability_rate 已于 2026-09-21 按需求退出宽表注册（评测器与测试保留），
    # 整行字典相等断言隐含该列不再出现在表头。
    assert "citation_traceability_rate" not in rows[0]
    assert rows[2]["user_id"] == "user-r"
    assert rows[2]["session_id"] == "session-r"
    assert rows[2]["log_id"] == "message-r"
    assert rows[2]["mode"] == "research"
    assert rows[2]["tool_calls_count"] == "2"
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
    # 第一条聊天 Trace 无数值回答，锚定率不可计算写空；研究回合含数值与 chunk 证据。
    assert rows[0]["answer_evidence_grounding_rate"] == ""
    assert rows[1]["answer_evidence_grounding_rate"] == ""
    assert rows[2]["answer_evidence_grounding_rate"] == "1.0"
    # 回合完成率：聊天 fixture 的 answer 无正文键，代理口径记 0（合法失败值）；
    # 研究 fixture 回合携带 turn.end(durationMs=7000)，闭环记 1。
    assert rows[2]["turn_completion_rate"] == "1"
    # 续跑重启次数：聊天无续跑机制恒 NOT_COMPUTABLE 写空；研究 fixture 单段
    # 回合只有 1 个 turn.start(ts=1000) 且携带 turn.end，双因素紧凑 JSON 输出
    # 重启 0 次（合法值）+ 闭环 1（复用 7.1 口径）。
    assert rows[2]["continuation_restart_count"] == (
        '[{"continuation_restart_count":0,"turn_completed":1}]'
    )
    # 模型层错误数：聊天无 llm.error 事件通道恒 NOT_COMPUTABLE 写空；研究 fixture
    # 回合无 llm.error 事件，零错误是合法值。fixture 有一条 assistant 消息但无
    # modelID：调用数照常计入分母（1 次），只是归因不到模型，分项为空。
    assert rows[2]["llm_error_count"] == (
        '[{"llm_error_count":0,"error_classes":[],"error_models":[],'
        '"total_call_count":1,"model_calls":[]}]'
    )
    # 工具错误率：聊天 fixture 的结果条目无载荷也无入口级 success 信号，载荷代理
    # 口径下无终态（no_terminal_tool_calls）写空；研究回合 excel/search 两调用均
    # completed，零错误是合法值，单元格为承载比率、分子分母与归因计数的紧凑 JSON。
    assert rows[2]["tool_error_rate"] == (
        '[{"tool_error_rate":0.0,"error_call_count":0,"terminal_call_count":2,'
        '"model_misuse_error_count":0,"infrastructure_error_count":0,'
        '"data_availability_error_count":0,"degraded_result_count":0,'
        '"unclassified_error_count":0}]'
    )
    # 错误自恢复率：聊天无错误事件通道恒 NOT_COMPUTABLE 写空；研究 fixture 回合
    # 无 llm.error 也无工具 error 终态，had_error=0 → no_error_events 写空
    # （不进报告级分母，不得记 1）。
    assert rows[2]["error_recovery_rate"] == ""
    # 上下文压缩事件数：聊天无压缩事件通道恒 NOT_COMPUTABLE 写空；研究 fixture
    # 回合无 compaction part，零压缩是合法值直接写 0。
    assert rows[2]["compaction_event_count"] == "0"
    # 框架 API 重试率：聊天无 API 明细通道恒 NOT_COMPUTABLE 写空；研究 fixture
    # 回合无 tool.debug 事件，no_tool_debug_events 不代表无重试，写空不记 0。
    assert rows[2]["api_retry_rate"] == ""
    # citation_traceability_rate 退出注册后：列数 18→17，fixture 三行各少一条
    # no_extractable_citations 问题记录，issue_count 23→20。
    # tool_error_attribution 列（2026-09-24）新增：列数 17→18，fixture 三行各多一条
    # no_tool_calls / 等价的问题记录，issue_count 23→26。
    assert summary == {"row_count": 3, "issue_count": 26, "metric_count": 19}


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


def test_tool_error_rate_column_computes_both_modes(tmp_path: Path) -> None:
    """工具错误率列：聊天载荷代理失败/降级分流与研究 error 终态都产出紧凑 JSON。"""

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(
        json.dumps(
            [
                {
                    "canvas": {
                        "meta": {"traceId": "trace-err", "create_time": 1_000},
                        "answer": {"create_time": 1_005},
                        "agents": [
                            {
                                "messages": [
                                    {
                                        "content": {
                                            "tool_call": [
                                                {
                                                    "toolId": "c1",
                                                    "toolName": "annual_report_extract",
                                                },
                                                {"toolId": "c2", "toolName": "search"},
                                            ],
                                            "tool_result": [
                                                {
                                                    "toolId": "c1",
                                                    "toolName": "annual_report_extract",
                                                    "result": {
                                                        "errorcode": "500",
                                                        "msg": "所有年报提取均失败",
                                                    },
                                                },
                                                {
                                                    "toolId": "c2",
                                                    "toolName": "search",
                                                    "result": {"errorcode": "0"},
                                                },
                                            ],
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                },
                {
                    "canvas": {
                        "meta": {"traceId": "trace-degraded", "create_time": 1_000},
                        "answer": {"create_time": 1_005},
                        "agents": [
                            {
                                "messages": [
                                    {
                                        "content": {
                                            "tool_call": [
                                                {"toolId": "d1", "toolName": "get_nl2sql_result"}
                                            ],
                                            "tool_result": [
                                                {
                                                    "toolId": "d1",
                                                    "toolName": "get_nl2sql_result",
                                                    "result": {
                                                        "tool_result": {
                                                            "code": 1,
                                                            "data": "校验说明：不受支持",
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                },
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
                        "callID": "r1",
                        "tool": "write",
                        "state": {"status": "error", "error": "INPUT_INTERRUPTED: 参数生成中断"},
                    },
                },
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "callID": "r2",
                        "tool": "search",
                        "state": {"status": "completed"},
                    },
                },
                _research_turn_event("turn.end", timestamp=2_000, duration_ms=1_000),
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
    # 聊天失败载荷:1 error(infrastructure)/2 终态。
    assert rows[0]["tool_error_rate"] == (
        '[{"tool_error_rate":0.5,"error_call_count":1,"terminal_call_count":2,'
        '"model_misuse_error_count":0,"infrastructure_error_count":1,'
        '"data_availability_error_count":0,"degraded_result_count":0,'
        '"unclassified_error_count":0}]'
    )
    # 聊天降级分流:code:1 自检降级进分母不进分子,比率为合法零值。
    assert rows[1]["tool_error_rate"] == (
        '[{"tool_error_rate":0.0,"error_call_count":0,"terminal_call_count":1,'
        '"model_misuse_error_count":0,"infrastructure_error_count":0,'
        '"data_availability_error_count":0,"degraded_result_count":1,'
        '"unclassified_error_count":0}]'
    )
    # 研究显式终态:error 归因 model_misuse,1/2。
    assert rows[2]["tool_error_rate"] == (
        '[{"tool_error_rate":0.5,"error_call_count":1,"terminal_call_count":2,'
        '"model_misuse_error_count":1,"infrastructure_error_count":0,'
        '"data_availability_error_count":0,"degraded_result_count":0,'
        '"unclassified_error_count":0}]'
    )
    # 错误正文不得进入 CSV。
    assert "INPUT_INTERRUPTED" not in rows[2]["tool_error_rate"]
    assert "所有年报提取均失败" not in rows[0]["tool_error_rate"]


def test_tool_error_attribution_column_locates_failed_tools(tmp_path: Path) -> None:
    """工具错误归因列：把逐次失败的工具名与归因写进独立列，定位到具体工具。

    此前该信息只存在于评测器 metadata，`build_metric_table` 不提取 → 静默丢弃，
    报告只能看到"N 次工具错误"却不知道坏在哪个工具。本列把 metadata 的
    `error_calls` 落成单元格，且不做 Top-K 截断（长尾正是要定位的对象）。
    """

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(
        json.dumps(
            [
                {
                    "canvas": {
                        "meta": {"traceId": "trace-attr", "create_time": 1_000},
                        "answer": {"create_time": 1_005},
                        "agents": [
                            {
                                "messages": [
                                    {
                                        "content": {
                                            "tool_call": [
                                                {
                                                    "toolId": "c1",
                                                    "toolName": "annual_report_extract",
                                                },
                                                {"toolId": "c2", "toolName": "search"},
                                            ],
                                            "tool_result": [
                                                {
                                                    "toolId": "c1",
                                                    "toolName": "annual_report_extract",
                                                    "result": {
                                                        "errorcode": "500",
                                                        "msg": "所有年报提取均失败",
                                                    },
                                                },
                                                {
                                                    "toolId": "c2",
                                                    "toolName": "search",
                                                    "result": {"errorcode": "0"},
                                                },
                                            ],
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                },
                {"canvas": {"meta": {"traceId": "trace-ok", "create_time": 1_000}, "answer": {}}},
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
                        "callID": "r1",
                        "tool": "write",
                        "state": {"status": "error", "error": "INPUT_INTERRUPTED: 参数生成中断"},
                    },
                },
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "callID": "r2",
                        "tool": "search",
                        "state": {"status": "completed"},
                    },
                },
                _research_turn_event("turn.end", timestamp=2_000, duration_ms=1_000),
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
    # 聊天载荷代理失败:定位到 annual_report_extract,归 infrastructure。
    assert json.loads(rows[0]["tool_error_attribution"]) == [
        {
            "by_tool": [{"name": "annual_report_extract", "count": 1}],
            "by_attribution": [{"name": "infrastructure_failure", "count": 1}],
        }
    ]
    # 零错误行留空——空不等于零,不得写 {"by_tool": []}。
    assert rows[1]["tool_error_attribution"] == ""
    # 研究显式终态:定位到 write,归 model_misuse。
    assert json.loads(rows[2]["tool_error_attribution"]) == [
        {
            "by_tool": [{"name": "write", "count": 1}],
            "by_attribution": [{"name": "model_misuse", "count": 1}],
        }
    ]
    # 归因计数与值列的 error_call_count 必须一一对应(交叉校验守恒)。
    assert (
        json.loads(rows[2]["tool_error_attribution"])[0]["by_tool"][0]["count"]
        == json.loads(rows[2]["tool_error_rate"])[0]["error_call_count"]
    )
    # 工具名可入列,但错误正文不得进入。
    assert "INPUT_INTERRUPTED" not in rows[2]["tool_error_attribution"]
    assert "所有年报提取均失败" not in rows[0]["tool_error_attribution"]


def test_error_recovery_rate_column_computes_research_json(tmp_path: Path) -> None:
    """错误自恢复率列：研究回合错误后闭环交付产出恢复 JSON；聊天无通道写空。"""

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(json.dumps([_chat_trace("trace-1", [])]), encoding="utf-8")
    research_path.write_text(
        json.dumps(
            [
                _research_turn_event("turn.start", timestamp=1_000),
                {
                    "type": "llm.error",
                    "sessionID": "session-r",
                    "userMessageID": "message-r",
                    "loop": 3,
                    "error": {"value": {"error": "SENSITIVE timeout awaiting headers"}},
                },
                {
                    "type": "llm.response",
                    "sessionID": "session-r",
                    "userMessageID": "message-r",
                    "loop": 3,
                    "result": {"finishReason": "tool-calls"},
                },
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "callID": "r1",
                        "tool": "grep",
                        "state": {"status": "error", "error": "socket connection was closed"},
                    },
                },
                {
                    "type": "message.updated",
                    "info": {"role": "assistant", "id": "assistant-r"},
                },
                {
                    "type": "message.part.updated",
                    "part": {"type": "text", "messageID": "assistant-r", "text": "兜底交付正文。"},
                },
                _research_turn_event("turn.end", timestamp=2_000, duration_ms=1_000),
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
    # 聊天无错误事件通道:NOT_COMPUTABLE 写空,禁止记 0/1。
    assert rows[0]["error_recovery_rate"] == ""
    # 研究:llm.error 1 条(loop 3 同 loop 重试成功)+ 工具 error 1 次;闭环且有正文
    # → error_recovered=1;单元格为紧凑 JSON,错误正文不得进入 CSV。
    assert rows[1]["error_recovery_rate"] == (
        '[{"error_recovered":1,"had_error":1,"llm_error_count":1,'
        '"tool_error_call_count":1,"retry_recovered_error_count":1}]'
    )
    assert "SENSITIVE" not in rows[1]["error_recovery_rate"]
    assert "socket connection" not in rows[1]["error_recovery_rate"]


def test_api_retry_rate_column_computes_research_json(tmp_path: Path) -> None:
    """框架 API 重试率列：研究回合按 requestId 尾序号产出紧凑 JSON；聊天无通道写空。"""

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(json.dumps([_chat_trace("trace-1", [])]), encoding="utf-8")
    research_path.write_text(
        json.dumps(
            [
                _research_turn_event("turn.start", timestamp=1_000),
                {
                    "type": "message.part.updated",
                    "part": {
                        "type": "tool",
                        "callID": "r1",
                        "tool": "document_search",
                        "state": {"status": "completed"},
                    },
                },
                {
                    "type": "tool.debug",
                    "callID": "r1",
                    "tool": "document_search",
                    "sessionID": "session-r",
                    "debug": {
                        "apiCalls": [
                            {
                                "endpoint": "/json/inner/SENSITIVE-PATH",
                                "httpStatus": 200,
                                "request": {"requestId": "req-aaaa-1", "ownerId": "SENSITIVE"},
                                "response": {"code": 200},
                            },
                            {
                                "endpoint": "/json/inner/SENSITIVE-PATH",
                                "httpStatus": 200,
                                "request": {"requestId": "req-aaaa-2"},
                                "response": {"code": 11410, "message": "SENSITIVE 已删除"},
                            },
                            {
                                "endpoint": "/json/inner/SENSITIVE-PATH",
                                "httpStatus": 200,
                                "request": {"requestId": "req-aaaa-3"},
                                "response": [1, 2],
                            },
                        ]
                    },
                },
                _research_turn_event("turn.end", timestamp=2_000, duration_ms=1_000),
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
    # 聊天 tool_result 无 API 明细通道:NOT_COMPUTABLE 写空,禁止记 0。
    assert rows[0]["api_retry_rate"] == ""
    # 研究:3 条 apiCalls、尾序号 >=2 的 2 条、业务错误(code 11410)1 条、
    # 非对象 response 进不可判定 1 条、覆盖率 1/1;endpoint 与请求/响应正文
    # 不得进入 CSV。
    cell = json.loads(rows[1]["api_retry_rate"])
    # 2026-09-21 需求方确认:JSON 单元格统一 [] 包围,单元素数组承载固定键序对象。
    assert isinstance(cell, list) and len(cell) == 1
    assert cell[0] == {
        "api_retry_rate": 2 / 3,
        "api_retry_count": 2,
        "api_call_count": 3,
        "api_business_error_count": 1,
        "undeterminable_api_count": 1,
        "api_debug_coverage": 1.0,
    }
    assert "SENSITIVE" not in rows[1]["api_retry_rate"]
    # 归因列:3 次同指纹 → 重复 2;第 2 次的前一次(第 1 次)成功 → retry_after_success 1;
    # endpoint 以聚合计数形式保留,但请求入参(ownerId)与响应正文不得进入。
    attr = json.loads(rows[1]["api_retry_attribution"])
    assert isinstance(attr, list) and len(attr) == 1
    assert attr[0]["by_tool"] == [{"name": "document_search", "count": 2}]
    assert attr[0]["retry_after_success"] == 1
    assert "ownerId" not in rows[1]["api_retry_attribution"]
    assert "已删除" not in rows[1]["api_retry_attribution"]
    # 聊天行归因留空(无通道)。
    assert rows[0]["api_retry_attribution"] == ""


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
    assert rows[0]["per_tool_call_tokens"] == ""
    assert rows[0]["total_token_consumption"] == ""
    assert rows[0]["max_tool_token_share"] == ""
    assert rows[0]["output_token_ratio"] == ""
    assert rows[0]["cache_input_token_ratio"] == ""
    assert rows[0]["per_tool_call_latency_ms"] == ""
    assert rows[0]["end_to_end_latency_ms"] == ""
    assert rows[0]["max_tool_latency_share"] == ""
    assert rows[0]["answer_evidence_grounding_rate"] == ""
    assert "citation_traceability_rate" not in rows[0]
    assert rows[0]["continuation_restart_count"] == ""
    assert rows[0]["llm_error_count"] == ""
    assert rows[0]["tool_error_rate"] == ""
    assert rows[0]["tool_error_attribution"] == ""
    assert rows[0]["error_recovery_rate"] == ""
    assert rows[0]["compaction_event_count"] == ""
    assert rows[0]["api_retry_rate"] == ""
    assert rows[0]["api_retry_attribution"] == ""
    # 空 canvas 下仅 turn_completion_rate 为 computed 0（合法失败值）不进 issues；
    # citation_traceability_rate 退出注册后，其余 17 列（含 api_retry_attribution）
    # 各产生 1 条问题记录。
    assert summary["issue_count"] == 18


def test_llm_error_count_column_writes_single_field_json(tmp_path: Path) -> None:
    """模型层错误数列：研究单元格为含主值与分类的紧凑 JSON；聊天无通道写空。"""

    chat_path = tmp_path / "chat.json"
    research_path = tmp_path / "research.json"
    output_path = tmp_path / "metric-table.csv"
    chat_path.write_text(json.dumps([_chat_trace("trace-1", [])]), encoding="utf-8")
    research_path.write_text(
        json.dumps(
            [
                _research_turn_event("turn.start", timestamp=1_000),
                {
                    "type": "llm.error",
                    "sessionID": "session-r",
                    "userMessageID": "message-r",
                    "loop": 8,
                    "error": {
                        "value": {
                            "error": {
                                "name": "AI_APICallError",
                                "message": (
                                    "anthropic http request: timeout awaiting response headers"
                                ),
                                # stack/url 携带其他类特征与端点，不得参与匹配或落盘
                                "stack": ("Error: prompt is too long at /mnt/private/run.py"),
                                "url": "https://vipapi.example.ai/v1/messages",
                            }
                        }
                    },
                },
                {
                    "type": "llm.error",
                    "sessionID": "session-r",
                    "userMessageID": "message-r",
                    "error": {
                        "value": {
                            "error": (
                                "We were unable to start processing your request "
                                "within the 900-second timeout"
                            )
                        }
                    },
                },
                {
                    "type": "message.updated",
                    "info": {
                        "id": "msg-1",
                        "role": "assistant",
                        "modelID": "m1",
                        "providerID": "p1",
                        "error": {"name": "APIError"},
                    },
                },
                # 同一条消息的第二次流式投影：调用数只计一次（去重键为消息 id）。
                {
                    "type": "message.updated",
                    "info": {
                        "id": "msg-1",
                        "role": "assistant",
                        "modelID": "m1",
                        "providerID": "p1",
                    },
                },
                # 第二条真实调用：同一模型，调用分项累加到 2。
                {
                    "type": "message.updated",
                    "info": {"id": "msg-2", "role": "assistant", "modelID": "m1"},
                },
                _research_turn_event("turn.end", timestamp=2_000, duration_ms=1_000),
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
    assert rows[0]["llm_error_count"] == ""
    # 分类按 count 降序、class 升序；单模型回合全部错误归因到该 model（provider 不再参与）。
    # 总调用数与模型调用分布一并输出：同 id 投影去重后是 2 次调用。
    assert rows[1]["llm_error_count"] == (
        '[{"llm_error_count":2,"error_classes":['
        '{"class":"provider_timeout","count":1},'
        '{"class":"queue_timeout","count":1}],'
        '"error_models":[{"model":"m1","count":2}],'
        '"total_call_count":2,"model_calls":[{"model":"m1","count":2}]}]'
    )
    # stack 中的 context_overflow 特征未泄漏进分类；端点与正文未进入 CSV。
    assert "context_overflow" not in rows[1]["llm_error_count"]
    assert "vipapi.example.ai" not in rows[1]["llm_error_count"]
    assert "prompt is too long" not in rows[1]["llm_error_count"]


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
