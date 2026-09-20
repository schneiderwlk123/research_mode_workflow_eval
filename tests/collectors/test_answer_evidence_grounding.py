from copy import deepcopy

import pytest

from collectors.answer_evidence_grounding import (
    normalize_chat_answer_grounding,
    normalize_research_answer_grounding,
)

_EMPTY_DIAGNOSTICS = {
    "orphan_result_count": 0,
    "failure_signal_payload_count": 0,
    "conflicting_result_count": 0,
    "evidence_chunk_count": 0,
    "excluded_tool_output_count": 0,
    "non_terminal_tool_part_count": 0,
    "turn_end_present": 0,
    "fabrication_risk_signal": 0,
    "rounded_match_count": 0,
}


def _expected(
    *,
    mode: str,
    body_channel: str,
    extracted: int,
    grounded: int,
    evidence_count: int,
    diagnostics: dict[str, int] | None = None,
    trace_id: str = "trace-1",
    error: str | None = None,
) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "body_channel": body_channel,
        "extracted_span_count": extracted,
        "grounded_span_count": grounded,
        "evidence_text_count": evidence_count,
        "unit_catalog_version": "grounding_units_v2",
        "normalization_version": "grounding_norm_v2",
        "failure_signal_rules_version": "chat_failure_signals_v1",
        "research_excluded_tools_version": "research_excluded_tools_v1",
        "diagnostics": _EMPTY_DIAGNOSTICS | (diagnostics or {}),
        "normalization_error": error,
    }


def _chat_trace(
    answer: str,
    calls: list[dict[str, object]] | None = None,
    results: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": calls or [],
                                "tool_result": results or [],
                            }
                        }
                    ]
                }
            ],
            "answer": {"answer": answer},
        }
    }


def _call(call_id: str, tool_name: str) -> dict[str, object]:
    return {"toolId": call_id, "toolName": tool_name, "arguments": {}}


def _result(call_id: str, tool_name: str, payload: object) -> dict[str, object]:
    return {
        "toolId": call_id,
        "toolName": tool_name,
        "result": payload,
        "durationSeconds": 1.0,
    }


# ---------------------------------------------------------------------------
# 聊天模式：匹配引擎
# ---------------------------------------------------------------------------


def test_chat_grounds_same_form_unit_conversion_and_trailing_zeros() -> None:
    trace = _chat_trace(
        answer="营业收入1.2亿元，同比增长10.5%。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "公司营业收入12,000万元，同比增长10.50%。",
            )
        ],
    )
    original = deepcopy(trace)

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized == _expected(
        mode="chat", body_channel="chat_answer", extracted=2, grounded=2, evidence_count=1
    )
    assert trace == original


def test_chat_boundary_guard_rejects_digit_neighbor_hits() -> None:
    trace = _chat_trace(
        answer="占比6.08%。",
        calls=[_call("call-1", "research_query")],
        results=[_result("call-1", "research_query", "涨幅前十包含16.08%。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 0


def test_chat_boundary_guard_accepts_real_occurrence() -> None:
    trace = _chat_trace(
        answer="占比6.08%。",
        calls=[_call("call-1", "research_query")],
        results=[_result("call-1", "research_query", "同比下降6.08%，环比持平。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 1


def test_chat_right_neighbor_after_unit_char_is_not_a_truncation_risk() -> None:
    # 中文研报正文存在无缝拼接（"上涨76%2026年""18.2亿美元2024"）：键以单位字符结尾
    # 时，右侧数字属于下一个数，不可能延长当前数值，不得作为截断风险拒绝。
    trace = _chat_trace(
        answer="韩国综合指数2025年上涨76%。",
        calls=[_call("call-1", "research_query")],
        results=[
            _result(
                "call-1",
                "research_query",
                "韩国综合指数2025年全年上涨76%2026年8月底累计上涨62%。",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_research_glued_unit_and_next_number_grounds() -> None:
    # 研究模式证据 chunk 同样存在无缝拼接，两侧共用同一守卫规则。
    events = [
        _turn_start(),
        _assistant_message("m-1"),
        _text_event("2031年市场规模18.2亿美元。", message_id="m-1"),
        _chunk_part(
            "search",
            _chunk("c1", "2031年全球硅电容市场规模将达到18.2亿美元2024-2031年CAGR约为8%"),
        ),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_sentence_period_after_number_is_not_a_truncation_risk() -> None:
    # 右邻小数点仅在其后仍是数字时才可能延长数值；句尾小数点必须放行。
    trace = _chat_trace(
        answer="收盘价为1,247.68元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "12m Price Target: Rmb2104 Price: Rmb1247.68.",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_enumeration_list_evidence_expands() -> None:
    # 中文研报的顿号列举数值（"38337、10214、6589家"）需要展开为逐项+单位后才能命中
    # 重排/加千分位的回答书写；半角逗号已被千分位剥离吞掉，不能作为列举分隔符。
    trace = _chat_trace(
        answer="古茗拥有10,214家门店。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "蜜雪冰城、古茗、霸王茶姬的门店数量达到38337、10214、6589家。",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1
    # 原文 + 顿号展开变体两条 haystack。
    assert normalized["evidence_text_count"] == 2


def test_chat_percentage_point_and_percent_are_equivalent() -> None:
    # 证据表格常把变化列写成"0.78%"，回答写"0.78个百分点"：两者互为同一书写。
    trace = _chat_trace(
        answer="净资产收益率较上年提高0.78个百分点。",
        calls=[_call("call-1", "company_finance_search")],
        results=[
            _result(
                "call-1",
                "company_finance_search",
                "加权平均净资产收益率24.91%，同比变化0.78%。",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_percent_matches_percentage_point_evidence() -> None:
    trace = _chat_trace(
        answer="毛利率同比下降1.78%。",
        calls=[_call("call-1", "company_finance_search")],
        results=[_result("call-1", "company_finance_search", "毛利率同比下降1.78个百分点。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 1


def test_chat_currency_prefix_and_english_magnitude_renderings() -> None:
    # 英文研报的货币书写：17亿元 = Rmb1.7bn；货币前缀键与换算系数联动。
    trace = _chat_trace(
        answer="第三季度实现收入约17亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "3Q25 revenue was up 1333% YoY to Rmb1.7bn similar to 2Q25 level.",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_million_magnitude_with_currency_prefix_grounds() -> None:
    # "vs." 缩写句点后跟货币前缀键：键首非数字时左邻小数点不构成截断风险。
    trace = _chat_trace(
        answer="第三季度合同负债降至5.43亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "Contract liability in 3Q25 digested to Rmb80m (vs. Rmb543m in 2Q25).",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 1


def test_chat_leading_decimal_fragment_still_rejected() -> None:
    # 键首是数字时左邻小数点仍拒绝：.68 是 0.68 的裸小数片段，不是 68%。
    trace = _chat_trace(
        answer="同比增长68%。",
        calls=[_call("call-1", "research_query")],
        results=[_result("call-1", "research_query", "某口径小数为.68%，与增速无关。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["rounded_match_count"] == 0


def test_chat_usd_dollar_prefix_grounds() -> None:
    trace = _chat_trace(
        answer="调整后每股收益8.17美元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "CAT reported 2Q adjusted EPS of $8.17 vs consensus $6.22.",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 1


def test_chat_cross_currency_prefixes_never_convert() -> None:
    # 跨货币类永不换算：元类 span 只生成人民币前缀键，不得命中美元书写；
    # 且货币类不生成裸英文数量级键（无货币语境的 1.7bn 无法证明币种）。
    trace = _chat_trace(
        answer="第三季度实现收入约17亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result("call-1", "searchComeinResource", "3Q25 revenue was up 1333% YoY to $1.7bn.")
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["rounded_match_count"] == 0


def test_chat_rounded_restatement_from_more_precise_evidence_grounds() -> None:
    # 舍入复述单向放行：证据小数位更多、按回答精度取整相等才算命中，
    # 命中数单独进入 rounded_match_count 诊断（保守命中数可由差值还原）。
    trace = _chat_trace(
        answer="2025年归母净利润722.01亿元。",
        calls=[_call("call-1", "company_finance_search")],
        results=[_result("call-1", "company_finance_search", "|722.0128亿|767.8631亿|")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1
    assert normalized["diagnostics"]["rounded_match_count"] == 1


def test_research_rounded_restatement_grounds() -> None:
    events = [
        _turn_start(),
        _assistant_message("m-1"),
        _text_event("全年归母净利润722.01亿元。", message_id="m-1"),
        _chunk_part("search", _chunk("c1", "宁德时代|2025Q4|当年累计|767.8631亿|722.0128亿|")),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["grounded_span_count"] == 1
    assert normalized["diagnostics"]["rounded_match_count"] == 1


def test_chat_rounded_restatement_across_downward_scale_grounds() -> None:
    # 证据表格以完整元计数（小计 168774585187.65 元 = 1687.7458518765 亿），
    # 回答四舍五入为 1687.75亿元：舍入匹配需支持向下量级因子。
    trace = _chat_trace(
        answer="酒类主营业务收入为1,687.75亿元。",
        calls=[_call("call-1", "company_finance_search")],
        results=[
            _result(
                "call-1",
                "company_finance_search",
                "<tr><td>小计</td><td>168774585187.65</td><td>-1.08</td></tr>",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1
    assert normalized["diagnostics"]["rounded_match_count"] == 1


def test_chat_rounding_does_not_accept_answer_more_precise_than_evidence() -> None:
    # 反向不放行：回答比证据更精确说明多余精度无出处，反而可能是幻觉信号。
    trace = _chat_trace(
        answer="第三季度合同负债降至5.43亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[_result("call-1", "searchComeinResource", "合同负债环比降至约5.4亿元。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["rounded_match_count"] == 0


def test_chat_rounding_requires_evidence_more_precise() -> None:
    # 同精度数字若构成出处，精确匹配早已命中；同精度不做舍入兜底。
    trace = _chat_trace(
        answer="其他业务收入5.43亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[_result("call-1", "searchComeinResource", "另一指标数值为5.43。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["rounded_match_count"] == 0


def test_chat_rounding_skips_integer_spans() -> None:
    # 整数 span（无小数位）不做舍入匹配，保持保守口径：50亿 不命中 约50.4亿。
    trace = _chat_trace(
        answer="全年收入指引下限约50亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[_result("call-1", "searchComeinResource", "全年收入指引介于50.4亿元到70.2亿元。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["rounded_match_count"] == 0


def test_chat_expands_combined_number_evidence() -> None:
    trace = _chat_trace(
        answer="装饰原纸收入12.15亿元。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[
            _result(
                "call-1",
                "searchComeinResource",
                "分产品看，装饰原纸/木浆贸易分别实现营收12.15/2.35亿元。",
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1
    # 原文 + 展开变体两条 haystack。
    assert normalized["evidence_text_count"] == 2


def test_chat_dict_payload_with_embedded_text_grounds() -> None:
    trace = _chat_trace(
        answer="日本政策利率为1%。",
        calls=[_call("call-1", "macro_query")],
        results=[_result("call-1", "macro_query", {"data": "日本政策利率1.0%"})],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_structured_numeric_leaf_is_v1_boundary() -> None:
    # v1 只实现归一化子串层：数值与单位分离在不同 JSON 字段（value/unit）的结构化
    # 叶子不做字段配对，该场景保持未命中，留待后续结构化叶子层升级。
    trace = _chat_trace(
        answer="日本政策利率为1%。",
        calls=[_call("call-1", "macro_query")],
        results=[_result("call-1", "macro_query", {"data": [{"value": 1.0, "unit": "%"}]})],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 0


def test_chat_code_blocks_are_excluded_from_span_extraction() -> None:
    trace = _chat_trace(
        answer="```python\nratio = 0.35\n```\n毛利率为35%。",
        calls=[_call("call-1", "finance_query")],
        results=[_result("call-1", "finance_query", "毛利率35%。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_chat_years_do_not_enter_spans() -> None:
    trace = _chat_trace(answer="2026年营业收入100亿元。")

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1


def test_chat_derived_percentages_stay_not_groundable() -> None:
    # 推导所得变化（由 15.56% 与 12.99% 相减）不构成出处，且新口径下
    # 2.57个百分点 的 % 等价键 2.57% 也不在证据中，仍应保持未命中。
    trace = _chat_trace(
        answer="毛利率提升2.57个百分点。",
        calls=[_call("call-1", "searchComeinResource")],
        results=[_result("call-1", "searchComeinResource", "毛利率15.56%，上年同期12.99%。")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 0


# ---------------------------------------------------------------------------
# 聊天模式：证据范围与隔离
# ---------------------------------------------------------------------------


def test_chat_failure_signal_payload_is_excluded() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        calls=[_call("call-1", "company_finance_search")],
        results=[
            _result(
                "call-1",
                "company_finance_search",
                {"errorcode": "500", "data": "营业收入100万元"},
            )
        ],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["failure_signal_payload_count"] == 1


def test_chat_orphan_results_are_excluded() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        results=[_result("orphan-1", "searchInternet", "营业收入100万元")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_chat_respond_returns_are_excluded() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        calls=[{"toolId": "resp-1", "toolName": "respond", "taskId": "t-1"}],
        results=[_result("resp-1", "respond", "营业收入100万元")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["grounded_span_count"] == 0


def test_chat_sub_agent_delegation_is_not_a_request() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        calls=[{"agentName": "worker_search", "arguments": {"query": "查营收"}}],
        results=[_result("call-1", "searchComeinResource", "营业收入100万元")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_chat_empty_evidence_with_numbers_is_fabrication_signal() -> None:
    trace = _chat_trace(answer="市场规模约100亿元，参与者超过50家。")

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 2
    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["fabrication_risk_signal"] == 1


def test_chat_answer_without_spans_extracts_zero() -> None:
    trace = _chat_trace(answer="这个问题暂无公开数据。")

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["extracted_span_count"] == 0


@pytest.mark.parametrize(
    ("answer", "evidence"),
    [
        ("营业收入1.2亿元。", None),
        ("营业收入1.2亿元。", "无关文本500万元。"),
    ],
)
def test_chat_sensitive_texts_stay_out_of_normalized_result(
    answer: str, evidence: str | None
) -> None:
    trace = _chat_trace(
        answer="敏感公司" + answer,
        calls=None if evidence is None else [_call("call-1", "search")],
        results=None
        if evidence is None
        else [_result("call-1", "search", "敏感证据载荷" + evidence)],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert "敏感" not in repr(normalized)


# ---------------------------------------------------------------------------
# 聊天模式：结构边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        ({}, "invalid_chat_trace_structure"),
        ({"canvas": {"meta": {}, "agents": [], "answer": {"answer": "x"}}}, "missing_trace_id"),
        (
            {"canvas": {"meta": {"traceId": "trace-1"}, "agents": [], "answer": {}}},
            "missing_final_answer",
        ),
    ],
)
def test_chat_structure_errors_are_explicit(trace: dict[str, object], reason: str) -> None:
    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["normalization_error"] == reason


def test_chat_call_without_tool_name_is_isolated() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        calls=[{"toolId": "call-1", "arguments": {}}],
        results=[_result("call-1", "search", "营业收入100万元")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["normalization_error"] == "missing_tool_name"
    assert normalized["diagnostics"]["orphan_result_count"] == 1


def test_chat_conflicting_call_definition_is_isolated() -> None:
    trace = _chat_trace(
        answer="营业收入100万元。",
        calls=[_call("call-1", "tool_a"), _call("call-1", "tool_b")],
        results=[_result("call-1", "tool_a", "营业收入100万元")],
    )

    normalized = normalize_chat_answer_grounding(trace)

    assert normalized["normalization_error"] == "conflicting_call_definition"


# ---------------------------------------------------------------------------
# 研究模式
# ---------------------------------------------------------------------------


def _turn_start() -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": "ses-1",
        "userMessageID": "msg-1",
        "query": "分析营业收入",
    }


def _turn_end() -> dict[str, object]:
    return {"type": "turn.end", "sessionID": "ses-1", "userMessageID": "msg-1"}


def _tool_part(tool: str, status: str, state: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {
            "type": "tool",
            "tool": tool,
            "callID": "call-1",
            "state": state or {"status": status},
        },
    }


def _chunk(chunk_id: str, text: str) -> dict[str, object]:
    chunk: dict[str, object] = {"chunkId": chunk_id, "chunkText": text}
    return chunk


def _chunk_part(tool: str, *chunks: dict[str, object]) -> dict[str, object]:
    return _tool_part(
        tool, "completed", {"status": "completed", "metadata": {"chunkList": list(chunks)}}
    )


def _text_event(text: str, message_id: str = "m-1") -> dict[str, object]:
    return {
        "type": "message.part.updated",
        "part": {"type": "text", "messageID": message_id, "text": text},
    }


def _assistant_message(message_id: str = "m-1") -> dict[str, object]:
    return {"type": "message.updated", "info": {"role": "assistant", "id": message_id}}


def test_research_write_artifact_channel_with_chunk_evidence() -> None:
    events = [
        _turn_start(),
        _tool_part(
            "write",
            "completed",
            {"status": "completed", "input": {"filePath": "/out/r.md", "content": "收入1.2亿元。"}},
        ),
        _chunk_part("searchComeinResource", _chunk("c1", "营业收入12000万元。")),
        _turn_end(),
    ]
    original = deepcopy(events)

    normalized = normalize_research_answer_grounding(events)

    assert normalized == _expected(
        mode="research",
        body_channel="write_artifact",
        extracted=1,
        grounded=1,
        evidence_count=1,
        diagnostics={"evidence_chunk_count": 1, "turn_end_present": 1},
        trace_id="ses-1:msg-1",
    )
    assert events == original


def test_research_last_write_per_path_wins() -> None:
    events = [
        _turn_start(),
        _tool_part(
            "write",
            "completed",
            {"status": "completed", "input": {"filePath": "/out/r.md", "content": "草稿。"}},
        ),
        _tool_part(
            "write",
            "completed",
            {
                "status": "completed",
                "input": {"filePath": "/out/r.md", "content": "最终毛利率50%。"},
            },
        ),
        _chunk_part("search", _chunk("c1", "毛利率50%。")),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["body_channel"] == "write_artifact"
    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_research_write_output_is_not_self_evidence() -> None:
    events = [
        _turn_start(),
        _tool_part(
            "write",
            "completed",
            {
                "status": "completed",
                "input": {"filePath": "/out/r.md", "content": "收入1.2亿元。"},
                "output": "已写入文件，正文包含收入1.2亿元。",
            },
        ),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 0
    assert normalized["diagnostics"]["excluded_tool_output_count"] == 1


def test_research_edit_fragments_form_body() -> None:
    events = [
        _turn_start(),
        _tool_part(
            "edit",
            "completed",
            {
                "status": "completed",
                "input": {
                    "newString": "## 经营摘要\n公司上半年实现营业收入1.2亿元，覆盖全年经营情况。"
                },
            },
        ),
        _chunk_part("search", _chunk("c1", "收入12000万元。")),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["body_channel"] == "edit_artifact"
    assert normalized["grounded_span_count"] == 1


def test_research_final_text_channel_uses_last_assistant_text() -> None:
    events = [
        _turn_start(),
        _assistant_message("m-1"),
        _text_event("中期说明，无数字。", message_id="m-1"),
        _text_event("最终结论：营业收入1.2亿元。", message_id="m-1"),
        _chunk_part("search", _chunk("c1", "收入12000万元。")),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["body_channel"] == "final_text"
    assert normalized["extracted_span_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_research_chunks_are_deduplicated_by_chunk_id() -> None:
    chunk = _chunk("c1", "营业收入12000万元。")
    events = [
        _turn_start(),
        _assistant_message("m-1"),
        _text_event("收入1.2亿元。", message_id="m-1"),
        _chunk_part("search", chunk),
        _chunk_part("research_query", chunk),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["diagnostics"]["evidence_chunk_count"] == 1
    assert normalized["grounded_span_count"] == 1


def test_research_non_terminal_and_error_parts_are_excluded() -> None:
    events = [
        _turn_start(),
        _tool_part("search", "pending", {"status": "pending"}),
        _tool_part("search", "error", {"status": "error"}),
        _assistant_message("m-1"),
        _text_event("收入1.2亿元。", message_id="m-1"),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["diagnostics"]["non_terminal_tool_part_count"] == 2
    assert normalized["grounded_span_count"] == 0
    # 证据侧为空且回答含数值 span，按口径输出无中生有信号。
    assert normalized["diagnostics"]["fabrication_risk_signal"] == 1


def test_research_missing_turn_boundary() -> None:
    events = [_tool_part("search", "completed", {"status": "completed"})]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["normalization_error"] == "missing_turn_boundary"


def test_research_missing_body() -> None:
    events = [_turn_start(), _tool_part("search", "completed", {"status": "completed"})]

    normalized = normalize_research_answer_grounding(events)

    assert normalized["normalization_error"] == "missing_final_answer"


def test_research_sensitive_texts_stay_out_of_normalized_result() -> None:
    events = [
        _turn_start(),
        _assistant_message("m-1"),
        _text_event("敏感结论：收入1.2亿元。", message_id="m-1"),
        _chunk_part("search", _chunk("c1", "敏感证据12000万元。")),
        _turn_end(),
    ]

    normalized = normalize_research_answer_grounding(events)

    assert "敏感" not in repr(normalized)
