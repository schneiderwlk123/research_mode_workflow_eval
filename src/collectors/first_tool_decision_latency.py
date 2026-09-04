"""提取当前请求到首次工具或子 Agent 决策所需的安全时间切片。"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TypedDict, TypeGuard


class FirstToolDecisionDiagnostics(TypedDict):
    """不包含请求或工具正文的时延诊断计数。"""

    decision_event_count: int
    malformed_decision_event_count: int
    duplicate_pending_event_count: int


class NormalizedFirstToolDecisionTrace(TypedDict):
    """供首次工具决策时延评测器消费的规范化输入。"""

    trace_id: str | None
    mode: str
    request_start_ms: int | None
    first_decision_ms: int | None
    decision_source: str | None
    diagnostics: FirstToolDecisionDiagnostics
    normalization_error: str | None


def normalize_chat_first_tool_decision_latency(
    trace: Mapping[str, object],
) -> NormalizedFirstToolDecisionTrace:
    """计算聊天请求创建时间到最早工具链决策事件的时间边界。"""

    trace_id = _text(trace.get("traceId"))
    start_status, request_start_ms = _timestamp_ms(trace.get("create_time"))
    if start_status == "missing":
        return _empty(trace_id, "chat", "missing_turn_start_time")
    if start_status == "invalid":
        return _empty(trace_id, "chat", "invalid_timestamp")

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _empty(trace_id, "chat", "invalid_tool_call_queue", malformed=1)

    decisions: list[tuple[int, int, str]] = []
    decision_count = 0
    malformed_count = 0
    for position, event in enumerate(raw_queue):
        if not isinstance(event, Mapping):
            malformed_count += 1
            continue
        event_type = event.get("event_type")
        if event_type == "call_sub_agent_begin":
            decision_count += 1
            payload = event.get("request_payload")
            source = "sub_agent"
        elif event_type == "tool_call":
            decision_count += 1
            payload = event.get("tool_request_args")
            source = "tool_call"
        else:
            continue
        if not isinstance(payload, Mapping) or payload.get("create_time") is None:
            malformed_count += 1
            continue
        status, timestamp_ms = _timestamp_ms(payload.get("create_time"))
        if status == "invalid":
            return _empty(
                trace_id,
                "chat",
                "invalid_timestamp",
                decision_count=decision_count,
                malformed=malformed_count + 1,
            )
        if timestamp_ms is None:
            malformed_count += 1
            continue
        decisions.append((timestamp_ms, position, source))

    diagnostics = _diagnostics(decision_count, malformed_count, 0)
    if malformed_count:
        return _result(
            trace_id,
            "chat",
            request_start_ms,
            None,
            None,
            diagnostics,
            "incomplete_tool_decision_observation",
        )
    if not decisions:
        return _result(
            trace_id,
            "chat",
            request_start_ms,
            None,
            None,
            diagnostics,
            "no_tool_decision",
        )
    first_decision_ms, _, source = min(decisions)
    return _result(
        trace_id,
        "chat",
        request_start_ms,
        first_decision_ms,
        source,
        diagnostics,
        None,
    )


def normalize_research_first_tool_decision_latency(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedFirstToolDecisionTrace:
    """提取研究回合开始时间与最早工具 pending 事件时间。"""

    resolved_session_id = _text(session_id)
    resolved_user_message_id = _text(user_message_id)
    start_times: set[int] = set()
    tool_event_observed = False
    pending_by_call: dict[str, int] = {}
    malformed_count = 0
    duplicate_count = 0

    for event in events:
        if not isinstance(event, Mapping):
            continue
        resolved_session_id = resolved_session_id or _text(event.get("sessionID"))
        resolved_user_message_id = resolved_user_message_id or _text(event.get("userMessageID"))
        if event.get("type") == "turn.start":
            status, timestamp_ms = _timestamp_ms(event.get("ts"))
            if status == "missing":
                return _empty(
                    _research_trace_id(resolved_session_id, resolved_user_message_id),
                    "research",
                    "missing_turn_start_time",
                )
            if status == "invalid" or timestamp_ms is None:
                return _empty(
                    _research_trace_id(resolved_session_id, resolved_user_message_id),
                    "research",
                    "invalid_timestamp",
                )
            start_times.add(timestamp_ms)

        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        tool_event_observed = True
        state = part.get("state")
        status_text = _text(state.get("status")) if isinstance(state, Mapping) else None
        if status_text is None or status_text.lower() != "pending":
            continue
        call_id = _text(part.get("callID"))
        if call_id is None:
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                "missing_call_id",
            )
        status, timestamp_ms = _timestamp_ms(event.get("ts"))
        if status == "missing":
            malformed_count += 1
            continue
        if status == "invalid" or timestamp_ms is None:
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                "invalid_timestamp",
                decision_count=len(pending_by_call),
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        existing = pending_by_call.get(call_id)
        if existing is not None:
            duplicate_count += 1
            pending_by_call[call_id] = min(existing, timestamp_ms)
        else:
            pending_by_call[call_id] = timestamp_ms

    trace_id = _research_trace_id(resolved_session_id, resolved_user_message_id)
    if not start_times:
        return _empty(trace_id, "research", "missing_turn_start_time")
    if len(start_times) != 1:
        return _empty(trace_id, "research", "conflicting_turn_start")
    request_start_ms = next(iter(start_times))
    diagnostics = _diagnostics(len(pending_by_call), malformed_count, duplicate_count)
    if malformed_count:
        return _result(
            trace_id,
            "research",
            request_start_ms,
            None,
            None,
            diagnostics,
            "incomplete_tool_decision_observation",
        )
    if not pending_by_call:
        reason = "missing_tool_decision_time" if tool_event_observed else "no_tool_decision"
        return _result(trace_id, "research", request_start_ms, None, None, diagnostics, reason)
    return _result(
        trace_id,
        "research",
        request_start_ms,
        min(pending_by_call.values()),
        "tool_pending",
        diagnostics,
        None,
    )


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


def _empty(
    trace_id: str | None,
    mode: str,
    error: str,
    *,
    decision_count: int = 0,
    malformed: int = 0,
    duplicate: int = 0,
) -> NormalizedFirstToolDecisionTrace:
    return _result(
        trace_id,
        mode,
        None,
        None,
        None,
        _diagnostics(decision_count, malformed, duplicate),
        error,
    )


def _result(
    trace_id: str | None,
    mode: str,
    request_start_ms: int | None,
    first_decision_ms: int | None,
    decision_source: str | None,
    diagnostics: FirstToolDecisionDiagnostics,
    error: str | None,
) -> NormalizedFirstToolDecisionTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "request_start_ms": request_start_ms,
        "first_decision_ms": first_decision_ms,
        "decision_source": decision_source,
        "diagnostics": diagnostics,
        "normalization_error": error,
    }


def _diagnostics(
    decision_count: int, malformed: int, duplicate: int
) -> FirstToolDecisionDiagnostics:
    return {
        "decision_event_count": decision_count,
        "malformed_decision_event_count": malformed,
        "duplicate_pending_event_count": duplicate,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
