"""规范化双模式工具调用次数，不保留参数、响应或消息正文。"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class PerToolCallStatistics(TypedDict):
    """单个规范化工具名在一条 Trace 内的调用统计。"""

    tool_name: str
    call_count: int
    repeat_call_count: int


class ToolCallStatisticsDiagnostics(TypedDict):
    """不包含调用正文的去重和排除诊断。"""

    duplicate_call_observation_count: int
    excluded_agent_delegation_count: int
    excluded_agent_return_count: int
    malformed_event_count: int


class NormalizedToolCallStatisticsTrace(TypedDict):
    """供工具调用次数指标消费的安全聚合结果。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    per_tool_statistics: tuple[PerToolCallStatistics, ...]
    diagnostics: ToolCallStatisticsDiagnostics
    normalization_error: str | None


_CHAT_AGENT_RETURN_TOOL_NAMES = frozenset({"respond"})


def normalize_chat_tool_call_statistics(
    trace: Mapping[str, object],
) -> NormalizedToolCallStatisticsTrace:
    """按 `traceId + toolId` 统计新版聊天日志中的唯一业务工具调用。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _empty("chat", None, "missing_trace_id", malformed=1)

    definitions: dict[str, str] = {}
    duplicate_count = 0
    excluded_delegation_count = 0
    excluded_return_count = 0

    for raw_agent in agents:
        if not isinstance(raw_agent, Mapping):
            return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
        messages = raw_agent.get("messages", ())
        if messages is None:
            messages = ()
        if not _is_sequence(messages):
            return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
        for raw_message in messages:
            if not isinstance(raw_message, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
                call_id = _text(raw_call.get("toolId"))
                tool_name = _text(raw_call.get("toolName"))
                agent_name = _text(raw_call.get("agentName"))
                if call_id is None and tool_name is None and agent_name is not None:
                    excluded_delegation_count += 1
                    continue
                if call_id is None:
                    return _empty("chat", trace_id, "missing_call_id", malformed=1)
                if tool_name is None:
                    return _empty("chat", trace_id, "missing_tool_name", malformed=1)
                existing = definitions.get(call_id)
                if existing is not None:
                    duplicate_count += 1
                    if existing != tool_name:
                        return _empty(
                            "chat",
                            trace_id,
                            "conflicting_call_definition",
                            duplicate=duplicate_count,
                        )
                    continue
                definitions[call_id] = tool_name

    business_names: list[str] = []
    for tool_name in definitions.values():
        if tool_name.casefold() in _CHAT_AGENT_RETURN_TOOL_NAMES:
            excluded_return_count += 1
        else:
            business_names.append(tool_name)
    return _result(
        "chat",
        trace_id,
        business_names,
        duplicate=duplicate_count,
        excluded_delegation=excluded_delegation_count,
        excluded_return=excluded_return_count,
    )


def normalize_research_tool_call_statistics(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolCallStatisticsTrace:
    """按 `callID` 合并研究模式工具生命周期并直接统计调用。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure", malformed=1)
    resolved_session_id = _text(session_id)
    resolved_message_id = _text(user_message_id)
    definitions: dict[str, str] = {}
    duplicate_count = 0

    for event in events:
        if not isinstance(event, Mapping):
            return _empty("research", None, "invalid_research_trace_structure", malformed=1)
        resolved_session_id = resolved_session_id or _text(event.get("sessionID"))
        resolved_message_id = resolved_message_id or _text(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            return _empty(
                "research",
                _research_trace_id(resolved_session_id, resolved_message_id),
                "invalid_research_trace_structure",
                malformed=1,
            )
        if part.get("type") != "tool":
            continue
        call_id = _text(part.get("callID"))
        if call_id is None:
            return _empty(
                "research",
                _research_trace_id(resolved_session_id, resolved_message_id),
                "missing_call_id",
                malformed=1,
            )
        tool_name = _text(part.get("tool"))
        if tool_name is None:
            return _empty(
                "research",
                _research_trace_id(resolved_session_id, resolved_message_id),
                "missing_tool_name",
                malformed=1,
            )
        existing = definitions.get(call_id)
        if existing is not None:
            duplicate_count += 1
            if existing != tool_name:
                return _empty(
                    "research",
                    _research_trace_id(resolved_session_id, resolved_message_id),
                    "conflicting_call_definition",
                    duplicate=duplicate_count,
                )
            continue
        definitions[call_id] = tool_name

    return _result(
        "research",
        _research_trace_id(resolved_session_id, resolved_message_id),
        list(definitions.values()),
        duplicate=duplicate_count,
    )


def _result(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    tool_names: list[str],
    *,
    duplicate: int = 0,
    excluded_delegation: int = 0,
    excluded_return: int = 0,
) -> NormalizedToolCallStatisticsTrace:
    counts = Counter(tool_names)
    statistics = tuple(
        PerToolCallStatistics(
            tool_name=tool_name,
            call_count=call_count,
            repeat_call_count=max(call_count - 1, 0),
        )
        for tool_name, call_count in sorted(counts.items())
    )
    return {
        "trace_id": trace_id,
        "mode": mode,
        "per_tool_statistics": statistics,
        "diagnostics": {
            "duplicate_call_observation_count": duplicate,
            "excluded_agent_delegation_count": excluded_delegation,
            "excluded_agent_return_count": excluded_return,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    *,
    duplicate: int = 0,
    malformed: int = 0,
) -> NormalizedToolCallStatisticsTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "per_tool_statistics": (),
        "diagnostics": {
            "duplicate_call_observation_count": duplicate,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
            "malformed_event_count": malformed,
        },
        "normalization_error": error,
    }


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
