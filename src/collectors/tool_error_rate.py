"""规范化双模式工具错误终态与归因目录 v1 计数,不保留错误正文或载荷。

业务规则(《初步入库评测指标.md》7.7):
- 聊天模式为载荷代理口径(`payload_proxy_v1`):没有显式终态字段,失败由
  `tool_result` 载荷的业务失败信号推断;分类依次检查嵌套 `tool_result` 信封、
  JSON 字符串解码、顶层结构化列表与入口级 `success/error` 字段(实勘:失败与
  降级信号常在 `{"tool_result": {...}, "doc_id": ...}` 内层,"Tool not found"
  类失败只有入口级信号无载荷)。纯散文等不可解析载荷不估算,只进
  `undeterminable_payload_count` 诊断且不进分母。埋点 C 落地后须对齐升版。
- 研究模式为显式终态口径(`explicit_status_v1`):`part.state.status == 'error'`
  即失败终态,`completed` 即成功终态,`pending/running` 无终态只进诊断。
- 硬边界:`error_code`/`errorcode` 为空串或缺失 = 成功;负向断言不得把空值判为
  失败(实勘中 41 条 `error_code: ""` 的成功结果曾被初扫误判;该字段实际深埋
  `debug_trace.nl2sql.nodes[]`,分类只看载荷顶层与一级信封,天然不触碰)。
- `code: 1` + "校验说明…不受支持/无法完整回答" 为业务自检降级,分流在失败信号
  之前:计入分母、不入硬失败分子;`code: 2` 异步"执行中"回执无失败信号,成功。
- 归因目录 v1 为纯确定性文本规则,未命中记 `unclassified`,只诊断不猜测;
  错误正文只在内存中流转,规范化结果仅保留类别计数。
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

ATTRIBUTION_CATALOG_VERSION = "tool_error_attribution_v1"
FAILURE_SIGNAL_RULES_VERSION = "chat_failure_signals_v1"
CHAT_TERMINAL_SEMANTICS = "payload_proxy_v1"
RESEARCH_TERMINAL_SEMANTICS = "explicit_status_v1"

TerminalState = Literal["success", "error", "degraded"]
Attribution = Literal["model_misuse", "infrastructure_failure", "data_availability", "unclassified"]


def _compact_pattern(text: str) -> str:
    """归因匹配用的文本紧凑化:去全部空白并小写,吸收大小写与空白差异。"""

    return re.sub(r"\s+", "", text).lower()


# 归因目录 v1:按 (类别 -> 紧凑化子串) 有序匹配,先命中先得。子串已做去空白与
# 小写紧凑化(与 classify_tool_error 的文本规范化一致),目录升级须同步升版本。
_ATTRIBUTION_RULES: tuple[tuple[Attribution, tuple[str, ...]], ...] = (
    (
        "model_misuse",
        (
            _compact_pattern("Found multiple matches for oldString"),
            _compact_pattern("INPUT_INTERRUPTED"),
            _compact_pattern("需要非空的 ops 数组"),
            _compact_pattern("Path must be workspace-relative"),
            _compact_pattern("Access denied outside mounted tool roots"),
            _compact_pattern("参数最多支持"),
        ),
    ),
    (
        "infrastructure_failure",
        (
            _compact_pattern("MCP error -32001"),
            _compact_pattern("Request timed out"),
            _compact_pattern("socket connection was closed"),
            _compact_pattern("Document API request timed out"),
            _compact_pattern("所有年报提取均失败"),
        ),
    ),
    (
        "data_availability",
        (
            _compact_pattern("未找到相关年报数据"),
            _compact_pattern("已被删除或移动"),
        ),
    ),
)

# 归因目录 v1 窄正则补充(2026-09-21 需求方确认补入):"Tool '<名称>' not found"
# = 模型调用不存在的工具,属模型侧误用;裸 "not found" 子串会误伤数据侧英文
# 文案(如 "file not found"),故限定 tool'<引号名>'notfound 的完整紧凑形态。
_ATTRIBUTION_REGEX_RULES: dict[Attribution, tuple[re.Pattern[str], ...]] = {
    "model_misuse": (re.compile(r"tool'[^']{1,120}'notfound"),),
}

# 业务错误码 11410("该文件/文件夹已被删除或移动")比文本规则更具体,优先判定。
_DATA_AVAILABILITY_CODES = frozenset({"11410"})

_CHAT_FAILURE_STATUSES = frozenset({"failed", "timeout", "timed_out", "error"})
_CHAT_SUCCESS_CODES = frozenset({"0", "200"})
_CHAT_AGENT_RETURN_TOOL_NAMES = frozenset({"respond"})


class NormalizedToolErrorCall(TypedDict):
    """单个唯一调用的终态与归因,不包含参数、响应或错误正文。"""

    call_id: str
    tool_name: str
    terminal_state: TerminalState
    attribution: Attribution | None


class ToolErrorRateDiagnostics(TypedDict):
    """可聚合但不泄露调用正文的诊断计数。"""

    model_misuse_error_count: int
    infrastructure_error_count: int
    data_availability_error_count: int
    unclassified_error_count: int
    degraded_result_count: int
    terminal_missing_count: int
    undeterminable_payload_count: int
    duplicate_observation_count: int
    orphan_result_count: int
    conflicting_terminal_state_count: int
    excluded_agent_delegation_count: int
    excluded_agent_return_count: int
    malformed_event_count: int


class NormalizedToolErrorRateTrace(TypedDict):
    """供工具错误率评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    terminal_semantics: str
    attribution_catalog_version: str
    failure_signal_rules_version: str | None
    tool_calls: tuple[NormalizedToolErrorCall, ...]
    diagnostics: ToolErrorRateDiagnostics
    normalization_error: str | None


def classify_tool_error(text: str | None, *, business_code: object = None) -> Attribution:
    """按归因目录 v1 对错误文本分类;未命中记 unclassified,不猜测。

    输入:错误正文(仅在内存流转)与可选业务错误码。输出:稳定归因类别字符串。
    匹配对文本做去空白与小写紧凑化,吸收大小写与空白差异;业务码 11410 优先。
    """

    code = business_code
    if isinstance(code, int) and not isinstance(code, bool):
        code = str(code)
    if isinstance(code, str) and code.strip() in _DATA_AVAILABILITY_CODES:
        return "data_availability"
    compact = _compact_pattern(text or "")
    for category, patterns in _ATTRIBUTION_RULES:
        if any(pattern and pattern in compact for pattern in patterns):
            return category
        if any(regex.search(compact) for regex in _ATTRIBUTION_REGEX_RULES.get(category, ())):
            return category
    return "unclassified"


def normalize_chat_tool_error_rate(
    trace: Mapping[str, object],
) -> NormalizedToolErrorRateTrace:
    """按新版 canvas 的 `traceId + toolId` 关联请求与结果并做载荷代理分类。

    排除子 Agent 委派、`respond` 返回与孤立结果;同一调用多个结果按最劣终态
    归并(error > degraded > success),观察分歧只进诊断。旧版聊天结构不再支持。
    """

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _empty("chat", None, "missing_trace_id", malformed=1)
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)

    diagnostics = _zero_diagnostics()
    definitions: dict[str, str] = {}
    observations: dict[str, list[Mapping[str, object]]] = {}
    structural_error: str | None = None

    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            diagnostics["malformed_event_count"] += 1
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            diagnostics["malformed_event_count"] += 1
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                diagnostics["malformed_event_count"] += 1
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                diagnostics["malformed_event_count"] += 1
                continue

            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                diagnostics["malformed_event_count"] += 1
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    diagnostics["malformed_event_count"] += 1
                    continue
                call_id = _text(raw_call.get("toolId"))
                tool_name = _text(raw_call.get("toolName"))
                agent_name = _text(raw_call.get("agentName"))
                if call_id is None and tool_name is None and agent_name is not None:
                    # 只有 agentName 的对象是子 Agent 委派,不构成普通工具请求。
                    diagnostics["excluded_agent_delegation_count"] += 1
                    continue
                if call_id is None:
                    return _empty("chat", trace_id, "missing_call_id", source=diagnostics)
                if tool_name is None:
                    return _empty("chat", trace_id, "missing_tool_name", source=diagnostics)
                if tool_name.casefold() in _CHAT_AGENT_RETURN_TOOL_NAMES:
                    if call_id not in definitions:
                        diagnostics["excluded_agent_return_count"] += 1
                    continue
                existing = definitions.get(call_id)
                if existing is not None:
                    diagnostics["duplicate_observation_count"] += 1
                    if existing != tool_name:
                        return _empty(
                            "chat", trace_id, "conflicting_call_definition", source=diagnostics
                        )
                    continue
                definitions[call_id] = tool_name

            raw_results = content.get("tool_result", ())
            if raw_results is None:
                raw_results = ()
            if not _is_sequence(raw_results):
                diagnostics["malformed_event_count"] += 1
                continue
            for raw_result in raw_results:
                if not isinstance(raw_result, Mapping):
                    diagnostics["malformed_event_count"] += 1
                    continue
                call_id = _text(raw_result.get("toolId"))
                if call_id is None or call_id not in definitions:
                    # 孤立结果(含 respond 返回与无 ID 载荷)不进分子分母。
                    diagnostics["orphan_result_count"] += 1
                    continue
                result_tool_name = _text(raw_result.get("toolName"))
                if result_tool_name is not None and result_tool_name != definitions[call_id]:
                    structural_error = structural_error or "conflicting_call_definition"
                    continue
                # 保留整个结果条目:入口级 success/error 与载荷都参与终态分类。
                observations.setdefault(call_id, []).append(raw_result)

    if diagnostics["malformed_event_count"]:
        return _empty("chat", trace_id, "invalid_chat_trace_structure", source=diagnostics)
    if structural_error is not None:
        return _empty("chat", trace_id, structural_error, source=diagnostics)

    calls: list[NormalizedToolErrorCall] = []
    for call_id, tool_name in definitions.items():
        entries = observations.get(call_id, [])
        if not entries:
            # 无关联结果 = 载荷代理下无终态,不进分母。
            diagnostics["terminal_missing_count"] += 1
            continue
        if len(entries) > 1:
            # 同一调用的额外结果观察只进诊断,终态按最劣归并。
            diagnostics["duplicate_observation_count"] += len(entries) - 1
        classified = [_classify_chat_observation(entry) for entry in entries]
        for state, _, _ in classified:
            if state is None:
                # 纯字符串等不可解析载荷:只诊断,不估算终态。
                diagnostics["undeterminable_payload_count"] += 1
        determined = [item for item in classified if item[0] is not None]
        if not determined:
            diagnostics["terminal_missing_count"] += 1
            continue
        states = [item[0] for item in determined]
        if len(set(states)) > 1:
            diagnostics["conflicting_terminal_state_count"] += 1
        terminal_state: TerminalState = (
            "error" if "error" in states else "degraded" if "degraded" in states else "success"
        )
        attribution: Attribution | None = None
        if terminal_state == "error":
            _, error_text, business_code = next(item for item in determined if item[0] == "error")
            attribution = classify_tool_error(error_text, business_code=business_code)
        calls.append(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "terminal_state": terminal_state,
                "attribution": attribution,
            }
        )

    return _finalize("chat", trace_id, calls, diagnostics)


def normalize_research_tool_error_rate(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolErrorRateTrace:
    """按 `callID` 合并研究工具生命周期并以显式 status 判定终态。

    输入契约:`events` 必须是同一回合按时间序合并去重后的事件流,回合切分由
    入口层负责(与锚定率/回合完成率一致)。`error` 终态优先于 `completed`,
    两者并存只进冲突诊断;错误文本取首个非空 `state.error`,仅用于内存归因。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure", malformed=1)
    resolved_session_id = _text(session_id)
    resolved_message_id = _text(user_message_id)
    diagnostics = _zero_diagnostics()
    definitions: dict[str, str] = {}
    statuses: dict[str, set[str]] = {}
    error_texts: dict[str, str] = {}

    for event in events:
        if not isinstance(event, Mapping):
            diagnostics["malformed_event_count"] += 1
            continue
        resolved_session_id = resolved_session_id or _text(event.get("sessionID"))
        resolved_message_id = resolved_message_id or _text(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            diagnostics["malformed_event_count"] += 1
            continue
        if part.get("type") != "tool":
            continue
        call_id = _text(part.get("callID"))
        trace_id = _research_trace_id(resolved_session_id, resolved_message_id)
        if call_id is None:
            return _empty("research", trace_id, "missing_call_id", source=diagnostics)
        tool_name = _text(part.get("tool"))
        previous = definitions.get(call_id)
        if tool_name is None and previous is None:
            return _empty("research", trace_id, "missing_tool_name", source=diagnostics)
        if tool_name is not None and previous is not None and previous != tool_name:
            return _empty("research", trace_id, "conflicting_call_definition", source=diagnostics)
        if tool_name is not None and previous is None:
            definitions[call_id] = tool_name
        statuses.setdefault(call_id, set())

        state = part.get("state")
        if not isinstance(state, Mapping):
            diagnostics["malformed_event_count"] += 1
            continue
        status = _normalized_status(state.get("status"))
        if status is None:
            diagnostics["malformed_event_count"] += 1
            continue
        observed = statuses[call_id]
        if status in observed:
            diagnostics["duplicate_observation_count"] += 1
        else:
            observed.add(status)
        if status == "error" and call_id not in error_texts:
            error_text = _text(state.get("error"))
            if error_text is not None:
                error_texts[call_id] = error_text

    trace_id = _research_trace_id(resolved_session_id, resolved_message_id)
    calls: list[NormalizedToolErrorCall] = []
    for call_id in definitions:
        tool_name = definitions[call_id]
        observed = statuses.get(call_id, set())
        if "error" in observed:
            if "completed" in observed:
                diagnostics["conflicting_terminal_state_count"] += 1
            calls.append(
                {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "terminal_state": "error",
                    "attribution": classify_tool_error(error_texts.get(call_id)),
                }
            )
        elif "completed" in observed:
            calls.append(
                {
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "terminal_state": "success",
                    "attribution": None,
                }
            )
        else:
            # pending/running 等无终态状态不进分母,只进诊断。
            diagnostics["terminal_missing_count"] += 1
    return _finalize("research", trace_id, calls, diagnostics)


def _finalize(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    calls: list[NormalizedToolErrorCall],
    diagnostics: ToolErrorRateDiagnostics,
) -> NormalizedToolErrorRateTrace:
    """汇总归因计数并推导 Trace 级不可计算原因。"""

    for call in calls:
        attribution = call["attribution"]
        if call["terminal_state"] == "error" and attribution is not None:
            if attribution == "model_misuse":
                diagnostics["model_misuse_error_count"] += 1
            elif attribution == "infrastructure_failure":
                diagnostics["infrastructure_error_count"] += 1
            elif attribution == "data_availability":
                diagnostics["data_availability_error_count"] += 1
            else:
                diagnostics["unclassified_error_count"] += 1
        elif call["terminal_state"] == "degraded":
            diagnostics["degraded_result_count"] += 1

    error: str | None = None
    if not calls:
        error = (
            "no_terminal_tool_calls"
            if diagnostics["terminal_missing_count"] or diagnostics["undeterminable_payload_count"]
            else "no_tool_calls"
        )
    return _build(mode, trace_id, tuple(calls), diagnostics, error)


def _build(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    calls: tuple[NormalizedToolErrorCall, ...],
    diagnostics: ToolErrorRateDiagnostics,
    error: str | None,
) -> NormalizedToolErrorRateTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "terminal_semantics": (
            CHAT_TERMINAL_SEMANTICS if mode == "chat" else RESEARCH_TERMINAL_SEMANTICS
        ),
        "attribution_catalog_version": ATTRIBUTION_CATALOG_VERSION,
        "failure_signal_rules_version": (FAILURE_SIGNAL_RULES_VERSION if mode == "chat" else None),
        "tool_calls": calls,
        "diagnostics": diagnostics,
        "normalization_error": error,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    *,
    malformed: int = 0,
    source: ToolErrorRateDiagnostics | None = None,
) -> NormalizedToolErrorRateTrace:
    if source is not None:
        diagnostics = source
    else:
        diagnostics = _zero_diagnostics()
        diagnostics["malformed_event_count"] += malformed
    return _build(mode, trace_id, (), diagnostics, error)


def _zero_diagnostics() -> ToolErrorRateDiagnostics:
    return {
        "model_misuse_error_count": 0,
        "infrastructure_error_count": 0,
        "data_availability_error_count": 0,
        "unclassified_error_count": 0,
        "degraded_result_count": 0,
        "terminal_missing_count": 0,
        "undeterminable_payload_count": 0,
        "duplicate_observation_count": 0,
        "orphan_result_count": 0,
        "conflicting_terminal_state_count": 0,
        "excluded_agent_delegation_count": 0,
        "excluded_agent_return_count": 0,
        "malformed_event_count": 0,
    }


def _classify_chat_observation(
    entry: Mapping[str, object],
) -> tuple[TerminalState | None, str | None, object]:
    """单个结果条目的载荷代理分类,返回 (终态, 归因文本, 业务码)。

    分类顺序(确定性,先命中先得):
    1. 降级分流:`code: 1` + 自检"校验说明"文本,即使携带失败信号也不入硬失败;
    2. 映射载荷(含嵌套 `tool_result` 信封与 JSON 字符串解码)的业务失败信号;
    3. 入口级 `success: false`(如 "Tool not found",可无载荷),错误文本取
       entry.error;
    4. 可解析载荷(映射、结构化列表、JSON 字符串)或入口级 `success: true`
       且无载荷 → 成功;`code: 2` 异步"执行中"回执无失败信号,按成功处理;
    5. 其余(纯散文字符串、无载荷且无入口信号)→ None,进不可判定诊断。
    """

    payload = _first_non_empty(entry.get("result"), entry.get("rawResult"))
    candidates, structured = _payload_candidates(payload)
    if any(_is_degraded_payload(candidate) for candidate in candidates):
        return "degraded", None, None
    failed = next((candidate for candidate in candidates if _hits_failure_signal(candidate)), None)
    if failed is not None:
        return "error", _chat_error_text(payload, entry), failed.get("code")
    if entry.get("success") is False:
        return "error", _chat_error_text(payload, entry), None
    if candidates:
        return "success", None, None
    if _is_sequence(structured):
        # 顶层结构化列表(检索类工具的 chunk 数组)无失败信封,按成功终态处理。
        return "success", None, None
    if payload is None and entry.get("success") is True:
        return "success", None, None
    return None, None, None


def _payload_candidates(payload: object) -> tuple[list[Mapping[str, object]], object]:
    """展开映射候选(含嵌套 tool_result 信封与 JSON 字符串解码)与结构化载荷。

    返回 (映射候选列表, 结构化载荷);字符串载荷先尝试 JSON 解码,解码失败保持
    原样(散文字符串进不可判定)。`debug_trace` 等深层嵌套不展开——节点级
    `error_code` 不是调用级信号(实勘:空串节点码深埋 nl2sql debug_trace)。
    """

    decoded: object = None
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except ValueError:
            decoded = None
    source = payload if isinstance(payload, Mapping) else decoded
    candidates: list[Mapping[str, object]] = []
    if isinstance(source, Mapping):
        candidates.append(source)
        inner = source.get("tool_result")
        if isinstance(inner, Mapping):
            candidates.append(inner)
    structured = decoded if isinstance(payload, str) else payload
    return candidates, structured


def _chat_error_text(payload: object, entry: Mapping[str, object]) -> str | None:
    """拼接仅供内存归因匹配的错误文本;结果不保留该字符串。"""

    parts: list[str] = []
    entry_error = _text(entry.get("error"))
    if entry_error is not None:
        parts.append(entry_error)
    if isinstance(payload, Mapping):
        parts.append(_serialize(payload))
    elif _is_sequence(payload):
        parts.append(_serialize(payload))
    elif isinstance(payload, str) and payload.strip():
        parts.append(payload)
    return "\n".join(parts) or None


def _is_degraded_payload(payload: Mapping[str, object]) -> bool:
    """业务 `code: 1` 且自检说明声明不完整(nl2sql 类降级),不算硬失败。"""

    code = payload.get("code")
    is_degraded_code = (isinstance(code, int) and not isinstance(code, bool) and code == 1) or (
        isinstance(code, str) and code.strip() == "1"
    )
    if not is_degraded_code:
        return False
    text = _serialize(payload)
    return "校验说明" in text and ("不受支持" in text or "无法完整回答" in text)


def _hits_failure_signal(payload: Mapping[str, object]) -> bool:
    """聊天业务失败信号 v1 + 7.7 扩展:status 增加 error,空串错误码为成功。"""

    for key in ("errorcode", "error_code"):
        raw = payload.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, str):
            code = raw.strip()
            # 硬边界:空串或缺失 = 成功,不得触发负向断言。
            if code and code not in _CHAT_SUCCESS_CODES:
                return True
        elif isinstance(raw, (int, float)) and raw not in {0, 200}:
            return True
    if payload.get("success") is False:
        return True
    status = _normalized_status(payload.get("status"))
    if status is not None and status in _CHAT_FAILURE_STATUSES:
        return True
    return False


def _serialize(payload: object) -> str:
    """把载荷序列化为仅供内存归因匹配的文本;结果不保留该字符串。"""

    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(payload)


def _first_non_empty(*candidates: object) -> object:
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str) and not candidate.strip():
            continue
        return candidate
    return None


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _normalized_status(value: object) -> str | None:
    text = _text(value)
    return text.lower() if text is not None else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
