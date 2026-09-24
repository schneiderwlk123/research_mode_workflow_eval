"""规范化聊天与研究模式的逐工具调用耗时和 Token 可观测性。

统计范围为全部业务工具类型(MCP、文件类、文档类、skill、task、run_code 等,
不按 MCP 手册过滤;task 为研究侧子 Agent 委派,按 2026-09-21 需求方确认保留
计入),仅排除两类非业务调用:

- 聊天 `agentName` 结构化子 Agent 委派(无 toolId):排除并计入
  `excluded_agent_delegation_count` 诊断(此前静默丢弃,2026-09-21 补齐计数)。
- 聊天 `respond` Agent 返回(权威口径 1.1 归类 agent_return,大小写不敏感):
  排除并计入 `excluded_agent_return_count` 诊断;其结果载荷不算孤立结果。
  修复记录: 此前 respond 泄漏进 per_tool_call_latency_ms 聊天列(2026-09-17
  样本 13 处),与 per_tool_call_tokens/max_tool_latency_share 口径不一致。
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypedDict, TypeGuard

# 结果侧 Agent 返回工具名(与 tool_call_statistics/tool_step_tokens 同一目录,
# casefold 比较);研究模式无此结构,两个排除计数恒为 0。
_CHAT_AGENT_RETURN_TOOL_NAMES = frozenset({"respond"})


class NormalizedToolResourceCall(TypedDict):
    """单次工具调用的安全资源视图，不保留参数和响应正文。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    call_id: str
    tool_name: str
    duration_ms: int | None
    duration_error: str | None
    token_total: None
    token_error: Literal["no_observable_tool_token_usage"]


class ToolResourceUsageDiagnostics(TypedDict):
    """逐调用资源规范化的覆盖和异常诊断。"""

    duration_missing_count: int
    duplicate_observation_count: int
    orphan_result_count: int
    malformed_event_count: int
    excluded_agent_delegation_count: int
    excluded_agent_return_count: int


class NormalizedToolResourceTrace(TypedDict):
    """一个 Trace 内全部普通工具调用的资源视图。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    tool_calls: tuple[NormalizedToolResourceCall, ...]
    diagnostics: ToolResourceUsageDiagnostics
    normalization_error: str | None


_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)
_TOOL_TOKEN_ERROR: Literal["no_observable_tool_token_usage"] = "no_observable_tool_token_usage"


def normalize_chat_tool_resource_usage(
    trace: Mapping[str, object],
) -> NormalizedToolResourceTrace:
    """关联新版 canvas 的工具请求与结果，并提取每次调用的独立耗时。"""

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
    observations: dict[str, list[object]] = {}
    duplicate_count = 0
    orphan_count = 0
    malformed_count = 0
    delegation_count = 0
    excluded_return_ids: set[str] = set()
    error: str | None = None

    for raw_agent in agents:
        if not isinstance(raw_agent, Mapping):
            malformed_count += 1
            error = error or "invalid_chat_trace_structure"
            continue
        messages = raw_agent.get("messages", ())
        if messages is None:
            messages = ()
        if not _is_sequence(messages):
            malformed_count += 1
            error = error or "invalid_chat_trace_structure"
            continue
        for raw_message in messages:
            if not isinstance(raw_message, Mapping):
                malformed_count += 1
                error = error or "invalid_chat_trace_structure"
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                malformed_count += 1
                error = error or "invalid_chat_trace_structure"
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                malformed_count += 1
                error = error or "invalid_chat_trace_structure"
            else:
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_count += 1
                        error = error or "invalid_chat_trace_structure"
                        continue
                    call_id = _text(raw_call.get("toolId"))
                    tool_name = _text(raw_call.get("toolName"))
                    if call_id is None and tool_name is None and _text(raw_call.get("agentName")):
                        delegation_count += 1
                        continue
                    if call_id is None:
                        return _empty(
                            "chat", trace_id, "missing_call_id", malformed=malformed_count + 1
                        )
                    if tool_name is None:
                        return _empty(
                            "chat", trace_id, "missing_tool_name", malformed=malformed_count + 1
                        )
                    if tool_name.casefold() in _CHAT_AGENT_RETURN_TOOL_NAMES:
                        # respond 是 Agent 返回,不是业务工具;重复定义只计一次排除。
                        if call_id in excluded_return_ids or call_id in definitions:
                            duplicate_count += 1
                        else:
                            excluded_return_ids.add(call_id)
                        continue
                    existing = definitions.get(call_id)
                    if existing is not None:
                        duplicate_count += 1
                        if existing != tool_name:
                            return _empty(
                                "chat",
                                trace_id,
                                "conflicting_call_definition",
                                malformed=malformed_count,
                                duplicate=duplicate_count,
                            )
                        continue
                    definitions[call_id] = tool_name

            raw_results = content.get("tool_result", ())
            if raw_results is None:
                raw_results = ()
            if not _is_sequence(raw_results):
                malformed_count += 1
                error = error or "invalid_chat_trace_structure"
            else:
                for raw_result in raw_results:
                    if not isinstance(raw_result, Mapping):
                        malformed_count += 1
                        error = error or "invalid_chat_trace_structure"
                        continue
                    call_id = _text(raw_result.get("toolId"))
                    if call_id is None:
                        malformed_count += 1
                        error = error or "missing_call_id"
                        continue
                    observed_results = observations.setdefault(call_id, [])
                    if observed_results:
                        duplicate_count += 1
                    observed_results.append(raw_result)

    calls: list[NormalizedToolResourceCall] = []
    missing_count = 0
    for call_id, tool_name in definitions.items():
        raw_durations: list[int] = []
        duration_error: str | None = None
        for raw_result in observations.pop(call_id, []):
            assert isinstance(raw_result, Mapping)
            result_name = _text(raw_result.get("toolName"))
            if result_name is not None and result_name != tool_name:
                return _empty(
                    "chat",
                    trace_id,
                    "conflicting_call_definition",
                    malformed=malformed_count,
                    duplicate=duplicate_count,
                )
            state, duration_ms = _seconds_duration_ms(raw_result.get("durationSeconds"))
            if state == "invalid":
                duration_error = "invalid_duration"
            elif state == "negative":
                duration_error = "invalid_event_order"
            elif duration_ms is not None:
                raw_durations.append(duration_ms)

        unique_durations = set(raw_durations)
        if duration_error is None and len(unique_durations) > 1:
            duration_error = "conflicting_duration_observation"
        duration_ms = next(iter(unique_durations)) if len(unique_durations) == 1 else None
        if duration_error is not None:
            duration_ms = None
        elif duration_ms is None:
            duration_error = "missing_tool_duration"
            missing_count += 1
        calls.append(_call(trace_id, "chat", call_id, tool_name, duration_ms, duration_error))

    # 被排除 Agent 返回的结果载荷随调用一并排除,不算孤立结果。
    orphan_count = sum(
        len(values)
        for call_id, values in observations.items()
        if call_id not in excluded_return_ids
    )
    if not calls and error is None:
        error = "no_tool_calls"
    return {
        "trace_id": trace_id,
        "mode": "chat",
        "tool_calls": tuple(calls),
        "diagnostics": {
            "duration_missing_count": missing_count,
            "duplicate_observation_count": duplicate_count,
            "orphan_result_count": orphan_count,
            "malformed_event_count": malformed_count,
            "excluded_agent_delegation_count": delegation_count,
            "excluded_agent_return_count": len(excluded_return_ids),
        },
        "normalization_error": error,
    }


def normalize_research_tool_resource_usage(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolResourceTrace:
    """按 callID 合并研究模式工具生命周期并计算墙钟耗时。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure", malformed=1)
    resolved_session_id = _text(session_id)
    resolved_message_id = _text(user_message_id)
    names: dict[str, str] = {}
    starts: dict[str, list[int]] = {}
    ends: dict[str, list[int]] = {}
    statuses: dict[str, set[str]] = {}
    call_order: dict[str, None] = {}
    call_errors: dict[str, str] = {}
    duplicate_count = 0
    malformed_count = 0

    for event in events:
        if not isinstance(event, Mapping):
            malformed_count += 1
            continue
        resolved_session_id = resolved_session_id or _text(event.get("sessionID"))
        resolved_message_id = resolved_message_id or _text(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        call_id = _text(part.get("callID"))
        if call_id is None:
            return _empty(
                "research",
                _research_trace_id(resolved_session_id, resolved_message_id),
                "missing_call_id",
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        call_order.setdefault(call_id, None)
        tool_name = _text(part.get("tool"))
        if tool_name is not None:
            existing_name = names.get(call_id)
            if existing_name is not None and existing_name != tool_name:
                return _empty(
                    "research",
                    _research_trace_id(resolved_session_id, resolved_message_id),
                    "conflicting_call_definition",
                    malformed=malformed_count,
                    duplicate=duplicate_count,
                )
            names[call_id] = tool_name

        state = part.get("state")
        if not isinstance(state, Mapping):
            malformed_count += 1
            call_errors[call_id] = "invalid_research_trace_structure"
            continue
        status = _normalized_status(state.get("status"))
        if status is None:
            malformed_count += 1
            call_errors[call_id] = "invalid_research_trace_structure"
            continue
        observed_statuses = statuses.setdefault(call_id, set())
        if status in observed_statuses:
            duplicate_count += 1
        observed_statuses.add(status)

        raw_time = state.get("time", {})
        if not isinstance(raw_time, Mapping):
            malformed_count += 1
            call_errors[call_id] = "invalid_research_trace_structure"
            continue
        start_state, start_ms = _timestamp_ms(raw_time.get("start"))
        if start_state == "invalid":
            call_errors[call_id] = "invalid_duration"
        elif start_ms is not None:
            starts.setdefault(call_id, []).append(start_ms)
        if status in _TERMINAL_STATUSES:
            end_state, end_ms = _timestamp_ms(raw_time.get("end"))
            if end_state == "invalid":
                call_errors[call_id] = "invalid_duration"
            elif end_ms is not None:
                ends.setdefault(call_id, []).append(end_ms)

    trace_id = _research_trace_id(resolved_session_id, resolved_message_id)
    calls: list[NormalizedToolResourceCall] = []
    missing_count = 0
    for call_id in call_order:
        tool_name = names.get(call_id)
        if tool_name is None:
            return _empty(
                "research",
                trace_id,
                "missing_tool_name",
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        duration_error = call_errors.get(call_id)
        duration_ms: int | None = None
        start_values = starts.get(call_id, [])
        end_values = ends.get(call_id, [])
        if duration_error is None and start_values and end_values:
            duration_ms = max(end_values) - min(start_values)
            if duration_ms < 0:
                duration_ms = None
                duration_error = "invalid_event_order"
        elif duration_error is None:
            duration_error = "missing_tool_duration"
            missing_count += 1
        calls.append(_call(trace_id, "research", call_id, tool_name, duration_ms, duration_error))

    error = None if calls else "no_tool_calls"
    return {
        "trace_id": trace_id,
        "mode": "research",
        "tool_calls": tuple(calls),
        "diagnostics": {
            "duration_missing_count": missing_count,
            "duplicate_observation_count": duplicate_count,
            "orphan_result_count": 0,
            "malformed_event_count": malformed_count,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
        },
        "normalization_error": error,
    }


def _call(
    trace_id: str | None,
    mode: Literal["chat", "research"],
    call_id: str,
    tool_name: str,
    duration_ms: int | None,
    duration_error: str | None,
) -> NormalizedToolResourceCall:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "call_id": call_id,
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "duration_error": duration_error,
        # 当前两种日志都只有模型步骤 Token，没有工具执行自身 Token。
        "token_total": None,
        "token_error": _TOOL_TOKEN_ERROR,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    *,
    malformed: int = 0,
    duplicate: int = 0,
) -> NormalizedToolResourceTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "tool_calls": (),
        "diagnostics": {
            "duration_missing_count": 0,
            "duplicate_observation_count": duplicate,
            "orphan_result_count": 0,
            "malformed_event_count": malformed,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
        },
        "normalization_error": error,
    }


def _seconds_duration_ms(value: object) -> tuple[str, int | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "invalid", None
    try:
        numeric = float(value)
    except OverflowError:
        return "invalid", None
    if not math.isfinite(numeric):
        return "invalid", None
    if numeric < 0:
        return "negative", None
    return "valid", round(numeric * 1000)


def _timestamp_ms(value: object) -> tuple[str, int | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool):
        return "invalid", None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except OverflowError:
            return "invalid", None
        if not math.isfinite(numeric):
            return "invalid", None
        seconds = numeric / 1000 if abs(numeric) >= 100_000_000_000 else numeric
        try:
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return "invalid", None
        return "valid", round(parsed.timestamp() * 1000)
    if not isinstance(value, str):
        return "invalid", None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return "invalid", None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return "valid", round(parsed.timestamp() * 1000)


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
