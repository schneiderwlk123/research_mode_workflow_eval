"""计算最终回答数值 span 在成功证据载荷中的精确锚定计数。

业务规则：指标只判断"回答里的数字在本 Trace 成功证据载荷中是否有精确出处"，不做语义
判断、不判冲突。未命中的 span 属于推导或格式差异的可能，仅进入计数诊断，不判错
（单向包含口径）。证据侧与回答侧正文都只在内存中流转，规范化结果只保留聚合计数。
"""

import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TypedDict, TypeGuard

UNIT_CATALOG_VERSION = "grounding_units_v2"
NORMALIZATION_VERSION = "grounding_norm_v2"
FAILURE_SIGNAL_RULES_VERSION = "chat_failure_signals_v1"
RESEARCH_EXCLUDED_TOOLS_VERSION = "research_excluded_tools_v1"

# 单位目录 v2：单位 -> (单位类, 数值换算系数)。跨单位类（元 <-> 美元）永不换算。
_UNIT_SCALES: dict[str, tuple[str, Decimal]] = {
    "亿元": ("cny", Decimal(10) ** 8),
    "万元": ("cny", Decimal(10) ** 4),
    "亿美元": ("usd", Decimal(10) ** 8),
    "万美元": ("usd", Decimal(10) ** 4),
    "亿": ("cny", Decimal(10) ** 8),
    "万": ("cny", Decimal(10) ** 4),
    "元": ("cny", Decimal(1)),
    "美元": ("usd", Decimal(1)),
    "%": ("pct", Decimal(1)),
    "％": ("pct", Decimal(1)),
    "个百分点": ("pct", Decimal(1)),
    "倍": ("cnt", Decimal(1)),
    "家": ("cnt", Decimal(1)),
    "只": ("cnt", Decimal(1)),
    "人": ("cnt", Decimal(1)),
}
_UNIT_ALTERNATION = "|".join(_UNIT_SCALES)

# 货币前缀目录 v2：证据侧英文研报常用 "Rmb1247.68"、"$8.17" 书写；回答侧把同一数值写成
# "1,247.68元""8.17美元"。前缀键与单位类绑定，跨货币类（cny <-> usd）永不互转。
_CURRENCY_PREFIXES: dict[str, tuple[str, ...]] = {
    "cny": ("Rmb", "RMB", "rmb", "¥", "￥", "人民币"),
    "usd": ("$", "US$", "USD", "usd"),
}

# 英文数量级后缀目录 v2：小数倍率 -> 后缀集合；与货币前缀组合生成
# "Rmb1.7bn"（17亿元）、"Rmb543m"（5.43亿元）等匹配键。单字母后缀（B/m/T）只与
# 货币前缀组合出现，避免裸 "5m" 与米/分钟等歧义书写冲突；英文数量级只与货币类联动，
# 不生成裸 "1.7bn" 键——无货币语境的英文数字无法证明与元类 span 同币种。
_ENGLISH_MAGNITUDES: tuple[tuple[Decimal, tuple[str, ...]], ...] = (
    (Decimal(10) ** 9, ("bn", "bns", "B", "billion")),
    (Decimal(10) ** 6, ("mn", "mns", "million", "m", "M")),
    (Decimal(10) ** 12, ("tn", "trillion", "T")),
)

# 单位目录不含"年"，因此"2026年"这类纯年份天然不构成 span；年份排除规则由目录本身满足。
_SPAN_PATTERN = re.compile(r"(-?)(\d[\d,]*(?:\.\d+)?)\s*(" + _UNIT_ALTERNATION + ")")
# 证据侧 "12.15/2.35亿元" 与 "38337、10214、6589家" 组合数值需要在内存中展开为
# "12.15亿元2.35亿元" / "38337家10214家6589家" 才能命中单独书写的 span；该规则只作用于
# 证据文本，不改写回答正文。半角逗号已被千分位剥离吞掉，不能作为列举分隔符参与展开。
_COMBINED_NUMBER_PATTERN = re.compile(
    r"(\d[\d,]*(?:\.\d+)?(?:/\d[\d,]*(?:\.\d+)?)+)(" + _UNIT_ALTERNATION + ")"
)
_ENUMERATION_NUMBER_PATTERN = re.compile(
    r"(\d[\d,]*(?:\.\d+)?(?:、\d[\d,]*(?:\.\d+)?)+)(" + _UNIT_ALTERNATION + ")"
)
_FENCED_BLOCK_PATTERN = re.compile(r"```.*?```", re.DOTALL)
_DECIMAL_NUMBER_PATTERN = re.compile(r"(\d+)\.(\d+)")

# 研究模式非证据工具目录 v1：这些工具的 output/raw 是执行回执或 Agent 自产文本，
# 让报告锚定它们会形成"自我锚定"，必须排除。目录升级需同步升 RESEARCH 版本。
_RESEARCH_EXCLUDED_TOOLS = frozenset(
    {"bash", "edit", "write", "cp", "rm", "mv", "word", "excel", "read", "glob", "grep", "skill"}
)

_CHAT_BODY_CHANNEL = "chat_answer"
_MISSING_BODY_CHANNEL = "missing"
BODY_CHANNELS = frozenset(
    {"chat_answer", "write_artifact", "edit_artifact", "final_text", "missing"}
)


class AnswerEvidenceGroundingDiagnostics(TypedDict):
    """不含回答或证据正文的锚定诊断计数。"""

    orphan_result_count: int
    failure_signal_payload_count: int
    conflicting_result_count: int
    evidence_chunk_count: int
    excluded_tool_output_count: int
    non_terminal_tool_part_count: int
    turn_end_present: int
    fabrication_risk_signal: int
    rounded_match_count: int


class NormalizedAnswerEvidenceGroundingTrace(TypedDict):
    """供数值锚定率评测器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    body_channel: str
    extracted_span_count: int
    grounded_span_count: int
    evidence_text_count: int
    unit_catalog_version: str
    normalization_version: str
    failure_signal_rules_version: str
    research_excluded_tools_version: str
    diagnostics: AnswerEvidenceGroundingDiagnostics
    normalization_error: str | None


def normalize_chat_answer_grounding(
    trace: Mapping[str, object],
) -> NormalizedAnswerEvidenceGroundingTrace:
    """从新版 canvas 提取回答数值 span 并在成功载荷内做归一化精确匹配。

    证据范围：与请求侧 `toolId` 关联的 `tool_result` 载荷；命中业务失败信号的载荷、
    孤立结果与 `respond` 返回不进入证据。匹配规则：单位换算渲染 + 千分位/全半角/
    尾零归一 + 数字边界守卫 + 组合数值展开。
    """

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty_result(
            None, "chat", _MISSING_BODY_CHANNEL, error="invalid_chat_trace_structure"
        )
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty_result(
            None, "chat", _MISSING_BODY_CHANNEL, error="invalid_chat_trace_structure"
        )
    trace_id = _identifier(meta.get("traceId"))
    if trace_id is None:
        return _empty_result(None, "chat", _MISSING_BODY_CHANNEL, error="missing_trace_id")
    answer_container = canvas.get("answer")
    answer = (
        _identifier(answer_container.get("answer"))
        if isinstance(answer_container, Mapping)
        else None
    )
    if answer is None:
        return _empty_result(trace_id, "chat", _MISSING_BODY_CHANNEL, error="missing_final_answer")

    normalized = _empty_result(trace_id, "chat", _CHAT_BODY_CHANNEL)
    request_tools = _chat_request_tools(canvas, normalized)
    haystacks = _chat_evidence_haystacks(canvas, request_tools, normalized)
    _match_spans(answer, haystacks, normalized)
    return normalized


def normalize_research_answer_grounding(
    events: Sequence[object],
) -> NormalizedAnswerEvidenceGroundingTrace:
    """对单个研究回合（跨文件合并后的事件序列）计算数值锚定计数。

    输入契约：`events` 必须是同一 `userMessageID` 回合按时间序合并去重后的完整事件
    流；一个文件含多个回合或一个回合跨多个文件的切分与合并由入口层负责。
    正文通道优先级：write 产物 > edit 片段 > turn.end 前最后 assistant 文本。
    """

    session_id: str | None = None
    user_message_id: str | None = None
    turn_start_seen = False
    turn_end_seen = False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        session_id = session_id or _identifier(event.get("sessionID"))
        if event.get("type") == "turn.start":
            turn_start_seen = True
            user_message_id = user_message_id or _identifier(event.get("userMessageID"))
        elif event.get("type") == "turn.end":
            turn_end_seen = True
    if not turn_start_seen or user_message_id is None:
        return _empty_result(None, "research", _MISSING_BODY_CHANNEL, error="missing_turn_boundary")
    trace_id = f"{session_id}:{user_message_id}" if session_id else user_message_id
    normalized = _empty_result(trace_id, "research", _MISSING_BODY_CHANNEL)
    normalized["diagnostics"]["turn_end_present"] = 1 if turn_end_seen else 0

    body, channel = _research_body(events)
    if body is None:
        normalized["normalization_error"] = "missing_final_answer"
        return normalized
    normalized["body_channel"] = channel
    haystacks = _research_evidence_haystacks(events, normalized)
    _match_spans(body, haystacks, normalized)
    return normalized


def _chat_request_tools(
    canvas: Mapping[str, object], normalized: NormalizedAnswerEvidenceGroundingTrace
) -> dict[str, str]:
    """收集请求侧普通工具调用 ID，排除子 Agent 委派与 respond 返回。"""

    request_tools: dict[str, str] = {}
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        normalized["normalization_error"] = "invalid_chat_trace_structure"
        return request_tools
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            normalized["normalization_error"] = "invalid_chat_trace_structure"
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            normalized["normalization_error"] = "invalid_chat_trace_structure"
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                normalized["normalization_error"] = "invalid_chat_trace_structure"
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                normalized["normalization_error"] = "invalid_chat_trace_structure"
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                normalized["normalization_error"] = "invalid_chat_trace_structure"
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    normalized["normalization_error"] = "invalid_chat_trace_structure"
                    continue
                call_id = _identifier(raw_call.get("toolId"))
                tool_name = _identifier(raw_call.get("toolName"))
                if call_id is None:
                    # 只有 agentName 的对象是子 Agent 委派，不构成普通请求。
                    continue
                if tool_name is None:
                    # 有调用 ID 却没有工具名的对象按仓库惯例隔离为结构错误。
                    normalized["normalization_error"] = normalized["normalization_error"] or (
                        "missing_tool_name"
                    )
                    continue
                if tool_name.lower() == "respond":
                    continue
                existing = request_tools.get(call_id)
                if existing is not None and existing != tool_name:
                    normalized["normalization_error"] = "conflicting_call_definition"
                    return request_tools
                request_tools[call_id] = tool_name
    return request_tools


def _chat_evidence_haystacks(
    canvas: Mapping[str, object],
    request_tools: dict[str, str],
    normalized: NormalizedAnswerEvidenceGroundingTrace,
) -> list[str]:
    """把成功载荷归一化为可搜索文本，并追加组合数值展开变体。"""

    haystacks: list[str] = []
    raw_agents = canvas.get("agents", ())
    if not _is_sequence(raw_agents):
        return haystacks
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            continue
        raw_messages = raw_agent.get("messages", ())
        if not _is_sequence(raw_messages):
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                continue
            raw_results = content.get("tool_result", ())
            if raw_results is None or not _is_sequence(raw_results):
                continue
            for raw_result in raw_results:
                if not isinstance(raw_result, Mapping):
                    continue
                payload = _chat_result_payload(raw_result, request_tools, normalized)
                if payload is None:
                    continue
                haystacks.extend(_payload_haystacks(payload))
    normalized["evidence_text_count"] = len(haystacks)
    return haystacks


def _chat_result_payload(
    result: Mapping[str, object],
    request_tools: dict[str, str],
    normalized: NormalizedAnswerEvidenceGroundingTrace,
) -> object | None:
    """选择进入证据的结果载荷；失败信号、孤立与冲突结果被隔离。"""

    call_id = _identifier(result.get("toolId"))
    if call_id is None or call_id not in request_tools:
        normalized["diagnostics"]["orphan_result_count"] += 1
        return None
    result_tool_name = _identifier(result.get("toolName"))
    request_tool_name = request_tools[call_id]
    if result_tool_name is not None and result_tool_name != request_tool_name:
        normalized["diagnostics"]["conflicting_result_count"] += 1
        normalized["normalization_error"] = normalized["normalization_error"] or (
            "conflicting_call_definition"
        )
        return None
    payload = _first_non_empty(result.get("result"), result.get("rawResult"))
    if isinstance(payload, Mapping) and _hits_failure_signal(payload):
        normalized["diagnostics"]["failure_signal_payload_count"] += 1
        return None
    return payload


def _hits_failure_signal(payload: Mapping[str, object]) -> bool:
    """聊天业务失败信号 v1：errorcode 非 0/200、success 为假、status 失败或超时。"""

    error_code = payload.get("errorcode")
    if isinstance(error_code, str) and error_code not in {"0", "200"}:
        return True
    success = payload.get("success")
    if success is False:
        return True
    status = payload.get("status")
    if isinstance(status, str) and status.lower() in {"failed", "timeout", "timed_out"}:
        return True
    return False


def _research_body(events: Sequence[object]) -> tuple[str | None, str]:
    """按 write > edit > 最后 assistant 文本的优先级选择交付正文。"""

    writes: dict[str, str] = {}
    edits: list[str] = []
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, Mapping) or state.get("status") != "completed":
            continue
        tool = _identifier(part.get("tool"))
        payload_input = state.get("input")
        inputs = payload_input if isinstance(payload_input, Mapping) else {}
        if tool == "write":
            content = _first_non_empty(inputs.get("content"), inputs.get("newString"))
            if isinstance(content, str) and content.strip():
                path = _identifier(inputs.get("filePath")) or _identifier(inputs.get("path")) or ""
                # 同一路径后写覆盖前写，保持文件最终状态。
                writes[path] = content
        elif tool == "edit":
            fragment = inputs.get("newString")
            if isinstance(fragment, str) and len(fragment.strip()) >= 30:
                edits.append(fragment)
    if writes:
        return max(writes.values(), key=len), "write_artifact"
    if edits:
        distinct = list(dict.fromkeys(edits))
        return "\n".join(distinct), "edit_artifact"
    final_text = _research_final_text(events)
    if final_text is not None:
        return final_text, "final_text"
    return None, _MISSING_BODY_CHANNEL


def _research_final_text(events: Sequence[object]) -> str | None:
    """turn.end 前最后一个 assistant 文本，沿用陈述支持率的正文选择口径。"""

    assistant_ids: set[str] = set()
    answer: str | None = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "turn.end":
            break
        if event.get("type") == "message.updated":
            info = event.get("info")
            if isinstance(info, Mapping) and info.get("role") == "assistant":
                message_id = _identifier(info.get("id"))
                if message_id is not None:
                    assistant_ids.add(message_id)
            continue
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "text":
            continue
        message_id = _identifier(part.get("messageID"))
        if message_id is None or message_id not in assistant_ids:
            continue
        answer = _identifier(part.get("text")) or answer
    return answer


def _research_evidence_haystacks(
    events: Sequence[object], normalized: NormalizedAnswerEvidenceGroundingTrace
) -> list[str]:
    """研究证据：completed 工具 part 的 chunkList 文本与非排除工具的 output/raw。"""

    haystacks: list[str] = []
    seen_chunk_ids: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, Mapping):
            continue
        status = _identifier(state.get("status"))
        if status != "completed":
            normalized["diagnostics"]["non_terminal_tool_part_count"] += 1
            continue
        metadata = state.get("metadata")
        chunk_list = metadata.get("chunkList") if isinstance(metadata, Mapping) else None
        if _is_sequence(chunk_list):
            for chunk in chunk_list:
                if not isinstance(chunk, Mapping):
                    continue
                chunk_id = _identifier(chunk.get("chunkId"))
                chunk_text = _identifier(chunk.get("chunkText"))
                if chunk_text is None or chunk_id is None or chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                haystacks.extend(_payload_haystacks(chunk_text))
        tool = _identifier(part.get("tool")) or ""
        if tool.lower() in _RESEARCH_EXCLUDED_TOOLS:
            output = state.get("output")
            raw = state.get("raw")
            if _first_non_empty(output, raw) is not None:
                normalized["diagnostics"]["excluded_tool_output_count"] += 1
            continue
        for payload in (state.get("output"), state.get("raw")):
            text = _payload_text(payload)
            if text is not None:
                haystacks.extend(_payload_haystacks(text))
    normalized["diagnostics"]["evidence_chunk_count"] = len(seen_chunk_ids)
    normalized["evidence_text_count"] = len(haystacks)
    return haystacks


def _match_spans(
    body: str, haystacks: list[str], normalized: NormalizedAnswerEvidenceGroundingTrace
) -> None:
    """抽取回答数值 span 并统计在证据 haystack 中的精确命中数。

    匹配顺序：精确渲染键（含单位换算、货币前缀与英文数量级）优先；全部未命中时
    才进入单向舍入复述匹配（证据更精确 -> 回答取整复述），命中数单独进入
    rounded_match_count 诊断，保证保守口径的命中数可由差值随时还原。
    """

    prose = _FENCED_BLOCK_PATTERN.sub(" ", body)
    extracted = 0
    grounded = 0
    rounded = 0
    evidence_numbers: list[Decimal] | None = None
    for match in _SPAN_PATTERN.finditer(prose):
        sign, number, unit = match.group(1), match.group(2), match.group(3)
        scale = _UNIT_SCALES.get(unit)
        if scale is None:
            continue
        extracted += 1
        signed_number = ("-" + number) if sign else number
        renderings = _renderings(signed_number, unit, scale)
        if any(_find_guarded(haystack, key) for haystack in haystacks for key in renderings):
            grounded += 1
            continue
        # 舍入匹配只在存在精确未命中时才提取证据数值，避免无谓的全量 token 扫描。
        if evidence_numbers is None:
            evidence_numbers = _evidence_numbers(haystacks)
        if _rounding_match(signed_number, evidence_numbers):
            grounded += 1
            rounded += 1
    normalized["extracted_span_count"] = extracted
    normalized["grounded_span_count"] = grounded
    normalized["diagnostics"]["rounded_match_count"] = rounded
    if extracted > 0 and not haystacks:
        normalized["diagnostics"]["fabrication_risk_signal"] = 1


def _renderings(number: str, unit: str, scale: tuple[str, Decimal]) -> frozenset[str]:
    """生成 span 的匹配键：原文形、整数形、万形、亿形与 v2 追加键。

    v1 键：原文形先做尾零压缩（10.50% 与 10.5% 视为同一书写），跨货币类不换算。
    v2 追加键：个百分点与 % 互为同一书写；货币类追加货币前缀形与英文数量级形
    （17亿元 -> Rmb1.7bn、5.43亿元 -> Rmb543m），覆盖英文研报书写。
    """

    unit_class, multiplier = scale
    compact = _strip_trailing_zeros(number.replace(",", ""))
    try:
        value = Decimal(compact) * multiplier
    except ArithmeticError:
        return frozenset({normalize_text(compact + unit)})
    renderings = {normalize_text(compact + unit)}
    if unit == "个百分点":
        renderings.add(normalize_text(compact + "%"))
    elif unit in {"%", "％"}:
        renderings.add(normalize_text(compact + "个百分点"))
    if unit_class in {"cny", "usd"} and value == value.to_integral_value():
        integral = int(value)
        renderings.add(str(integral))
        if abs(integral) >= 10_000 and integral % 10_000 == 0:
            renderings.add(f"{integral // 10_000}万")
        if abs(integral) >= 100_000_000 and integral % 100_000_000 == 0:
            renderings.add(f"{integral // 100_000_000}亿")
    if unit_class in {"cny", "usd"}:
        renderings.update(_currency_renderings(value, unit_class))
    return frozenset(renderings)


def _currency_renderings(value: Decimal, unit_class: str) -> frozenset[str]:
    """货币类的英文书写渲染键，value 为换算到基准货币单位后的带符号数值。

    只生成带货币前缀的键（Rmb1.7bn / $8.17），不生成裸英文数量级键；负数保留符号，
    防止 "-5.43亿元" 命中证据里的正值 "Rmb543m"。
    """

    prefixes = _CURRENCY_PREFIXES[unit_class]
    sign = "-" if value < 0 else ""
    magnitude = -value if value < 0 else value
    magnitude_text = format(magnitude.normalize(), "f")
    renderings: set[str] = set()
    for prefix in prefixes:
        renderings.add(f"{prefix}{sign}{magnitude_text}")
    for divisor, suffixes in _ENGLISH_MAGNITUDES:
        scaled = magnitude / divisor
        if scaled < 1:
            continue
        text = format(scaled.normalize(), "f")
        for suffix in suffixes:
            for prefix in prefixes:
                renderings.add(f"{prefix}{sign}{text}{suffix}")
    return frozenset(renderings)


def _find_guarded(haystack: str, key: str) -> bool:
    """子串命中必须满足数字边界守卫，防止 6.08% 命中 16.08%、0.66亿元 命中 490.66亿元。

    v2 规则：边界检查只约束"可能延长当前数值"的方向——
    - 左侧：键首是数字时，左邻不得为数字或小数点（16.08 内的 6.08、"12.5" 内的 "5"）；
      键首是货币前缀等非数字字符时，左邻小数点属于缩写句点（"(vs.Rmb543m"），必须放行；
    - 右侧：仅当键尾本身是数字时才检查右邻；右邻为小数点且其后仍是数字才拒绝
      （"12.5" vs "12.55"），句尾小数点放行。
    键以单位字符（% 元 亿 家 bn…）结尾时，右侧数字属于下一个数（"76%2026年"
    "18.2亿美元2024"），不构成截断风险，必须放行。
    """

    if not key:
        return False
    key_start_digit = key[0].isdigit()
    key_end_digit = key[-1].isdigit()
    start = haystack.find(key)
    while start != -1:
        end = start + len(key)
        left = haystack[start - 1] if start > 0 else ""
        right = haystack[end] if end < len(haystack) else ""
        after = haystack[end + 1] if end + 1 < len(haystack) else ""
        left_bad = left.isdigit() or (left == "." and key_start_digit)
        right_bad = (
            (right.isdigit() or (right == "." and after.isdigit())) if key_end_digit else False
        )
        if not left_bad and not right_bad:
            return True
        start = haystack.find(key, start + 1)
    return False


_EVIDENCE_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
# 舍入复述倍率：证据数值可能与回答 span 处于万/亿/bn/mn 等不同书写量级；倍率只做
# 数量级缩放，不跨币种。向下因子覆盖证据侧以完整元/万元计数的表格（"小计
# 168774585187.65"元 = 1687.75亿元）。
_ROUNDED_MATCH_SCALES: tuple[Decimal, ...] = (
    Decimal(1),
    Decimal(10) ** 4,
    Decimal(10) ** 6,
    Decimal(10) ** 8,
    Decimal(10) ** 9,
    Decimal(1) / (Decimal(10) ** 4),
    Decimal(1) / (Decimal(10) ** 6),
    Decimal(1) / (Decimal(10) ** 8),
    Decimal(1) / (Decimal(10) ** 9),
)


def _evidence_numbers(haystacks: list[str]) -> list[Decimal]:
    """提取证据侧数值 token（千分位已在归一化时剥离），按原文去重降低比较量。

    token 提取天然携带完整数字边界（16.08 抽成单一 token），无需再做邻接守卫；
    时间戳、文档号等无语境数字可能参与比较，属可接受残余噪声，命中数由
    rounded_match_count 单独暴露以便审计。
    """

    numbers: list[Decimal] = []
    seen: set[str] = set()
    for haystack in haystacks:
        for match in _EVIDENCE_NUMBER_PATTERN.finditer(haystack):
            text = match.group(0)
            if text in seen:
                continue
            seen.add(text)
            try:
                number = Decimal(text)
            except ArithmeticError:
                continue
            if number != 0:
                numbers.append(number)
    return numbers


def _rounding_match(number: str, evidence_numbers: list[Decimal]) -> bool:
    """舍入复述单向锚定：证据小数位多于回答时，按回答精度取整相等才算命中。

    反向（回答比证据更精确）不放行——证据是事实源，回答多出的精度没有出处。
    整数 span（无小数位）与零值不做舍入匹配，保持 v1 保守口径。
    """

    compact = _strip_trailing_zeros(number.replace(",", ""))
    try:
        target = Decimal(compact)
    except ArithmeticError:
        return False
    target_exponent = target.as_tuple().exponent
    if not isinstance(target_exponent, int):
        return False  # NaN/Infinity 不参与舍入匹配
    target_decimals = -target_exponent
    if target_decimals < 1 or target == 0:
        return False
    quantum = Decimal(1).scaleb(-target_decimals)
    for raw in evidence_numbers:
        for factor in _ROUNDED_MATCH_SCALES:
            candidate = raw * factor
            candidate_exponent = candidate.as_tuple().exponent
            if not isinstance(candidate_exponent, int):
                continue
            candidate_decimals = -candidate_exponent
            if candidate_decimals <= target_decimals:
                continue
            try:
                if candidate.quantize(quantum) == target:
                    return True
            except ArithmeticError:
                # 超出精度范围的 token 按隔离处理，不中断匹配。
                continue
    return False


def _payload_haystacks(payload: object) -> list[str]:
    """单个载荷的归一化文本与组合数值展开变体。"""

    text = _payload_text(payload)
    if text is None:
        return []
    normalized = _strip_trailing_zeros(normalize_text(text))
    haystacks = [normalized]
    expanded = _COMBINED_NUMBER_PATTERN.sub(_expand_combined_match, normalized)
    expanded = _ENUMERATION_NUMBER_PATTERN.sub(_expand_enumeration_match, expanded)
    if expanded != normalized:
        haystacks.append(expanded)
    return haystacks


def _strip_trailing_zeros(text: str) -> str:
    """去掉数字小数位的无效尾零：10.50% 与 10.5%、1.0000000000 与 1 视为同一书写。"""

    def _replace(match: re.Match[str]) -> str:
        fraction = match.group(2).rstrip("0")
        return match.group(1) + (f".{fraction}" if fraction else "")

    return _DECIMAL_NUMBER_PATTERN.sub(_replace, text)


def _expand_combined_match(match: re.Match[str]) -> str:
    """把 "12.15/2.35亿元" 展开为 "12.15亿元 2.35亿元"。

    展开项之间必须使用非数字分隔符，否则相邻数字会触发边界守卫造成假未命中。
    """

    numbers, unit = match.group(1), match.group(2)
    return " ".join(number + unit for number in numbers.split("/"))


def _expand_enumeration_match(match: re.Match[str]) -> str:
    """把 "38337、10214、6589家" 展开为 "38337家 10214家 6589家"。

    中文研报的顿号列举与斜杠组合同属"共享单位"书写；半角逗号已被千分位剥离
    吞掉，不参与展开。
    """

    numbers, unit = match.group(1), match.group(2)
    return " ".join(number + unit for number in re.split("、", numbers))


def _payload_text(payload: object) -> str | None:
    """载荷统一转成文本；无法文本化的标量不参与锚定。"""

    if isinstance(payload, str):
        return payload if payload.strip() else None
    if isinstance(payload, Mapping):
        return json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, Sequence):
        return json.dumps(payload, ensure_ascii=False)
    return None


def normalize_text(value: str) -> str:
    """去空白、全角逗号与千分位逗号、全角百分号归一，供匹配键与 haystack 使用。"""

    return re.sub(r"\s+", "", value).replace("，", "").replace(",", "").replace("％", "%")


def _first_non_empty(*values: object) -> object | None:
    for value in values:
        if value is None or value == "" or value == {} or value == []:
            continue
        return value
    return None


def _empty_result(
    trace_id: str | None,
    mode: str,
    body_channel: str,
    *,
    error: str | None = None,
) -> NormalizedAnswerEvidenceGroundingTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "body_channel": body_channel,
        "extracted_span_count": 0,
        "grounded_span_count": 0,
        "evidence_text_count": 0,
        "unit_catalog_version": UNIT_CATALOG_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "failure_signal_rules_version": FAILURE_SIGNAL_RULES_VERSION,
        "research_excluded_tools_version": RESEARCH_EXCLUDED_TOOLS_VERSION,
        "diagnostics": {
            "orphan_result_count": 0,
            "failure_signal_payload_count": 0,
            "conflicting_result_count": 0,
            "evidence_chunk_count": 0,
            "excluded_tool_output_count": 0,
            "non_terminal_tool_part_count": 0,
            "turn_end_present": 0,
            "fabrication_risk_signal": 0,
            "rounded_match_count": 0,
        },
        "normalization_error": error,
    }


def _identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
