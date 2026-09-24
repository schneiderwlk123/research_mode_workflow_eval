"""模型层错误数 collector 行为测试(稳定性维度 7.4)。

覆盖:五类 v1 目录匹配、字符串/对象双形态、name+message 之外的字段不参与匹配、
unclassified 兜底、重复投影诊断、loop 缺失、跨通道投影不重复计数、模型归因
(单模型归因/双形态字段/多模型与无模型不猜测/provider 不参与归因)、模型调用计数
(唯一 assistant 消息去重/非 assistant 不计/id 缺失保守计数/同 id 冲突路由诊断/
无路由调用只进诊断)、聊天固定不可计算、结构非法隔离、错误正文不进入规范化输出。
"""

from collectors.llm_error_count import (
    LLM_ERROR_CLASS_CATALOG_VERSION,
    LLM_ERROR_CLASSES,
    NormalizedLlmErrorTrace,
    normalize_chat_llm_errors,
    normalize_research_llm_errors,
)


def _llm_error(
    *,
    loop: object = 1,
    message: str = "boom",
    name: str = "AI_APICallError",
    as_string: bool = False,
    stack: str | None = None,
    url: str | None = None,
    session_id: str = "session-r",
    user_message_id: str = "message-r",
) -> dict[str, object]:
    inner: object
    if as_string:
        inner = message
    else:
        inner = {"name": name, "message": message}
        if stack is not None:
            inner["stack"] = stack
        if url is not None:
            inner["url"] = url
    event: dict[str, object] = {
        "type": "llm.error",
        "ts": 1_700_000_000_000,
        "sessionID": session_id,
        "userMessageID": user_message_id,
        "error": {"value": {"error": inner}},
    }
    if loop is not None:
        event["loop"] = loop
    return event


def _turn_start() -> dict[str, object]:
    return {"type": "turn.start", "sessionID": "session-r", "userMessageID": "message-r", "ts": 1}


def _message_updated(
    *,
    message_id: str | None = "msg-1",
    role: str = "assistant",
    model_id: str | None = None,
    nested_model: dict[str, str] | None = None,
    with_error: bool = False,
) -> dict[str, object]:
    info: dict[str, object] = {"role": role}
    if message_id is not None:
        info["id"] = message_id
    if model_id is not None:
        info["modelID"] = model_id
    if nested_model is not None:
        info["model"] = nested_model
    if with_error:
        info["error"] = {"name": "APIError"}
    return {"type": "message.updated", "info": info}


def _models(normalized: NormalizedLlmErrorTrace) -> list[dict[str, object]]:
    return [dict(item) for item in (normalized["error_model_counts"] or [])]


def _calls(normalized: NormalizedLlmErrorTrace) -> list[dict[str, object]]:
    return [dict(item) for item in (normalized["model_call_counts"] or [])]


def test_research_zero_errors_is_computable_zero() -> None:
    normalized = normalize_research_llm_errors([_turn_start(), {"type": "turn.end"}])
    assert normalized["mode"] == "research"
    assert normalized["normalization_error"] is None
    assert normalized["llm_error_count"] == 0
    assert dict(normalized["error_class_counts"] or {}) == {}
    assert _models(normalized) == []
    # 无 assistant 消息 → 总调用数为 0,是合法观测值(不是不可计算)
    assert normalized["total_model_call_count"] == 0
    assert _calls(normalized) == []
    assert normalized["diagnostics"]["model_unattributed_error_count"] == 0
    assert normalized["diagnostics"]["model_call_unattributed_count"] == 0
    assert normalized["trace_id"] == "session-r:message-r"


def test_research_classifies_all_v1_classes() -> None:
    events = [
        _turn_start(),
        _llm_error(loop=13, message='400: {"error":{"message":"prompt is too long: 1078568"}}'),
        _llm_error(loop=1, message="anthropic http request: timeout awaiting response headers"),
        _llm_error(loop=2, message="Post failed: net/http: timeout awaiting response headers"),
        _llm_error(loop=None, message="Payment Required", as_string=False),
        _llm_error(
            loop=None,
            message="We were unable to start processing your request within the 900-second timeout",
            as_string=True,
        ),
        _llm_error(loop=19, message="400: Invalid `signature` in `thinking` block"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["llm_error_count"] == 6
    assert dict(normalized["error_class_counts"] or {}) == {
        "context_overflow": 1,
        "provider_timeout": 2,
        "billing_failure": 1,
        "queue_timeout": 1,
        "protocol_corruption": 1,
    }
    assert normalized["diagnostics"]["unclassified_count"] == 0
    assert normalized["diagnostics"]["loop_missing_count"] == 2
    # 无 message.updated 路由证据 → 不归因、不猜测
    assert _models(normalized) == []
    assert normalized["diagnostics"]["model_unattributed_error_count"] == 6
    assert normalized["diagnostics"]["model_route_count"] == 0


def test_research_object_form_matches_name_and_message_only() -> None:
    # message 命中 billing_failure;stack 里植入 context_overflow 特征、url 植入
    # provider_timeout 特征——若 stack/url 参与匹配,按目录顺序会误归 context_overflow。
    events = [
        _turn_start(),
        _llm_error(
            loop=3,
            name="Payment Required",
            message="AI_APICallError: Payment Required",
            stack="Error: prompt is too long at /mnt/workspace/run.py",
            url="https://vipapi.example.ai/v1/messages?timeout awaiting response headers",
        ),
    ]
    normalized = normalize_research_llm_errors(events)
    assert dict(normalized["error_class_counts"] or {}) == {"billing_failure": 1}


def test_research_unclassified_fallback_does_not_guess() -> None:
    events = [_turn_start(), _llm_error(loop=5, message="some brand new failure text")]
    normalized = normalize_research_llm_errors(events)
    assert normalized["llm_error_count"] == 1
    assert dict(normalized["error_class_counts"] or {}) == {"unclassified": 1}
    assert normalized["diagnostics"]["unclassified_count"] == 1


def test_research_empty_error_payload_counts_as_unclassified() -> None:
    event = _turn_start()
    broken = {
        "type": "llm.error",
        "sessionID": "session-r",
        "userMessageID": "message-r",
        "error": {},
    }
    normalized = normalize_research_llm_errors([event, broken])
    assert normalized["llm_error_count"] == 1
    assert dict(normalized["error_class_counts"] or {}) == {"unclassified": 1}


def test_research_duplicate_projection_counted_raw_with_diagnostic() -> None:
    same = _llm_error(loop=1, message="anthropic http request: timeout awaiting response headers")
    events = [_turn_start(), same, dict(same), _llm_error(loop=2, message="Payment Required")]
    normalized = normalize_research_llm_errors(events)
    # 主值按原始事件数计(规格 7.5 基线 loop 1×2 计 2 条),重复只进诊断。
    assert normalized["llm_error_count"] == 3
    assert dict(normalized["error_class_counts"] or {}) == {
        "provider_timeout": 2,
        "billing_failure": 1,
    }
    assert normalized["diagnostics"]["duplicate_error_event_count"] == 1


def test_research_cross_channel_projections_not_counted() -> None:
    events = [
        _turn_start(),
        _llm_error(loop=8, message="timeout awaiting response headers"),
        _message_updated(with_error=True),
        _message_updated(with_error=True),
        {"type": "llm.response", "result": {"finishReason": "error"}},
        {"type": "llm.response", "result": {"finishReason": "stop"}},
        _message_updated(),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["llm_error_count"] == 1
    assert normalized["diagnostics"]["message_error_projection_count"] == 2
    assert normalized["diagnostics"]["finish_reason_error_count"] == 1


def test_research_single_model_attributes_all_errors() -> None:
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _llm_error(loop=1, message="timeout awaiting response headers"),
        _message_updated(message_id="m2", model_id="pro"),
        _llm_error(loop=2, message="Payment Required"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert _models(normalized) == [{"model": "pro", "count": 2}]
    assert normalized["diagnostics"]["model_route_count"] == 1
    assert normalized["diagnostics"]["model_unattributed_error_count"] == 0
    # 调用数与错误数取自同一路由通道,应能直接 join
    assert normalized["total_model_call_count"] == 2
    assert _calls(normalized) == [{"model": "pro", "count": 2}]


def test_research_nested_model_dict_route_attributed() -> None:
    # info.model 字典形态(与顶层 modelID 互斥出现)同样构成路由证据。
    events = [
        _turn_start(),
        _message_updated(
            nested_model={"modelID": "glm-5.3", "providerID": "comein-auto-model_openai"}
        ),
        _llm_error(
            loop=None,
            message="We were unable to start processing within the 900-second timeout",
            as_string=True,
        ),
    ]
    normalized = normalize_research_llm_errors(events)
    assert _models(normalized) == [{"model": "glm-5.3", "count": 1}]
    assert normalized["diagnostics"]["model_route_count"] == 1


def test_research_mixed_shapes_same_model_stay_single() -> None:
    # 顶层形态与字典形态指向同一模型时去重为一条,仍可归因。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m2", nested_model={"modelID": "pro"}),
        _llm_error(loop=1, message="Payment Required"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["diagnostics"]["model_route_count"] == 1
    assert _models(normalized) == [{"model": "pro", "count": 1}]
    assert normalized["total_model_call_count"] == 2


def test_research_multi_model_turn_is_not_attributed() -> None:
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m2", model_id="glm-5.3"),
        _llm_error(loop=1, message="Payment Required"),
        _llm_error(loop=2, message="timeout awaiting response headers"),
    ]
    normalized = normalize_research_llm_errors(events)
    # llm.error 不带模型标识,多模型回合逐错误归因不可证明 → 不猜测。
    assert _models(normalized) == []
    assert normalized["diagnostics"]["model_route_count"] == 2
    assert normalized["diagnostics"]["model_unattributed_error_count"] == 2
    # 但调用数照常按模型分解(与错误归因是两件事)
    assert normalized["total_model_call_count"] == 2
    assert _calls(normalized) == [{"model": "glm-5.3", "count": 1}, {"model": "pro", "count": 1}]


def test_research_provider_does_not_split_same_model() -> None:
    # 1.2.0 起 provider 不参与归因:同一 model 挂在不同 provider 上仍是一个模型。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro", nested_model=None),
        _message_updated(message_id="m2", model_id="pro"),
        _llm_error(loop=1, message="Payment Required"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["diagnostics"]["model_route_count"] == 1
    assert _models(normalized) == [{"model": "pro", "count": 1}]
    # 结果里不再出现 provider 键
    assert all("provider" not in item for item in _models(normalized))
    assert all("provider" not in item for item in _calls(normalized))


def test_research_call_count_dedupes_repeated_message_projections() -> None:
    # 同一条 assistant 消息的多次流式投影只计一次调用,但路由证据照常去重。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m2", model_id="pro"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["total_model_call_count"] == 2
    assert _calls(normalized) == [{"model": "pro", "count": 2}]


def test_research_non_assistant_messages_are_not_model_calls() -> None:
    events = [
        _turn_start(),
        _message_updated(message_id="u1", role="user"),
        _message_updated(message_id="u1", role="user"),
        _message_updated(message_id="m1", model_id="pro"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["total_model_call_count"] == 1
    assert _calls(normalized) == [{"model": "pro", "count": 1}]
    assert normalized["diagnostics"]["non_assistant_routed_message_count"] == 0


def test_research_non_assistant_message_with_route_is_diagnosed() -> None:
    # 非 assistant 消息却带路由:属口径缺口信号,单独诊断以免静默漏计。
    events = [
        _turn_start(),
        _message_updated(message_id="u1", role="user", model_id="pro"),
        _message_updated(message_id="m1", model_id="pro"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["total_model_call_count"] == 1
    assert normalized["diagnostics"]["non_assistant_routed_message_count"] == 1


def test_research_missing_message_id_counts_conservatively() -> None:
    # id 缺失无法去重:保守计为独立调用(宁多计不漏计),不猜测其模型。
    events = [
        _turn_start(),
        _message_updated(message_id=None, model_id="pro"),
        _message_updated(message_id=None, model_id="pro"),
        _message_updated(message_id="m1", model_id="pro"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["total_model_call_count"] == 3
    assert normalized["diagnostics"]["missing_message_id_count"] == 2
    assert _calls(normalized) == [{"model": "pro", "count": 3}]


def test_research_conflicting_route_on_same_message_id_is_diagnosed() -> None:
    # 同一消息 id 先后带出不同模型:路由证据自相矛盾,保留首次观测并发诊断。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m1", model_id="glm-5.3"),
        _llm_error(loop=1, message="Payment Required"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["diagnostics"]["conflicting_model_route_count"] == 1
    assert normalized["total_model_call_count"] == 1
    assert _calls(normalized) == [{"model": "pro", "count": 1}]
    assert normalized["diagnostics"]["model_route_count"] == 1
    assert _models(normalized) == [{"model": "pro", "count": 1}]


def test_research_call_without_route_keeps_denominator() -> None:
    # 无路由的调用仍计入总调用数,只是不进任何模型分项——分母不因归因失败而缩小。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _message_updated(message_id="m2"),
        _message_updated(message_id="m3"),
    ]
    normalized = normalize_research_llm_errors(events)
    assert normalized["total_model_call_count"] == 3
    assert _calls(normalized) == [{"model": "pro", "count": 1}]
    assert normalized["diagnostics"]["model_call_unattributed_count"] == 2
    assert normalized["diagnostics"]["model_route_count"] == 1


def test_research_model_call_shares_same_channel_as_error_attribution() -> None:
    # 能归因到错误的模型必然出现在调用分项里(两者同通道),否则报告无法直接算出错率。
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="glm-5.3"),
        _message_updated(message_id="m2", model_id="glm-5.3"),
        _llm_error(loop=1, message="Payment Required"),
    ]
    normalized = normalize_research_llm_errors(events)
    error_models = {item["model"] for item in _models(normalized)}
    call_models = {item["model"] for item in _calls(normalized)}
    assert error_models <= call_models


def test_research_trace_id_falls_back_to_error_event_identity() -> None:
    events = [_llm_error(loop=1, message="Payment Required", user_message_id="msg-x")]
    normalized = normalize_research_llm_errors(events)
    assert normalized["trace_id"] == "session-r:msg-x"


def test_research_non_sequence_is_structure_error() -> None:
    normalized = normalize_research_llm_errors({"type": "llm.error"})  # type: ignore[arg-type]
    assert normalized["normalization_error"] == "invalid_research_trace_structure"
    assert normalized["llm_error_count"] is None
    assert normalized["error_class_counts"] is None
    assert normalized["error_model_counts"] is None
    assert normalized["total_model_call_count"] is None
    assert normalized["model_call_counts"] is None


def test_chat_is_not_computable_without_error_channel() -> None:
    trace = {"canvas": {"meta": {"traceId": "trace-1"}, "agents": [], "answer": {"answer": "ok"}}}
    normalized = normalize_chat_llm_errors(trace)
    assert normalized["mode"] == "chat"
    assert normalized["normalization_error"] == "no_model_error_events"
    assert normalized["llm_error_count"] is None
    assert normalized["error_class_counts"] is None
    assert normalized["error_model_counts"] is None
    assert normalized["total_model_call_count"] is None
    assert normalized["model_call_counts"] is None
    assert normalized["trace_id"] == "trace-1"


def test_chat_invalid_structure_is_error() -> None:
    assert normalize_chat_llm_errors({})["normalization_error"] == "invalid_chat_trace_structure"
    assert (
        normalize_chat_llm_errors({"canvas": {"meta": "bad"}})["normalization_error"]
        == "invalid_chat_trace_structure"
    )


def test_error_text_never_enters_normalized_output() -> None:
    secret_message = "Payment Required secret-endpoint https://vipapi.internal.ai/v1/messages"
    events = [
        _turn_start(),
        _message_updated(message_id="m1", model_id="pro"),
        _llm_error(loop=1, message=secret_message, stack="trace at /mnt/private/path.py"),
    ]
    normalized = normalize_research_llm_errors(events)
    serialized = repr(normalized)
    for fragment in (
        "secret-endpoint",
        "vipapi.internal.ai",
        "/mnt/private/path.py",
        "Payment Required",
    ):
        assert fragment not in serialized
    # 模型标识是结构化元数据,允许进入结果
    assert "pro" in serialized


def test_catalog_version_and_whitelist_are_stable() -> None:
    assert LLM_ERROR_CLASS_CATALOG_VERSION == "llm_error_class_v1"
    assert LLM_ERROR_CLASSES == frozenset(
        {
            "context_overflow",
            "provider_timeout",
            "queue_timeout",
            "billing_failure",
            "protocol_corruption",
            "unclassified",
        }
    )
