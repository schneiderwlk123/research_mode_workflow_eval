"""将聊天和研究日志中的工具调用事件规范化为无敏感正文的统一结构。"""

from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard


class NormalizedToolCall(TypedDict):
    """一次工具调用的最小事件完整性视图。"""

    call_id: str
    request_observed: bool
    terminal_observed: bool
    source_event_count: int


class ToolCallDiagnostics(TypedDict):
    """不包含原始工具正文的规范化诊断计数。"""

    duplicate_state_event_count: int
    duplicate_request_observation_count: int
    duplicate_result_observation_count: int
    orphan_result_count: int
    malformed_event_count: int


class NormalizedToolCallTrace(TypedDict):
    """供确定性评测器消费的最小 Trace 切片。"""

    trace_id: str | None
    mode: str
    tool_calls: tuple[NormalizedToolCall, ...]
    diagnostics: ToolCallDiagnostics
    normalization_error: str | None


_REQUEST_STATUSES = frozenset({"pending", "running"})
_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


def normalize_chat_tool_call_events(trace: Mapping[str, object]) -> NormalizedToolCallTrace:
    """按新版 canvas 结构合并普通工具请求和结果。

    结果侧孤立记录可能是 worker 或 direct_answer 返回，因此只记录诊断，不反向生成
    普通工具调用。任何原始参数和响应正文都限制在本函数调用期间。
    """

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty_trace(
            trace_id=None,
            mode="chat",
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty_trace(
            trace_id=None,
            mode="chat",
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    trace_id = _optional_identifier(meta.get("traceId"))
    if trace_id is None:
        return _empty_trace(
            trace_id=None,
            mode="chat",
            normalization_error="missing_trace_id",
            malformed_event_count=1,
        )

    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _empty_trace(
            trace_id=trace_id,
            mode="chat",
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )

    calls: dict[str, NormalizedToolCall] = {}
    tool_names: dict[str, str] = {}
    results: list[Mapping[object, object]] = []
    duplicate_request_count = 0
    duplicate_result_count = 0
    orphan_result_count = 0
    malformed_event_count = 0
    normalization_error: str | None = None

    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue

            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
            else:
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_event_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    call_id = _optional_identifier(raw_call.get("toolId"))
                    tool_name = _optional_identifier(raw_call.get("toolName"))
                    agent_name = _optional_identifier(raw_call.get("agentName"))
                    if call_id is None and tool_name is None and agent_name is not None:
                        continue
                    if call_id is None and tool_name is not None:
                        malformed_event_count += 1
                        normalization_error = normalization_error or "missing_call_id"
                        continue
                    if call_id is not None and tool_name is None:
                        malformed_event_count += 1
                        normalization_error = normalization_error or "missing_tool_name"
                        continue
                    if call_id is None or tool_name is None:
                        malformed_event_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue

                    existing_name = tool_names.get(call_id)
                    if existing_name is not None:
                        calls[call_id]["source_event_count"] += 1
                        duplicate_request_count += 1
                        if existing_name != tool_name:
                            normalization_error = (
                                normalization_error or "conflicting_call_definition"
                            )
                        continue
                    tool_names[call_id] = tool_name
                    calls[call_id] = {
                        "call_id": call_id,
                        "request_observed": True,
                        "terminal_observed": False,
                        "source_event_count": 1,
                    }

            raw_results = content.get("tool_result", ())
            if raw_results is None:
                raw_results = ()
            if not _is_sequence(raw_results):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
            else:
                for raw_result in raw_results:
                    if not isinstance(raw_result, Mapping):
                        malformed_event_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    if _optional_identifier(raw_result.get("toolId")) is None:
                        malformed_event_count += 1
                        normalization_error = normalization_error or "missing_call_id"
                        continue
                    results.append(raw_result)

    for result in results:
        call_id = _optional_identifier(result.get("toolId"))
        assert call_id is not None
        call = calls.get(call_id)
        if call is None:
            orphan_result_count += 1
            continue
        result_tool_name = _optional_identifier(result.get("toolName"))
        if result_tool_name is not None and result_tool_name != tool_names[call_id]:
            normalization_error = normalization_error or "conflicting_call_definition"
        if call["terminal_observed"] is True:
            duplicate_result_count += 1
        call["terminal_observed"] = True
        call["source_event_count"] += 1

    return {
        "trace_id": trace_id,
        "mode": "chat",
        "tool_calls": tuple(calls.values()),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "duplicate_request_observation_count": duplicate_request_count,
            "duplicate_result_observation_count": duplicate_result_count,
            "orphan_result_count": orphan_result_count,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": normalization_error,
    }


def normalize_research_tool_call_events(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolCallTrace:
    """按 `callID` 合并研究模式工具状态事件。

    缺少 `callID` 时无法判断多条状态是否属于同一调用。严格策略会让整次会话不可
    计算，而不是生成临时标识并冒险放大分母。
    """

    calls: dict[str, NormalizedToolCall] = {}
    observed_statuses: dict[str, set[str]] = {}
    duplicate_state_event_count = 0
    malformed_event_count = 0
    resolved_session_id = session_id
    resolved_user_message_id = user_message_id

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if resolved_session_id is None:
            resolved_session_id = _optional_identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _optional_identifier(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue

        part = event.get("part")
        if not isinstance(part, Mapping):
            malformed_event_count += 1
            continue
        if part.get("type") != "tool":
            continue

        call_id = _required_identifier(part.get("callID"))
        if call_id is None:
            return _empty_trace(
                trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
                mode="research",
                normalization_error="missing_call_id",
                malformed_event_count=malformed_event_count + 1,
                duplicate_state_event_count=duplicate_state_event_count,
            )

        call = calls.setdefault(
            call_id,
            {
                "call_id": call_id,
                "request_observed": False,
                "terminal_observed": False,
                "source_event_count": 0,
            },
        )
        call["source_event_count"] += 1

        state = part.get("state")
        if not isinstance(state, Mapping):
            malformed_event_count += 1
            continue
        status_value = state.get("status")
        if not isinstance(status_value, str) or not status_value.strip():
            malformed_event_count += 1
            continue
        status = status_value.strip().lower()

        statuses = observed_statuses.setdefault(call_id, set())
        if status in statuses:
            duplicate_state_event_count += 1
        else:
            statuses.add(status)

        if status in _REQUEST_STATUSES:
            call["request_observed"] = True
        if status in _TERMINAL_STATUSES:
            call["terminal_observed"] = True

    return {
        "trace_id": _research_trace_id(resolved_session_id, resolved_user_message_id),
        "mode": "research",
        "tool_calls": tuple(calls.values()),
        "diagnostics": {
            "duplicate_state_event_count": duplicate_state_event_count,
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": None,
    }


def _empty_trace(
    *,
    trace_id: str | None,
    mode: str,
    normalization_error: str,
    malformed_event_count: int,
    duplicate_state_event_count: int = 0,
) -> NormalizedToolCallTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "tool_calls": (),
        "diagnostics": {
            "duplicate_state_event_count": duplicate_state_event_count,
            "duplicate_request_observation_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": normalization_error,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _optional_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_identifier(value: object) -> str | None:
    return _optional_identifier(value)


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
