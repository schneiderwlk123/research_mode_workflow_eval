"""规范化聊天与研究模式的框架 API 重试证据(稳定性维度 7.8)。

口径声明(《初步入库评测指标.md》7.8):本模块计量工具内部传输/业务层的框架
自动重试(`tool.debug.debug.apiCalls` 的 requestId 尾序号),不是埋点 D 定义的
模型级重试(retry_of_call_id 决策链);两者语义不同,禁止互相代理。

聊天 canvas 的 tool_result 无 API 调用明细通道,通道不存在不等于无重试,固定
返回不可计算(禁止记 0);结构非法才是 ERROR(与 7.4/7.6 同一契约边界)。

研究模式消费入口层已合并的单逻辑回合事件序列(与 7.4/7.6 同一信任契约):

- 重试证据(fingerprint_dup_v2,2026-09-23 需求方确认):2026-09-23 全量实勘发现
  requestId 尾序号 `-N` 是"同一 callID 内第 N 个 API 请求"的序号而非重试计数
  (83.8% 相邻请求入参不同,属工作流步骤)。故重试改为:同一 (callID, 入参指纹)
  重复出现,组内次数-1 计为重试;指纹 = md5(规范化入参,剔除 requestId/
  messageId/sessionId/ownerId)。缺 callID 的请求只进分母不进分子。
  本指标计量工具内部传输/业务层的重复请求(重试上界),不是埋点 D 的模型级重试;
  `retry_semantics`(fingerprint_dup_v2 / no_tool_debug_channel)随元数据声明。
- 去重键为 `(callID, requestId)`: 同一 API 调用的重复投影只计一次,进重复
  诊断(全仓重复投影去重范式;2026-09-17 样本实测 573 条原始 / 572 唯一)。
- 业务错误字面口径: `response` 为对象 ∧ 携带 `code` 键 ∧ code ∉ 成功集
  {0, "0", 200, "200", None, ""} ∧ `success` 不为 true;response 非对象或
  缺 code 键不进分子,计入 undeterminable 诊断,不估算。
- 覆盖率 = 有 tool.debug 的 callID ∩ 工具 callID ÷ 全部唯一工具 callID,
  必须随结果输出(实勘覆盖率仅约三成,解读受限);孤立 debug(无对应工具
  part)不进分子单独诊断;无工具 part 时分母为空,覆盖率为 None 不猜测。
- tool.debug 事件存在但全部 apiCalls 为空 → no_observable_api_calls;
  完全无 tool.debug 事件 → no_tool_debug_events,两级不可计算原因码区分。

结果只保留计数、比率输入与安全诊断,不保留 endpoint、请求体或响应正文。
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

# requestId 尾序号: `req-<uuid>-<N>` 的 N;无尾序号即畸形,不可判定重试。
_REQUEST_ID_SUFFIX = re.compile(r"-(\d+)$")

# 业务错误成功集(权威口径 7.8 字面枚举):code 命中即视为非业务错误。
_SUCCESS_CODES = frozenset({0, "0", 200, "200", None, ""})

# 计算入参指纹时剔除的易变键:这些键标识"这一次请求/会话",不参与"是否同一请求"判定。
_VOLATILE_REQUEST_KEYS = frozenset({"requestId", "messageId", "sessionId", "ownerId"})


def _request_fingerprint(endpoint: object, request: object) -> str | None:
    """对单次 API 请求的 (endpoint + 规范化入参) 做指纹。

    2026-09-23 全量实勘确认:requestId 尾序号是"同一 callID 内第 N 个 API 请求"
    的序号,并非重试计数;真正的重试表现为同一 (callID, endpoint+入参) 重复出现。
    指纹取 endpoint + 业务入参(剔除易变键),用于把"重复的同一请求"与
    "工作流的不同步骤"区分开;endpoint 必须入指纹,否则同入参不同接口会误判重复。
    """
    if not isinstance(request, Mapping):
        return None
    payload = {k: v for k, v in request.items() if k not in _VOLATILE_REQUEST_KEYS}
    payload["__endpoint__"] = endpoint
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


class ApiRetryDiagnostics(TypedDict):
    """框架 API 重试判定的安全诊断计数;聊天模式各项恒为 0。"""

    duplicate_api_call_projection_count: int
    malformed_request_id_count: int
    undeterminable_api_count: int
    orphan_tool_debug_count: int
    missing_debug_call_id_count: int
    malformed_tool_part_count: int


class NormalizedApiRetryTrace(TypedDict):
    """供框架 API 重试率评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    api_call_count: int | None
    api_retry_count: int | None
    api_business_error_count: int | None
    business_error_codes: tuple[tuple[str, int], ...]
    tool_call_id_count: int | None
    debug_call_id_count: int | None
    api_debug_coverage: float | None
    api_request_depth_max: int | None
    api_retry_attribution: dict[str, object] | None
    diagnostics: ApiRetryDiagnostics
    normalization_error: str | None


def normalize_chat_api_retry(trace: Mapping[str, object]) -> NormalizedApiRetryTrace:
    """聊天模式固定不可计算: tool_result 无 API 调用明细通道。

    canvas 结构非法才是 ERROR;结构合法即返回 no_tool_debug_events——通道
    不存在不等于无重试,禁止记 0(与 7.4/7.6 聊天侧同一契约边界)。
    """

    canvas = trace.get("canvas") if isinstance(trace, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(canvas.get("meta"), Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    trace_id = _text(canvas["meta"].get("traceId"))
    return _empty("chat", trace_id, "no_tool_debug_events")


def normalize_research_api_retry(events: Sequence[object]) -> NormalizedApiRetryTrace:
    """统计单个合并逻辑回合内 tool.debug apiCalls 的重试与业务错误证据。

    信任入口层单回合契约(回合切分与续跑合并是入口层职责);trace_id 尽力
    从 turn.start 提取,缺失时回退事件级 sessionID。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    diagnostics = _zero_diagnostics()
    full_trace_id: str | None = None
    fallback_trace_id: str | None = None
    debug_event_count = 0
    debug_call_ids: set[str] = set()
    tool_call_ids: set[str] = set()
    seen_api_calls: set[tuple[str, str]] = set()
    repeat_groups: Counter[tuple[str, str]] = Counter()
    group_seq: dict[tuple[str, str], list[bool]] = {}
    group_meta: dict[tuple[str, str], tuple[str | None, object]] = {}
    depth_by_call: Counter[str] = Counter()
    api_call_count = 0
    api_business_error_count = 0
    code_counter: Counter[str] = Counter()

    # 预扫一遍建立 callID -> 工具名映射:tool.debug 与 message.part.updated 的先后
    # 顺序不固定,若在主循环里边扫边用会漏映射,故先单独扫一次。
    call_to_tool: dict[str, str] = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        cid, tool = _text(part.get("callID")), _text(part.get("tool"))
        if cid is not None and tool is not None:
            call_to_tool.setdefault(cid, tool)

    for event in events:
        if not isinstance(event, Mapping):
            continue
        session_id = _text(event.get("sessionID"))
        if session_id is not None and fallback_trace_id is None:
            fallback_trace_id = session_id
        event_type = event.get("type")
        if event_type == "turn.start" and full_trace_id is None:
            full_trace_id = _trace_id(session_id, _text(event.get("userMessageID")))
        elif event_type == "tool.debug":
            debug_event_count += 1
            call_id = _text(event.get("callID"))
            if call_id is None:
                # 缺 callID 无法参与覆盖率归属,apiCalls 照常进入主值。
                diagnostics["missing_debug_call_id_count"] += 1
            else:
                debug_call_ids.add(call_id)
            debug = event.get("debug")
            api_calls = debug.get("apiCalls") if isinstance(debug, Mapping) else None
            if not _is_sequence(api_calls):
                continue
            for entry in api_calls:
                if not isinstance(entry, Mapping):
                    continue
                request = entry.get("request")
                request_id = (
                    _text(request.get("requestId")) if isinstance(request, Mapping) else None
                )
                suffix = _request_id_suffix(request_id)
                if request_id is None or suffix is None:
                    # 畸形 requestId: 无去重键,逐条独立计数;指纹仍可用于重复判定。
                    diagnostics["malformed_request_id_count"] += 1
                else:
                    dedup_key = (call_id or "", request_id)
                    if dedup_key in seen_api_calls:
                        diagnostics["duplicate_api_call_projection_count"] += 1
                        continue
                    seen_api_calls.add(dedup_key)
                    if call_id is not None:
                        depth_by_call[call_id] = max(depth_by_call[call_id], suffix)
                api_call_count += 1
                outcome, code = _business_outcome(entry.get("response"))
                if outcome == "undeterminable":
                    diagnostics["undeterminable_api_count"] += 1
                elif outcome == "error":
                    api_business_error_count += 1
                    assert code is not None  # noqa: S101 - outcome 为 error 时必有 code
                    code_counter[code] += 1
                # 重试判定(fingerprint_dup_v2,2026-09-23 确认):同一 (callID, 入参指纹)
                # 重复出现才算重试;尾序号仅是"调用内第 N 个请求"的序号,不再单独判定。
                fingerprint = _request_fingerprint(entry.get("endpoint"), request)
                if call_id is not None and fingerprint is not None:
                    # 有 callID 才能限定"同一次工具调用内的重复请求";缺 callID 的请求
                    # 只进分母、不进分子(无法确认是否同一次调用的重发),事件级
                    # missing_debug_call_id_count 已在上面计过一次,此处不重复累加。
                    repeat_groups[(call_id, fingerprint)] += 1
                    # 归因(仅框架层):记录组内出现顺序的成功标记与工具/endpoint,
                    # 供 by_tool / by_endpoint / retry_after_success 汇总。
                    group_seq.setdefault((call_id, fingerprint), []).append(outcome == "ok")
                    group_meta[(call_id, fingerprint)] = (
                        call_to_tool.get(call_id),
                        entry.get("endpoint"),
                    )
        elif event_type == "message.part.updated":
            part = event.get("part")
            if not isinstance(part, Mapping) or part.get("type") != "tool":
                continue
            part_call_id = _text(part.get("callID"))
            if part_call_id is None:
                # 缺 callID 的工具 part 无法进入覆盖率分母,分流诊断不阻断。
                diagnostics["malformed_tool_part_count"] += 1
            else:
                tool_call_ids.add(part_call_id)

    trace_id = full_trace_id or fallback_trace_id
    if debug_event_count == 0:
        return _empty("research", trace_id, "no_tool_debug_events")

    covered = debug_call_ids & tool_call_ids
    diagnostics["orphan_tool_debug_count"] = len(debug_call_ids - tool_call_ids)
    coverage = len(covered) / len(tool_call_ids) if tool_call_ids else None

    if api_call_count == 0:
        result = _empty("research", trace_id, "no_observable_api_calls", diagnostics)
        result["debug_call_id_count"] = len(debug_call_ids)
        return result

    # 重试数 = 各组(次数-1)之和:组内第 1 次是原始请求,其后每次同指纹重复算一次重试。
    api_retry_count = sum(max(0, c - 1) for c in repeat_groups.values())
    api_request_depth_max = max(depth_by_call.values()) if depth_by_call else None

    # 框架层归因(2026-09-24 需求方确认:只要框架层,不要模型层跨 callID):
    # by_tool / by_endpoint 按"重复次数"归到工具与接口;retry_after_success 统计
    # 组内"前一次已成功、后一次仍重发"的次数,量化框架对已成功请求的无效重发。
    by_tool: Counter[str] = Counter()
    by_endpoint: Counter[str] = Counter()
    retry_after_success = 0
    for key, seq in group_seq.items():
        repeats = len(seq) - 1
        if repeats <= 0:
            continue
        tool, endpoint = group_meta.get(key, (None, None))
        by_tool[tool or "<unmapped>"] += repeats
        by_endpoint[str(endpoint)] += repeats
        retry_after_success += sum(1 for i in range(1, len(seq)) if seq[i - 1])
    api_retry_attribution = {
        "by_tool": dict(by_tool),
        "by_endpoint": dict(by_endpoint),
        "retry_after_success": retry_after_success,
    }

    return {
        "trace_id": trace_id,
        "mode": "research",
        "api_call_count": api_call_count,
        "api_retry_count": api_retry_count,
        "api_business_error_count": api_business_error_count,
        "business_error_codes": tuple(sorted(code_counter.items())),
        "tool_call_id_count": len(tool_call_ids),
        "debug_call_id_count": len(debug_call_ids),
        "api_debug_coverage": coverage,
        "api_request_depth_max": api_request_depth_max,
        "api_retry_attribution": api_retry_attribution,
        "diagnostics": diagnostics,
        "normalization_error": None,
    }


def _business_outcome(response: object) -> tuple[str, str | None]:
    """按字面口径分类业务结果: 返回 (error/ok/undeterminable, 规范化错误码)。

    2026-09-23 全量实勘修正:成功的响应**不带 code 键**,直接返回业务载荷
    (如 {docNodeId, fullPath, name});只有失败响应才带 code + message。因此
    "response 是对象但缺 code 键" 判为 ok(而非旧版的 undeterminable);
    response 非对象(如 list/None)才是 undeterminable(不估算)。
    code 命中成功集 {0,"0",200,"200",None,""} 或 success 为 true → ok;其余 → error。
    """

    if not isinstance(response, Mapping):
        return "undeterminable", None
    if "code" not in response:
        return "ok", None
    code = response["code"]
    if code in _SUCCESS_CODES or response.get("success") is True:
        return "ok", None
    return "error", str(code)


def _request_id_suffix(request_id: str | None) -> int | None:
    """提取 requestId 尾序号;缺失或无尾序号返回 None(不可判定重试)。"""

    if request_id is None:
        return None
    match = _REQUEST_ID_SUFFIX.search(request_id)
    return int(match.group(1)) if match else None


def _zero_diagnostics() -> ApiRetryDiagnostics:
    return {
        "duplicate_api_call_projection_count": 0,
        "malformed_request_id_count": 0,
        "undeterminable_api_count": 0,
        "orphan_tool_debug_count": 0,
        "missing_debug_call_id_count": 0,
        "malformed_tool_part_count": 0,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    diagnostics: ApiRetryDiagnostics | None = None,
) -> NormalizedApiRetryTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "api_call_count": None,
        "api_retry_count": None,
        "api_business_error_count": None,
        "business_error_codes": (),
        "tool_call_id_count": None,
        "debug_call_id_count": None,
        "api_debug_coverage": None,
        "api_request_depth_max": None,
        "api_retry_attribution": None,
        "diagnostics": diagnostics or _zero_diagnostics(),
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
