"""框架 API 重试率双模式规范化行为测试(稳定性维度 7.8)。

覆盖: 聊天固定不可计算(结构合法与非法区分)、研究无 tool.debug 事件与有事件
但无 apiCalls 的两级不可计算、requestId 尾序号重试判定、(callID, requestId)
重复投影去重、畸形 requestId 保守计数、业务错误字面口径(code 成功集/
success=true 覆盖/非对象与缺 code 进 undeterminable)、错误码分布、覆盖率
(交集口径/孤立 debug/无工具 part/缺 callID 的工具 part)、trace_id 回退、
非序列输入、敏感正文隔离与输入不变。
"""

from copy import deepcopy

import pytest

from collectors.api_retry import (
    normalize_chat_api_retry,
    normalize_research_api_retry,
)

_ZERO_DIAGNOSTICS = {
    "duplicate_api_call_projection_count": 0,
    "malformed_request_id_count": 0,
    "undeterminable_api_count": 0,
    "orphan_tool_debug_count": 0,
    "missing_debug_call_id_count": 0,
    "malformed_tool_part_count": 0,
}


def _chat_trace() -> dict[str, object]:
    return {
        "canvas": {
            "meta": {"traceId": "trace-1"},
            "answer": {"answer": "SENSITIVE"},
        }
    }


def _turn_start(user_message_id: str = "msg-1", session_id: str = "ses-1") -> dict[str, object]:
    return {
        "type": "turn.start",
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "ts": "2026-09-11T14:00:20.662Z",
    }


def _tool_debug(
    call_id: object = "call-1",
    api_calls: list[dict[str, object]] | None = None,
    session_id: str = "ses-1",
) -> dict[str, object]:
    return {
        "type": "tool.debug",
        "callID": call_id,
        "tool": "document_search",
        "messageID": "m-1",
        "sessionID": session_id,
        "debug": {"apiCalls": api_calls if api_calls is not None else []},
    }


def _api_call(
    request_id: object,
    response: object = None,
    *,
    include_response: bool = True,
    endpoint: str = "/json/inner/lookup",
) -> dict[str, object]:
    call: dict[str, object] = {
        "endpoint": endpoint,
        "httpStatus": 200,
        "request": {"sessionId": "SENSITIVE"},
    }
    if request_id is not None:
        request = call["request"]
        assert isinstance(request, dict)
        request["requestId"] = request_id
    if include_response:
        # 默认为可判定的成功载荷;业务错误分类只由显式传入的 response 驱动。
        call["response"] = {"code": 200} if response is None else response
    return call


def _tool_part(call_id: object = "call-1", session_id: str = "ses-1") -> dict[str, object]:
    part: dict[str, object] = {
        "type": "tool",
        "tool": "document_search",
        "sessionID": session_id,
        "state": {"status": "completed"},
    }
    if call_id is not None:
        part["callID"] = call_id
    return {"type": "message.part.updated", "sessionID": session_id, "part": part}


def test_chat_is_not_computable_with_channel_reason() -> None:
    # 聊天 tool_result 无 API 调用明细通道;通道不存在不等于无重试,禁止记 0。
    trace = _chat_trace()
    original = deepcopy(trace)

    normalized = normalize_chat_api_retry(trace)

    assert normalized == {
        "trace_id": "trace-1",
        "mode": "chat",
        "api_call_count": None,
        "api_retry_count": None,
        "api_business_error_count": None,
        "business_error_codes": (),
        "tool_call_id_count": None,
        "debug_call_id_count": None,
        "api_debug_coverage": None,
        "api_request_depth_max": None,
        "api_retry_attribution": None,
        "diagnostics": dict(_ZERO_DIAGNOSTICS),
        "normalization_error": "no_tool_debug_events",
    }
    assert "SENSITIVE" not in repr(normalized)
    assert trace == original


def test_chat_invalid_structure_is_explicit_error() -> None:
    for trace in ({}, {"traceId": "legacy"}, {"canvas": {"meta": "not-a-mapping"}}):
        normalized = normalize_chat_api_retry(trace)
        assert normalized["mode"] == "chat"
        assert normalized["api_call_count"] is None
        assert normalized["normalization_error"] == "invalid_chat_trace_structure"


def test_research_invalid_structure_is_explicit_error() -> None:
    for events in ("not-a-sequence", {"type": "turn.start"}, None):
        normalized = normalize_research_api_retry(events)  # type: ignore[arg-type]
        assert normalized["mode"] == "research"
        assert normalized["normalization_error"] == "invalid_research_trace_structure"


def test_research_without_tool_debug_events_is_not_computable() -> None:
    # 有工具调用但无 tool.debug:不可计算,不代表无重试(覆盖率缺口不是零)。
    events = [_turn_start(), _tool_part("call-1"), _tool_part("call-2")]
    original = deepcopy(events)

    normalized = normalize_research_api_retry(events)

    assert normalized["normalization_error"] == "no_tool_debug_events"
    assert normalized["api_call_count"] is None
    assert normalized["tool_call_id_count"] is None
    assert events == original


def test_research_with_empty_api_calls_is_not_computable() -> None:
    # tool.debug 事件存在但全部 apiCalls 为空:分母为零,单独稳定原因码。
    events = [_turn_start(), _tool_part("call-1"), _tool_debug("call-1", [])]

    normalized = normalize_research_api_retry(events)

    assert normalized["normalization_error"] == "no_observable_api_calls"
    assert normalized["api_call_count"] is None
    assert normalized["debug_call_id_count"] == 1


def test_research_retry_rate_counts_fingerprint_repeats() -> None:
    # fingerprint_dup_v2:同一 (callID, endpoint+入参) 重复出现才算重试;
    # -1/-2/-3 入参相同 → 组内 3 次 → 重试 2;call-2 单次 → 0。深度=最大尾序号 3。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_part("call-2"),
        _tool_debug(
            "call-1",
            [
                _api_call("req-aaaa-1"),
                _api_call("req-aaaa-2"),
                _api_call("req-aaaa-3"),
            ],
        ),
        _tool_debug("call-2", [_api_call("req-bbbb-1")]),
    ]
    original = deepcopy(events)

    normalized = normalize_research_api_retry(events)

    assert normalized == {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "api_call_count": 4,
        "api_retry_count": 2,
        "api_business_error_count": 0,
        "business_error_codes": (),
        "tool_call_id_count": 2,
        "debug_call_id_count": 2,
        "api_debug_coverage": 1.0,
        "api_request_depth_max": 3,
        "api_retry_attribution": {
            # call-1 内 3 次同指纹(默认 response code=200 全成功):重复 2 次,
            # 且两次重复的"前一次"均成功 → retry_after_success=2。
            "by_tool": {"document_search": 2},
            "by_endpoint": {"/json/inner/lookup": 2},
            "retry_after_success": 2,
        },
        "diagnostics": dict(_ZERO_DIAGNOSTICS),
        "normalization_error": None,
    }
    assert "SENSITIVE" not in repr(normalized)
    assert events == original


def test_retry_after_success_only_counts_when_preceding_succeeded() -> None:
    # 同指纹三次:第1次失败(11410)、第2/3次成功。重复 2 次,但只有"第3次的前一次(第2次)
    # 成功"才计 retry_after_success;第2次的前一次(第1次)失败不计 → =1。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug(
            "call-1",
            [
                _api_call("req-aaaa-1", {"code": 11410, "message": "deleted"}),
                _api_call("req-aaaa-2"),
                _api_call("req-aaaa-3"),
            ],
        ),
    ]

    normalized = normalize_research_api_retry(events)

    attr = normalized["api_retry_attribution"]
    assert attr is not None  # 同指纹重复必须产出归因,不能为 None
    assert normalized["api_retry_count"] == 2
    assert attr["retry_after_success"] == 1
    assert attr["by_tool"] == {"document_search": 2}


def test_request_params_and_response_bodies_are_not_persisted() -> None:
    # 归因只落 endpoint 与聚合计数;请求入参(SENTINEL-PARAM)与响应正文
    # (SENTINEL-BODY)不得出现在规范化结果里(只以指纹哈希形式参与判定)。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug(
            "call-1",
            [
                {
                    "endpoint": "/json/inner/lookup",
                    "httpStatus": 200,
                    "request": {
                        "sessionId": "S",
                        "requestId": "req-a-1",
                        "fullPath": "SENTINEL-PARAM",
                    },
                    "response": {"code": 200, "detail": "SENTINEL-BODY"},
                },
                {
                    "endpoint": "/json/inner/lookup",
                    "httpStatus": 200,
                    "request": {
                        "sessionId": "S",
                        "requestId": "req-a-2",
                        "fullPath": "SENTINEL-PARAM",
                    },
                    "response": {"code": 200, "detail": "SENTINEL-BODY"},
                },
            ],
        ),
    ]

    normalized = normalize_research_api_retry(events)

    blob = repr(normalized)
    assert "SENTINEL-PARAM" not in blob
    assert "SENTINEL-BODY" not in blob
    # endpoint 与聚合计数是有意保留的定位信息。
    attr = normalized["api_retry_attribution"]
    assert attr is not None  # 同指纹重复必须产出归因,不能为 None
    assert attr["by_endpoint"] == {"/json/inner/lookup": 1}


def test_workflow_steps_with_distinct_params_are_not_retries() -> None:
    # 尾序号递增但入参/endpoint 不同 = 工作流步骤,不算重试(2026-09-23 实勘结论)。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug(
            "call-1",
            [
                _api_call("req-aaaa-1", endpoint="/ep/lookup"),
                _api_call("req-aaaa-2", endpoint="/ep/create"),
                _api_call("req-aaaa-3", endpoint="/ep/read"),
            ],
        ),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["api_call_count"] == 3
    assert normalized["api_retry_count"] == 0
    assert normalized["api_request_depth_max"] == 3


def test_duplicate_projection_is_deduped_by_call_id_and_request_id() -> None:
    # 同一 (callID, requestId) 的重复投影只计一次,进重复诊断(全仓去重范式)。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug("call-1", [_api_call("req-aaaa-1")]),
        _tool_debug("call-1", [_api_call("req-aaaa-1")]),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["api_call_count"] == 1
    assert normalized["api_retry_count"] == 0
    assert normalized["diagnostics"]["duplicate_api_call_projection_count"] == 1


def test_malformed_request_id_is_counted_but_not_retry() -> None:
    # requestId 缺失或无尾序号:进 malformed 诊断、计入分母;因入参/endpoint 各不同,
    # 不构成重复请求,重试为 0(新口径重试由指纹判定,与尾序号无关)。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug(
            "call-1",
            [
                _api_call("req-aaaa", endpoint="/ep/a"),
                _api_call("req-aaaa", endpoint="/ep/b"),
                _api_call(None, endpoint="/ep/c"),
                _api_call("req-bbbb-2", endpoint="/ep/d"),
            ],
        ),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["api_call_count"] == 4
    assert normalized["api_retry_count"] == 0
    assert normalized["diagnostics"]["malformed_request_id_count"] == 3


def test_business_error_literal_rule() -> None:
    # 字面口径(2026-09-23 修正):response 为对象但**缺 code 键** = 成功(带业务载荷);
    # 带 code 且 ∉ 成功集 ∧ success ≠ true 才是业务错误;response 非对象才 undeterminable。
    responses = [
        ({"code": 11410}, "error"),
        ({"code": "11443"}, "error"),
        ({"code": 500, "success": True}, "ok"),
        ({"code": 200}, "ok"),
        ({"code": "0"}, "ok"),
        ({"code": 0}, "ok"),
        ({"code": None}, "ok"),
        ({"code": ""}, "ok"),
        ({"success": False}, "ok"),  # 对象但无 code 键 → 成功(新口径)
        ({"docNodeId": "x"}, "ok"),  # 典型成功业务载荷,无 code 键
        ([1, 2], "undeterminable"),
        ("散文载荷", "undeterminable"),
        ("OMIT_RESPONSE", "undeterminable"),
    ]
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_debug(
            "call-1",
            [
                _api_call(
                    f"req-aaaa-{index + 1}",
                    None if response == "OMIT_RESPONSE" else response,
                    include_response=response != "OMIT_RESPONSE",
                )
                for index, (response, _) in enumerate(responses)
            ],
        ),
    ]

    normalized = normalize_research_api_retry(events)

    expected_errors = sum(1 for _, kind in responses if kind == "error")
    expected_undeterminable = sum(1 for _, kind in responses if kind == "undeterminable")
    assert normalized["api_call_count"] == len(responses)
    assert normalized["api_business_error_count"] == expected_errors
    assert normalized["diagnostics"]["undeterminable_api_count"] == expected_undeterminable
    assert normalized["business_error_codes"] == (("11410", 1), ("11443", 1))


def test_coverage_uses_intersection_and_orphan_debug_is_diagnosed() -> None:
    # 覆盖率 = 有 debug 的 callID ∩ 工具 callID ÷ 全部唯一工具 callID;
    # 孤立 debug(无对应工具 part)不进分子,单独诊断。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_part("call-2"),
        _tool_part("call-3"),
        _tool_debug("call-1", [_api_call("req-aaaa-1")]),
        _tool_debug("call-9", [_api_call("req-bbbb-1")]),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["tool_call_id_count"] == 3
    assert normalized["debug_call_id_count"] == 2
    assert normalized["api_debug_coverage"] == pytest.approx(1 / 3)
    assert normalized["diagnostics"]["orphan_tool_debug_count"] == 1


def test_coverage_is_none_without_tool_parts() -> None:
    # 无工具 part 时覆盖率分母为空:输出 None,不猜测为 0 或 1。
    events = [_turn_start(), _tool_debug("call-1", [_api_call("req-aaaa-1")])]

    normalized = normalize_research_api_retry(events)

    assert normalized["tool_call_id_count"] == 0
    assert normalized["api_debug_coverage"] is None
    assert normalized["normalization_error"] is None


def test_tool_part_without_call_id_is_diagnosed_not_counted() -> None:
    # 缺 callID 的工具 part 无法进入覆盖率分母:分流诊断,不阻断主值。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        _tool_part(None),
        _tool_debug("call-1", [_api_call("req-aaaa-1")]),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["tool_call_id_count"] == 1
    assert normalized["api_debug_coverage"] == 1.0
    assert normalized["diagnostics"]["malformed_tool_part_count"] == 1


def test_debug_event_without_call_id_counts_api_calls() -> None:
    # tool.debug 缺 callID:apiCalls 照常进入主值(去重键退化为 requestId),
    # 覆盖率分子无法归属,进缺失诊断。
    events = [
        _turn_start(),
        _tool_part("call-1"),
        {
            "type": "tool.debug",
            "sessionID": "ses-1",
            "debug": {"apiCalls": [_api_call("req-aaaa-2")]},
        },
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["api_call_count"] == 1
    assert normalized["api_retry_count"] == 0  # 缺 callID 无法限定同一次调用,不进分子
    assert normalized["debug_call_id_count"] == 0
    assert normalized["api_debug_coverage"] == 0.0
    assert normalized["diagnostics"]["missing_debug_call_id_count"] == 1


def test_trace_id_falls_back_to_session_id() -> None:
    # 无 turn.start 时回退事件级 sessionID(与 7.6 同一尽力提取契约)。
    events = [
        {
            "type": "tool.debug",
            "callID": "call-1",
            "sessionID": "ses-9",
            "debug": {"apiCalls": [_api_call("req-aaaa-1")]},
        },
        _tool_part("call-1", session_id="ses-9"),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["trace_id"] == "ses-9"
    assert normalized["normalization_error"] is None


def test_non_mapping_events_are_skipped() -> None:
    events = [
        _turn_start(),
        "junk",
        None,
        _tool_part("call-1"),
        _tool_debug("call-1", [_api_call("req-aaaa-1")]),
    ]

    normalized = normalize_research_api_retry(events)

    assert normalized["api_call_count"] == 1
    assert normalized["normalization_error"] is None
