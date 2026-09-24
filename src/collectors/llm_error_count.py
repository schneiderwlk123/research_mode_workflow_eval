"""规范化聊天与研究模式的模型层错误证据(稳定性维度 7.4)。

研究模式统计单回合事件序列内的 `llm.error` 事件,并按版本化目录对错误文本做
大小写不敏感子串匹配分类;聊天 canvas 无模型层错误事件通道,固定返回不可计算
(硬失败由 7.1 回合完成率的代理口径捕获,不在此重复计量)。

模型路由证据(1.2.0 起仅取 model,不再取 provider):`llm.error` 事件自身不携带
模型标识,归因证据取自回合内 `message.updated` 的模型路由字段——顶层
`info.modelID`,或 `info.model` 字典形态的 `modelID`(两种形态在日志中互斥出现)。
仅当回合内去重后恰有一个模型时,全部错误事件归因到该模型;多模型或无模型时逐
错误归因不可证明,`error_model_counts` 输出空、错误数进
`model_unattributed_error_count` 诊断,不猜测。modelID 为平台自动路由别名
(如 "pro"),真实后端模型名不在日志内。

模型调用计数(1.2.0 新增):`llm.error` 不带模型标识,因此"某模型出错率"的
分母必须另有来源。同一通道的 `message.updated` 已逐条携带模型路由,故以
**唯一 assistant 消息**(`info.role == "assistant"` 且 `info.id` 去重)作为一次
模型的计量单位——同一条消息的多次流式投影只计一次。若同一 id 先后带出不同模型,
说明路由证据自相矛盾,该 id 只按首次观测计入并进 `conflicting_model_route_count`
诊断;`info.id` 缺失或非法时无法去重,按仓库既有约定保守计为独立调用(宁多计不
漏计)并进 `missing_message_id_count` 诊断。无路由的调用仍计入总调用数,但不进
任何 `model_call_counts` 分项,进 `model_call_unattributed_count` 诊断——分母不
因归因失败而缩小。总调用数为 0(回合内无 assistant 消息)是合法观测值。

安全边界:错误正文只在内存中参与匹配;`stack`/`url` 字段可能携带供应商端点与
本地路径,既不进入匹配文本也不进入任何输出。规范化结果仅保留计数、分类标签、
路由标识与安全诊断,不保留 message 原文。

跨通道去重:`message.updated.info.error` 与 `llm.response.result.finishReason`
是同一底层失败在消息/响应通道上的投影(实勘:4 条 info.error 全部落在已有
llm.error 的回合),只进诊断计数,不得计入主值,防止同一错误被重复计量。
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

# 分类目录版本:新增类别或修改匹配特征必须升版并重算基线。
LLM_ERROR_CLASS_CATALOG_VERSION = "llm_error_class_v1"
UNCLASSIFIED_ERROR_CLASS = "unclassified"

# 目录顺序即匹配优先级,首个命中即归类;特征为大小写不敏感子串。
# v1 特征来自 2026-09-17 样本 9 条 llm.error 的实勘归纳(权威口径 7.4 表)。
_ERROR_CLASS_RULES_V1: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("context_overflow", ("prompt is too long",)),
    ("provider_timeout", ("timeout awaiting response headers", "net/http: timeout")),
    ("queue_timeout", ("900-second timeout",)),
    ("billing_failure", ("Payment Required",)),
    ("protocol_corruption", ("Invalid `signature` in `thinking` block",)),
)

# 评测器白名单校验用:目录内全部类 + unclassified 兜底。
LLM_ERROR_CLASSES: frozenset[str] = frozenset(
    {name for name, _ in _ERROR_CLASS_RULES_V1} | {UNCLASSIFIED_ERROR_CLASS}
)


class LlmErrorModelCount(TypedDict):
    """归因到单一模型的错误计数。"""

    model: str
    count: int


class LlmModelCallCount(TypedDict):
    """单一模型的调用计数(唯一 assistant 消息数)。"""

    model: str
    count: int


class LlmErrorDiagnostics(TypedDict):
    """模型层错误的安全诊断计数;只含计数与路由数,不含任何错误正文。"""

    duplicate_error_event_count: int
    message_error_projection_count: int
    finish_reason_error_count: int
    loop_missing_count: int
    unclassified_count: int
    model_route_count: int
    model_unattributed_error_count: int
    model_call_unattributed_count: int
    missing_message_id_count: int
    conflicting_model_route_count: int
    non_assistant_routed_message_count: int


class NormalizedLlmErrorTrace(TypedDict):
    """供模型层错误数评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    llm_error_count: int | None
    error_class_counts: Mapping[str, int] | None
    error_model_counts: Sequence[LlmErrorModelCount] | None
    total_model_call_count: int | None
    model_call_counts: Sequence[LlmModelCallCount] | None
    diagnostics: LlmErrorDiagnostics
    normalization_error: str | None


def normalize_chat_llm_errors(trace: Mapping[str, object]) -> NormalizedLlmErrorTrace:
    """聊天模式固定不可计算:canvas 无模型层错误事件通道。

    canvas 结构非法才是 ERROR;结构合法即返回 no_model_error_events——通道不存在
    不等于无错误,禁止记 0(仓库规则:缺失输入显式不可计算,不用零值代替)。
    模型调用数同理不可得:canvas 无逐消息模型路由通道,一并留空。
    """

    canvas = trace.get("canvas") if isinstance(trace, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(canvas.get("meta"), Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas["meta"]
    trace_id = _text(meta.get("traceId"))
    return _empty("chat", trace_id, "no_model_error_events")


def normalize_research_llm_errors(events: Sequence[object]) -> NormalizedLlmErrorTrace:
    """统计单个合并逻辑回合内的 llm.error 事件数、分类、模型归因与模型调用数。

    输入契约与 6.1/7.1 一致:入口层先按 sessionID 分组、按 turn.start 切分并合并
    `_noquery` 续跑段;回合边界完整性由 7.1 负责,本函数信任单回合契约,不重复
    校验边界。零错误是合法观测值(count=0),不是不可计算。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    diagnostics = _zero_diagnostics()
    class_counts: dict[str, int] = {}
    signatures: Counter[tuple[object, str, int]] = Counter()
    # 模型调用计数:每次调用一个槽位,值是该次调用的模型(None = 该次调用无路由证据)。
    # seen_message_ids 把消息 id 映射到其槽位下标,实现"同 id 多次流式投影只计一次"。
    # 错误归因的路由集合由这份槽位派生(不再单独扫描),保证两个通道口径一致——
    # 否则同 id 冲突路由时会出现"调用分项只有 A,却声称观测到两个模型"的矛盾。
    call_routes: list[str | None] = []
    seen_message_ids: dict[str, int] = {}
    llm_error_count = 0
    trace_id: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("type")
        if event_type == "turn.start" and trace_id is None:
            trace_id = _trace_id(_text(event.get("sessionID")), _text(event.get("userMessageID")))
        elif event_type == "llm.error":
            llm_error_count += 1
            if trace_id is None:
                trace_id = _trace_id(
                    _text(event.get("sessionID")), _text(event.get("userMessageID"))
                )
            loop = event.get("loop")
            if not _is_loop_value(loop):
                diagnostics["loop_missing_count"] += 1
            text = _error_match_text(event)
            error_class = _classify_error(text)
            if error_class == UNCLASSIFIED_ERROR_CLASS:
                diagnostics["unclassified_count"] += 1
            class_counts[error_class] = class_counts.get(error_class, 0) + 1
            # 重复投影诊断键:同 loop + 同类 + 同文本长度视为同一错误的重复投影
            # (实勘 turn_027 loop=1 两条逐字节相同)。仅作诊断,主值按原始事件计。
            signatures[(_loop_key(loop), error_class, len(text))] += 1
        elif event_type == "message.updated":
            info = event.get("info")
            if not isinstance(info, Mapping):
                continue
            if info.get("error") is not None:
                diagnostics["message_error_projection_count"] += 1
            # 只有 assistant 消息代表一次真实模型调用;user 消息不消耗模型。
            # 路由证据与调用计数共用同一来源,保证"有错误必有对应调用"可 join。
            if _text(info.get("role")) != "assistant":
                if _model_route(info) is not None:
                    diagnostics["non_assistant_routed_message_count"] += 1
                continue
            route = _model_route(info)
            message_id = _text(info.get("id"))
            if message_id is None:
                # id 缺失无法去重:保守计为独立调用(与 7.3/7.6/7.8 同方向),不猜测其模型。
                call_routes.append(route)
                diagnostics["missing_message_id_count"] += 1
                continue
            index = seen_message_ids.get(message_id)
            if index is None:
                seen_message_ids[message_id] = len(call_routes)
                call_routes.append(route)
            elif call_routes[index] != route:
                # 同一消息 id 带出不同模型:路由证据自相矛盾,保留首次观测并发诊断。
                diagnostics["conflicting_model_route_count"] += 1
        elif event_type == "llm.response":
            result = event.get("result")
            if isinstance(result, Mapping) and result.get("finishReason") == "error":
                diagnostics["finish_reason_error_count"] += 1

    diagnostics["duplicate_error_event_count"] = sum(n - 1 for n in signatures.values() if n > 1)
    routes = {model for model in call_routes if model is not None}
    diagnostics["model_route_count"] = len(routes)
    error_model_counts = _attribute_models(routes, llm_error_count, diagnostics)
    total_call_count, model_call_counts = _count_model_calls(call_routes, diagnostics)
    return _result(
        "research",
        trace_id,
        llm_error_count,
        class_counts,
        error_model_counts,
        total_call_count,
        model_call_counts,
        diagnostics,
        None,
    )


def _model_route(info: Mapping[str, object]) -> str | None:
    """从 message.updated.info 提取模型标识(modelID)。

    兼容两种互斥形态:顶层 `info.modelID`,与 `info.model` 字典内嵌 `modelID`。
    providerID 自 1.2.0 起不再参与归因——实勘模型名与 provider 一一对应,按
    provider 分组只会把一个模型拆成多行;模型标识缺失或空即无路由证据,不猜测。
    """

    model = _text(info.get("modelID"))
    nested = info.get("model")
    if model is None and isinstance(nested, Mapping):
        model = _text(nested.get("modelID"))
    return model


def _count_model_calls(
    call_routes: Sequence[str | None],
    diagnostics: LlmErrorDiagnostics,
) -> tuple[int, list[LlmModelCallCount]]:
    """返回 (总调用数, 按模型的调用分项)。

    分母不因归因失败而缩小:总调用数是全部调用槽位数,而分项之和可能小于它,
    差额即 `model_call_unattributed_count`(该次调用没有路由证据)。
    """

    counts: dict[str, int] = {}
    unattributed = 0
    for model in call_routes:
        if model is None:
            unattributed += 1
            continue
        counts[model] = counts.get(model, 0) + 1
    diagnostics["model_call_unattributed_count"] = unattributed
    return (
        len(call_routes),
        [
            {"model": model, "count": count}
            for model, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    )


def _attribute_models(
    routes: set[str],
    llm_error_count: int,
    diagnostics: LlmErrorDiagnostics,
) -> list[LlmErrorModelCount]:
    """单模型回合把全部错误归因到该模型;多模型/无模型不猜测,进诊断。"""

    if llm_error_count == 0 or len(routes) != 1:
        diagnostics["model_unattributed_error_count"] = llm_error_count
        return []
    model = next(iter(routes))
    return [{"model": model, "count": llm_error_count}]


def _error_match_text(event: Mapping[str, object]) -> str:
    """提取用于分类匹配的错误文本;对象形态只取 name + message。

    stack 与 url 被显式排除:stack 可能携带本地路径,url 携带供应商端点,
    二者既不进入匹配上下文也不进入任何输出(v1 全部特征在 name/message 内可命中,
    已用真实样本 9/9 验证)。
    """

    error = event.get("error")
    if not isinstance(error, Mapping):
        return ""
    value = error.get("value")
    if not isinstance(value, Mapping):
        return ""
    inner = value.get("error")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, Mapping):
        parts = [
            inner[key]
            for key in ("name", "message")
            if isinstance(inner.get(key), str) and inner[key]
        ]
        return " | ".join(parts)
    return ""


def _classify_error(text: str) -> str:
    lowered = text.lower()
    for error_class, features in _ERROR_CLASS_RULES_V1:
        for feature in features:
            if feature.lower() in lowered:
                return error_class
    return UNCLASSIFIED_ERROR_CLASS


def _is_loop_value(loop: object) -> bool:
    return isinstance(loop, int) and not isinstance(loop, bool)


def _loop_key(loop: object) -> object:
    if _is_loop_value(loop):
        return ("value", loop)
    return ("missing", None)


def _zero_diagnostics() -> LlmErrorDiagnostics:
    return {
        "duplicate_error_event_count": 0,
        "message_error_projection_count": 0,
        "finish_reason_error_count": 0,
        "loop_missing_count": 0,
        "unclassified_count": 0,
        "model_route_count": 0,
        "model_unattributed_error_count": 0,
        "model_call_unattributed_count": 0,
        "missing_message_id_count": 0,
        "conflicting_model_route_count": 0,
        "non_assistant_routed_message_count": 0,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    diagnostics: LlmErrorDiagnostics | None = None,
) -> NormalizedLlmErrorTrace:
    return _result(
        mode, trace_id, None, None, None, None, None, diagnostics or _zero_diagnostics(), error
    )


def _result(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    llm_error_count: int | None,
    error_class_counts: Mapping[str, int] | None,
    error_model_counts: Sequence[LlmErrorModelCount] | None,
    total_model_call_count: int | None,
    model_call_counts: Sequence[LlmModelCallCount] | None,
    diagnostics: LlmErrorDiagnostics,
    error: str | None,
) -> NormalizedLlmErrorTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "llm_error_count": llm_error_count,
        "error_class_counts": error_class_counts,
        "error_model_counts": error_model_counts,
        "total_model_call_count": total_model_call_count,
        "model_call_counts": model_call_counts,
        "diagnostics": diagnostics,
        "normalization_error": error,
    }


def _trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
