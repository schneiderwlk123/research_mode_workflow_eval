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
    """规范化单条聊天 Trace 的工具调用事件。

    当前聊天日志没有跨事件稳定调用标识，因此按 `tool_call_queue` 中工具调用对象的
    出现顺序生成会话内标识。规范化过程只记录字段是否存在，不复制请求或响应正文。
    """

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _empty_trace(
            trace_id=_optional_identifier(trace.get("traceId")),
            mode="chat",
            normalization_error="invalid_tool_call_queue",
            malformed_event_count=1,
        )

    calls: list[NormalizedToolCall] = []
    malformed_event_count = 0
    for event in raw_queue:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if event.get("event_type") != "tool_call":
            continue

        call_index = len(calls)
        calls.append(
            {
                "call_id": f"chat-tool-call-{call_index}",
                "request_observed": _has_non_empty_value(event, "tool_request_args"),
                "terminal_observed": _has_non_empty_value(event, "tool_response_result"),
                "source_event_count": 1,
            }
        )

    return {
        "trace_id": _optional_identifier(trace.get("traceId")),
        "mode": "chat",
        "tool_calls": tuple(calls),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": None,
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


def _has_non_empty_value(mapping: Mapping[object, object], key: str) -> bool:
    if key not in mapping:
        return False
    value = mapping[key]
    if value is None:
        return False
    if isinstance(value, (str, bytes, Mapping, Sequence)):
        return len(value) > 0
    return True


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
