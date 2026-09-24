"""规范化引用可溯率所需的引用编号与来源注册表计数，不保留回答或来源正文。

业务规则（v1.0.0，2026-09-20 口径确认）：最终回答中的 `[n]` 引用编号能否解析到本
Trace 中间检索输出暴露的来源编号注册表。比对是纯编号解析：回答侧抽"引用了哪些
编号"，中间输出侧抽"存在哪些编号"，交集算可溯；编号存在不代表内容真使用了该来源
（内容级对应属 v2/Judge 方向），该弱判定边界随结果版本声明。

注册表形态：
- 聊天：按消息稳定顺序拼接各成功检索轮的 doc_id 序列，注册表为连续位置 1..N；
- 研究：本回合 completed 工具输出中的 `id: [n]` 与行首 `n. [` 编号清单并集，
  注册表为离散集合。write/edit 产物自身不是来源清单，排除防止自我锚定。
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard

from collectors.answer_evidence_grounding import (
    _FENCED_BLOCK_PATTERN,
    _first_non_empty,
    _hits_failure_signal,
    _identifier,
    _research_body,
)

# 版本化规则标识；抽取或注册表规则变化时必须同步升版。
CITE_PATTERN_VERSION = "cite_v1"
SOURCE_REGISTRY_VERSION = "source_registry_v1"

# 纯数字单值引用；[图1]、[注2] 天然不命中。
_CITATION_PATTERN = re.compile(r"\[(\d{1,4})\]")
# markdown 链接形态 [n](http...)：链接文本同样承载引用语义，计入引用并单独计数。
_MARKDOWN_LINK_CITATION_PATTERN = re.compile(r"\[(\d{1,4})\]\(")
# 复合引用形态 [1,2] / [1-3]：v1 不解析，只计数诊断。
_NONSTANDARD_CITATION_PATTERN = re.compile(r"\[\d{1,4}(?:[,，]\d{1,4}|[-–]\d{1,4})+\]")
# 研究模式来源编号清单的两种已实勘形态：skill 注入的 "id: [n]"（主形态）与
# 行首 "n. [标题]" 渲染形态。
_RESEARCH_SOURCE_ID_PATTERN = re.compile(r"id:\s*\[(\d{1,5})\]")
_RESEARCH_LINE_NUMBER_PATTERN = re.compile(r"^\s*(\d{1,4})[.、)）]\s*\[", re.MULTILINE)

# 研究模式交付产物工具：其 input 是报告正文而非来源清单。
_RESEARCH_DELIVERY_TOOLS = frozenset({"write", "edit"})

_CHAT_BODY_CHANNEL = "chat_answer"
_MISSING_BODY_CHANNEL = "missing"
BODY_CHANNELS = frozenset(
    {"chat_answer", "write_artifact", "edit_artifact", "final_text", "missing"}
)


class CitationTraceabilityDiagnostics(TypedDict):
    """不含回答或来源正文的引用可溯诊断计数。"""

    malformed_event_count: int
    orphan_result_count: int
    failure_signal_payload_count: int
    conflicting_result_count: int
    nonstandard_citation_count: int
    markdown_link_citation_count: int
    range_exceeded_citation_count: int
    source_list_missing: int
    numbering_scheme_mixed_suspected: int
    fabrication_risk_signal: int


class NormalizedCitationTraceabilityTrace(TypedDict):
    """供引用可溯率评测器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    body_channel: str
    citation_pattern_version: str
    source_registry_version: str
    extracted_citation_count: int
    resolved_citation_count: int
    range_exceeded_citation_count: int
    nonstandard_citation_count: int
    source_sequence_length: int
    source_registry_size: int
    diagnostics: CitationTraceabilityDiagnostics
    normalization_error: str | None


def normalize_chat_citation_traceability(
    trace: Mapping[str, object],
) -> NormalizedCitationTraceabilityTrace:
    """从新版 canvas 提取回答引用编号，并在检索轮 doc_id 位置序列内解析。"""

    normalized = _empty_result("chat")
    canvas = trace.get("canvas")
    meta = canvas.get("meta") if isinstance(canvas, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(meta, Mapping):
        normalized["normalization_error"] = "invalid_chat_trace_structure"
        normalized["diagnostics"]["malformed_event_count"] = 1
        return normalized
    trace_id = _identifier(meta.get("traceId"))
    if trace_id is None:
        normalized["normalization_error"] = "missing_trace_id"
        normalized["diagnostics"]["malformed_event_count"] = 1
        return normalized
    normalized["trace_id"] = trace_id
    answer_container = canvas.get("answer")
    answer = (
        _identifier(answer_container.get("answer"))
        if isinstance(answer_container, Mapping)
        else None
    )
    if answer is None:
        normalized["body_channel"] = _MISSING_BODY_CHANNEL
        normalized["normalization_error"] = "missing_final_answer"
        return normalized
    normalized["body_channel"] = _CHAT_BODY_CHANNEL

    doc_sequence, round_lengths = _chat_registry(canvas, normalized)
    _resolve_citations(answer, doc_sequence, normalized, round_lengths)
    return normalized


def normalize_research_citation_traceability(
    events: Sequence[object],
) -> NormalizedCitationTraceabilityTrace:
    """对单个研究回合计算引用可溯计数；输入契约与锚定率一致（单回合事件序列）。"""

    normalized = _empty_result("research")
    session_id: str | None = None
    user_message_id: str | None = None
    for event in events:
        if not isinstance(event, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        session_id = session_id or _identifier(event.get("sessionID"))
        if event.get("type") == "turn.start":
            user_message_id = user_message_id or _identifier(event.get("userMessageID"))
    if user_message_id is None:
        normalized["normalization_error"] = "missing_turn_boundary"
        normalized["diagnostics"]["malformed_event_count"] += 1
        return normalized
    normalized["trace_id"] = f"{session_id}:{user_message_id}" if session_id else user_message_id

    id_registry = _research_registry(events, normalized)
    body, channel = _research_body(events)
    if body is None:
        normalized["body_channel"] = _MISSING_BODY_CHANNEL
        normalized["normalization_error"] = "missing_final_answer"
        return normalized
    normalized["body_channel"] = channel
    _resolve_citations(body, sorted(id_registry), normalized, None)
    return normalized


def _chat_registry(
    canvas: Mapping[str, object], normalized: NormalizedCitationTraceabilityTrace
) -> tuple[list[int], list[int]]:
    """构建聊天注册表：成功检索轮 doc_id 按消息稳定顺序拼接，返回序列与各轮长度。

    轮长度供混排编号诊断使用（引用最大值未超过任何单轮 doc 数时无法区分
    累积编号与每轮重排）。
    """

    request_tools: dict[str, str] = {}
    doc_sequence: list[int] = []
    round_lengths: list[int] = []
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        normalized["normalization_error"] = normalized["normalization_error"] or (
            "invalid_chat_trace_structure"
        )
        return doc_sequence, round_lengths
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                normalized["diagnostics"]["malformed_event_count"] += 1
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                normalized["diagnostics"]["malformed_event_count"] += 1
                continue
            for raw_call in _as_sequence(content.get("tool_call")):
                if not isinstance(raw_call, Mapping):
                    normalized["diagnostics"]["malformed_event_count"] += 1
                    continue
                call_id = _identifier(raw_call.get("toolId"))
                tool_name = _identifier(raw_call.get("toolName"))
                if call_id is None:
                    # 只有 agentName 的对象是子 Agent 委派，不构成普通请求。
                    continue
                if tool_name is None:
                    normalized["normalization_error"] = normalized["normalization_error"] or (
                        "missing_tool_name"
                    )
                    continue
                if tool_name.lower() == "respond":
                    continue
                existing = request_tools.get(call_id)
                if existing is not None and existing != tool_name:
                    normalized["diagnostics"]["conflicting_result_count"] += 1
                    continue
                request_tools[call_id] = tool_name
            for raw_result in _as_sequence(content.get("tool_result")):
                if not isinstance(raw_result, Mapping):
                    normalized["diagnostics"]["malformed_event_count"] += 1
                    continue
                payload = _chat_result_payload(raw_result, request_tools, normalized)
                if payload is None:
                    continue
                round_ids = _extract_doc_ids(payload)
                if round_ids:
                    round_lengths.append(len(round_ids))
                    doc_sequence.extend(round_ids)
    return doc_sequence, round_lengths


def _chat_result_payload(
    result: Mapping[str, object],
    request_tools: Mapping[str, str],
    normalized: NormalizedCitationTraceabilityTrace,
) -> object | None:
    """选择进入注册表的结果载荷；失败信号、孤立与冲突结果被隔离。"""

    call_id = _identifier(result.get("toolId"))
    if call_id is None or call_id not in request_tools:
        normalized["diagnostics"]["orphan_result_count"] += 1
        return None
    result_tool_name = _identifier(result.get("toolName"))
    request_tool_name = request_tools[call_id]
    if result_tool_name is not None and result_tool_name != request_tool_name:
        normalized["diagnostics"]["conflicting_result_count"] += 1
        return None
    payload = _first_non_empty(result.get("result"), result.get("rawResult"))
    if isinstance(payload, Mapping) and _hits_failure_signal(payload):
        normalized["diagnostics"]["failure_signal_payload_count"] += 1
        return None
    return payload


def _extract_doc_ids(payload: object) -> list[int]:
    """从成功载荷提取 doc_id 序列；只识别含 doc_id 键的结构，工具名不做白名单。"""

    doc_ids: list[int] = []
    if isinstance(payload, Mapping):
        nested = payload.get("doc_list")
        if nested is not None and _is_sequence(nested):
            for item in nested:
                doc_ids.extend(_extract_doc_ids(item))
        elif payload.get("doc_id") is not None:
            doc_id = _to_int(payload["doc_id"])
            if doc_id is not None:
                doc_ids.append(doc_id)
    elif _is_sequence(payload):
        for item in payload:
            doc_ids.extend(_extract_doc_ids(item))
    return doc_ids


def _research_registry(
    events: Sequence[object], normalized: NormalizedCitationTraceabilityTrace
) -> set[int]:
    """构建研究注册表：completed 工具 part 输出中的来源编号集合。"""

    registry: set[int] = set()
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, Mapping) or state.get("status") != "completed":
            continue
        tool = _identifier(part.get("tool")) or ""
        if tool.lower() in _RESEARCH_DELIVERY_TOOLS:
            # 交付产物自身不是来源清单，排除防止报告引用自建编号造成自我锚定。
            continue
        output_text = _payload_text(state.get("output"))
        if not output_text:
            continue
        for pattern in (_RESEARCH_SOURCE_ID_PATTERN, _RESEARCH_LINE_NUMBER_PATTERN):
            for match in pattern.finditer(output_text):
                doc_id = _to_int(match.group(1))
                if doc_id is not None:
                    registry.add(doc_id)
    return registry


def _resolve_citations(
    body: str,
    doc_sequence: list[int],
    normalized: NormalizedCitationTraceabilityTrace,
    round_lengths: list[int] | None,
) -> None:
    """抽取正文引用编号并在注册表内解析；注册表为空时输出无中生有信号。"""

    prose = _FENCED_BLOCK_PATTERN.sub(" ", body)
    nonstandard = len(_NONSTANDARD_CITATION_PATTERN.findall(prose))
    markdown_links = len(_MARKDOWN_LINK_CITATION_PATTERN.findall(prose))
    citations = {int(match.group(1)) for match in _CITATION_PATTERN.finditer(prose)}

    registry = set(doc_sequence)
    resolved = sum(1 for number in citations if number in registry)
    range_exceeded = len(citations) - resolved

    normalized["extracted_citation_count"] = len(citations)
    normalized["resolved_citation_count"] = resolved
    normalized["range_exceeded_citation_count"] = range_exceeded
    normalized["nonstandard_citation_count"] = nonstandard
    normalized["source_sequence_length"] = len(doc_sequence) if normalized["mode"] == "chat" else 0
    normalized["source_registry_size"] = (
        len(doc_sequence) if normalized["mode"] == "chat" else len(registry)
    )
    diagnostics = normalized["diagnostics"]
    diagnostics["nonstandard_citation_count"] = nonstandard
    diagnostics["markdown_link_citation_count"] = markdown_links
    diagnostics["range_exceeded_citation_count"] = range_exceeded
    if citations and not doc_sequence:
        # 有引用而无任何来源注册表：引用编号无从解析，按无中生有信号暴露。
        diagnostics["source_list_missing"] = 1
        diagnostics["fabrication_risk_signal"] = 1
    elif citations and normalized["mode"] == "chat" and round_lengths:
        # 聊天编号语义混杂（累积 vs 每轮重排）：引用最大值不超过任何单轮 doc 数
        # 且序列明显更长时，标记疑似重排，提示解读时结合该标记而非直接视为幻觉。
        max_round = max(round_lengths)
        if max(citations) <= max_round and len(doc_sequence) >= 3 * max_round:
            diagnostics["numbering_scheme_mixed_suspected"] = 1


def _payload_text(payload: object) -> str:
    """来源输出统一转文本；None/空值返回空串。"""

    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        return json.dumps(payload, ensure_ascii=False)
    if _is_sequence(payload):
        return json.dumps(payload, ensure_ascii=False)
    return ""


def _to_int(value: object) -> int | None:
    """doc_id / 编号的整数解析；非法值返回 None 并由调用方隔离。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_sequence(value: object) -> Sequence[object]:
    if value is None:
        return ()
    if _is_sequence(value):
        return value
    return ()


def _empty_result(mode: str) -> NormalizedCitationTraceabilityTrace:
    return {
        "trace_id": None,
        "mode": mode,
        "body_channel": _MISSING_BODY_CHANNEL,
        "citation_pattern_version": CITE_PATTERN_VERSION,
        "source_registry_version": SOURCE_REGISTRY_VERSION,
        "extracted_citation_count": 0,
        "resolved_citation_count": 0,
        "range_exceeded_citation_count": 0,
        "nonstandard_citation_count": 0,
        "source_sequence_length": 0,
        "source_registry_size": 0,
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


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
