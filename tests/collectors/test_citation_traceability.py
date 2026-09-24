"""引用可溯率 collector 行为测试：通道选择、引用抽取、注册表构建与边界隔离。"""

from copy import deepcopy

from collectors.citation_traceability import (
    CITE_PATTERN_VERSION,
    SOURCE_REGISTRY_VERSION,
    normalize_chat_citation_traceability,
    normalize_research_citation_traceability,
)


def _chat_trace(
    answer: str | None,
    tool_rounds: list[list[int] | None],
    *,
    trace_id: str | None = "trace-001",
) -> dict[str, object]:
    """构造聊天 Trace；tool_rounds 每项为一轮检索的 doc_id 列表或 None（失败载荷）。"""

    messages: list[dict[str, object]] = []
    for index, round_ids in enumerate(tool_rounds):
        call_id = f"call-{index}"
        call: dict[str, object] = {"toolId": call_id, "toolName": "search", "arguments": {}}
        if round_ids is None:
            # 失败信号载荷轮：errorcode=500，不得进入注册表。
            call["toolName"] = "fetch"
        messages.append(
            {
                "role": "assistant",
                "content": {
                    "tool_call": [call],
                    "usageTokens": {"input_tokens": 10, "output_tokens": 5},
                },
            }
        )
        result_payload: object = (
            {"errorcode": "500", "doc_id": 99}
            if round_ids is None
            else {"doc_list": [{"doc_id": d} for d in round_ids]}
        )
        messages.append(
            {
                "role": "assistant",
                "content": {
                    "tool_result": [
                        {"toolId": call_id, "toolName": call["toolName"], "result": result_payload}
                    ]
                },
            }
        )
    answer_block = {"answer": answer} if answer is not None else {}
    return {
        "canvas": {
            "meta": {"traceId": trace_id},
            "answer": answer_block,
            "agents": [{"messages": messages}],
        }
    }


def test_chat_resolves_citations_into_cumulative_position_registry() -> None:
    trace = _chat_trace("结论见[1][3][7]。", [[1, 2], [3, 4, 5, 6, 7]])
    original = deepcopy(trace)

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized == {
        "trace_id": "trace-001",
        "mode": "chat",
        "body_channel": "chat_answer",
        "citation_pattern_version": CITE_PATTERN_VERSION,
        "source_registry_version": SOURCE_REGISTRY_VERSION,
        "extracted_citation_count": 3,
        "resolved_citation_count": 3,
        "range_exceeded_citation_count": 0,
        "nonstandard_citation_count": 0,
        "source_sequence_length": 7,
        "source_registry_size": 7,
        "diagnostics": {
            "malformed_event_count": 0,
            "orphan_result_count": 0,
            "failure_signal_payload_count": 0,
            "conflicting_result_count": 0,
            "nonstandard_citation_count": 0,
            "markdown_link_citation_count": 0,
            "range_exceeded_citation_count": 0,
            "source_list_missing": 0,
            "numbering_scheme_mixed_suspected": 0,
            "fabrication_risk_signal": 0,
        },
        "normalization_error": None,
    }
    assert trace == original


def test_chat_out_of_range_citation_is_unresolved() -> None:
    trace = _chat_trace("引用[2][9]。", [[1, 2], [3, 4]])

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["extracted_citation_count"] == 2
    assert normalized["resolved_citation_count"] == 1
    assert normalized["range_exceeded_citation_count"] == 1


def test_chat_failed_round_is_excluded_from_registry() -> None:
    # 第二轮 errorcode=500 失败，其 doc_id=99 不得扩大注册表；[9] 因此超界。
    trace = _chat_trace("引用[2][9]。", [[1, 2], None])

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["source_sequence_length"] == 2
    assert normalized["resolved_citation_count"] == 1
    assert normalized["range_exceeded_citation_count"] == 1
    assert normalized["diagnostics"]["failure_signal_payload_count"] == 1


def test_chat_deduplicates_same_number_and_counts_nonstandard_forms() -> None:
    trace = _chat_trace("[2]重复[2]，复合[1,2]与[1-3]以及链接[3](http://x)。", [[1, 2, 3]])

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["extracted_citation_count"] == 2
    assert normalized["resolved_citation_count"] == 2
    assert normalized["diagnostics"]["nonstandard_citation_count"] == 2
    assert normalized["diagnostics"]["markdown_link_citation_count"] == 1


def test_chat_citation_without_any_source_list_is_fabrication_signal() -> None:
    trace = _chat_trace("无引用来源的回答[1][2]。", [None])

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["extracted_citation_count"] == 2
    assert normalized["resolved_citation_count"] == 0
    assert normalized["range_exceeded_citation_count"] == 2
    assert normalized["diagnostics"]["fabrication_risk_signal"] == 1
    assert normalized["diagnostics"]["source_list_missing"] == 1


def test_chat_without_citations_is_not_computable_shape() -> None:
    normalized = normalize_chat_citation_traceability(_chat_trace("无引用回答。", [[1, 2]]))

    assert normalized["extracted_citation_count"] == 0
    assert normalized["resolved_citation_count"] == 0
    assert normalized["normalization_error"] is None


def test_chat_missing_answer_is_isolated() -> None:
    normalized = normalize_chat_citation_traceability(_chat_trace(None, [[1, 2]]))

    assert normalized["trace_id"] == "trace-001"
    assert normalized["body_channel"] == "missing"
    assert normalized["normalization_error"] == "missing_final_answer"


def test_chat_invalid_structure_is_isolated() -> None:
    normalized = normalize_chat_citation_traceability({"canvas": "invalid"})

    assert normalized["normalization_error"] == "invalid_chat_trace_structure"
    assert normalized["trace_id"] is None


def test_chat_fenced_code_citations_are_ignored() -> None:
    trace = _chat_trace("正文[1]。```\narr[9]\n```", [[1]])

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["extracted_citation_count"] == 1
    assert normalized["resolved_citation_count"] == 1


def test_chat_numbering_scheme_mixed_suspected_marks_renumbering_shape() -> None:
    # 三轮各 10 个 doc（N=30），引用最大值 5 未超过单轮 10 —— 无法区分累积/重排。
    rounds: list[list[int] | None] = [list(range(1, 11))] * 3
    trace = _chat_trace("引用[1][5]。", rounds)

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["source_sequence_length"] == 30
    assert normalized["resolved_citation_count"] == 2
    assert normalized["diagnostics"]["numbering_scheme_mixed_suspected"] == 1


def test_chat_cross_round_citation_is_not_suspected() -> None:
    # 引用最大值 108 超过单轮 30 —— 跨轮编号，累积形态实锤，不标记疑似重排。
    rounds: list[list[int] | None] = [*[[10, 20, 30]] * 3, [108]]
    trace = _chat_trace("引用[108]。", rounds)

    normalized = normalize_chat_citation_traceability(trace)

    assert normalized["resolved_citation_count"] == 1
    assert normalized["diagnostics"]["numbering_scheme_mixed_suspected"] == 0


def _write_part(call_id: str, content: str) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": "write",
            "state": {
                "status": "completed",
                "input": {"filePath": "report.md", "content": content},
            },
        },
    }


def _research_search_part(call_id: str, output: str, *, tool: str = "search") -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "callID": call_id,
            "tool": tool,
            "state": {"status": "completed", "output": output},
        },
    }


def _research_text_part(text: str) -> list[dict[str, object]]:
    """真实研究形态：先有 message.updated 的 assistant 声明，再有 text part。"""

    return [
        {
            "type": "message.updated",
            "info": {"role": "assistant", "id": "m1"},
        },
        {
            "type": "message.part.updated",
            "part": {"type": "text", "messageID": "m1", "text": text},
        },
    ]


def test_research_resolves_citations_from_id_list_registry() -> None:
    outputs = "id: [1]\n标题: A\nid: [12]\n标题: B\nid: [96]\n标题: C"
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        _research_search_part("t1", outputs),
        _write_part("w1", "报告正文[12][96][439]。"),
    ]

    normalized = normalize_research_citation_traceability(events)

    assert normalized["trace_id"] == "s1:u1"
    assert normalized["mode"] == "research"
    assert normalized["body_channel"] == "write_artifact"
    assert normalized["extracted_citation_count"] == 3
    assert normalized["resolved_citation_count"] == 2
    assert normalized["range_exceeded_citation_count"] == 1
    assert normalized["source_registry_size"] == 3
    assert normalized["normalization_error"] is None


def test_research_line_numbered_output_enters_registry() -> None:
    outputs = "1. [标题一]\n内容\n2. [标题二]\n内容"
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        _research_search_part("t1", outputs),
        _write_part("w1", "报告[1][2][5]。"),
    ]

    normalized = normalize_research_citation_traceability(events)

    assert normalized["source_registry_size"] == 2
    assert normalized["extracted_citation_count"] == 3
    assert normalized["resolved_citation_count"] == 2
    assert normalized["range_exceeded_citation_count"] == 1


def test_research_edit_channel_and_final_text_channel() -> None:
    outputs = "id: [3]\nid: [8]"
    events_with_edit = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        _research_search_part("t1", outputs),
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "e1",
                "tool": "edit",
                "state": {
                    "status": "completed",
                    # newString 超过 _research_body 的 30 字符门槛才会进入 edit 通道。
                    "input": {
                        "newString": "片段正文引用[8]，且长度必须超过三十字符门槛进入编辑通道。"
                    },
                },
            },
        },
    ]
    normalized = normalize_research_citation_traceability(events_with_edit)
    assert normalized["body_channel"] == "edit_artifact"
    assert normalized["resolved_citation_count"] == 1

    events_final = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        _research_search_part("t1", outputs),
        *_research_text_part("最终回答[3]。"),
    ]
    normalized = normalize_research_citation_traceability(events_final)
    assert normalized["body_channel"] == "final_text"
    assert normalized["resolved_citation_count"] == 1


def test_research_missing_turn_boundary_is_isolated() -> None:
    normalized = normalize_research_citation_traceability(
        [_research_search_part("t1", "id: [1]"), _write_part("w1", "报告[1]。")]
    )

    assert normalized["trace_id"] is None
    assert normalized["normalization_error"] == "missing_turn_boundary"


def test_research_non_terminal_parts_do_not_enter_registry() -> None:
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "t1",
                "tool": "search",
                "state": {"status": "running", "output": "id: [5]"},
            },
        },
        _write_part("w1", "报告[5]。"),
    ]

    normalized = normalize_research_citation_traceability(events)

    assert normalized["source_registry_size"] == 0
    assert normalized["resolved_citation_count"] == 0
    assert normalized["extracted_citation_count"] == 1


def test_research_no_citations_shape() -> None:
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "u1"},
        _research_search_part("t1", "id: [1]"),
        _write_part("w1", "报告无引用。"),
    ]

    normalized = normalize_research_citation_traceability(events)

    assert normalized["extracted_citation_count"] == 0
    assert normalized["normalization_error"] is None
