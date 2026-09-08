"""规范化聊天与研究模式的模型步骤时延观测。"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypedDict, TypeGuard


class ModelStepLatencyDiagnostics(TypedDict):
    """步骤覆盖和研究边界交叉校验的安全诊断。"""

    valid_step_count: int
    latency_missing_count: int
    duplicate_boundary_event_count: int
    timestamp_cross_check_count: int
    max_abs_boundary_difference_ms: int | None


class NormalizedModelStepLatencyTrace(TypedDict):
    """供平均模型步骤时延评估器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    step_durations_ms: tuple[int, ...]
    diagnostics: ModelStepLatencyDiagnostics
    normalization_error: str | None


def normalize_chat_model_step_latency(
    trace: Mapping[str, object],
) -> NormalizedModelStepLatencyTrace:
    """读取每条 Agent message 自身耗时，且不重复读取最终 answer 的复制值。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure")

    trace_id = _text(meta.get("traceId"))
    durations: list[int] = []
    missing_count = 0
    for agent in agents:
        if not isinstance(agent, Mapping):
            return _empty("chat", trace_id, "invalid_chat_trace_structure")
        messages = agent.get("messages")
        if not _is_sequence(messages):
            return _empty("chat", trace_id, "invalid_chat_trace_structure")
        for message in messages:
            if not isinstance(message, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure")
            content = message.get("content")
            if not isinstance(content, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure")
            state, duration_ms = _seconds_duration_ms(content.get("durationSeconds"))
            if state == "missing":
                missing_count += 1
            elif state == "invalid":
                return _empty("chat", trace_id, "invalid_duration", missing_count)
            elif state == "negative":
                return _empty("chat", trace_id, "invalid_event_order", missing_count)
            elif duration_ms is not None:
                durations.append(duration_ms)
    return _finalize("chat", trace_id, durations, missing_count)


def normalize_research_model_step_latency(
    events: Sequence[object],
) -> NormalizedModelStepLatencyTrace:
    """按 messageID 配对 step-start/step-finish，以事件 time 计算模型循环时延。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    session_id: str | None = None
    user_message_id: str | None = None
    boundaries: dict[str, dict[str, tuple[int, int | None]]] = {}
    duplicate_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "turn.start":
            session_id = session_id or _text(event.get("sessionID"))
            user_message_id = user_message_id or _text(event.get("userMessageID"))
            continue
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            continue
        part_type = part.get("type")
        if part_type not in {"step-start", "step-finish"}:
            continue
        message_id = _text(part.get("messageID"))
        if message_id is None:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "missing_step_id",
                duplicate_count=duplicate_count,
            )
        state, boundary_ms = _event_time_ms(event.get("time"))
        if state != "valid" or boundary_ms is None:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "invalid_timestamp",
                duplicate_count=duplicate_count,
            )
        ts_ms = _optional_iso_timestamp_ms(event.get("ts"))
        kind = "start" if part_type == "step-start" else "finish"
        message_boundaries = boundaries.setdefault(message_id, {})
        existing = message_boundaries.get(kind)
        if existing is None:
            message_boundaries[kind] = (boundary_ms, ts_ms)
        elif existing[0] == boundary_ms:
            duplicate_count += 1
        else:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "conflicting_step_boundary",
                duplicate_count=duplicate_count,
            )

    durations: list[int] = []
    missing_count = 0
    cross_differences: list[int] = []
    for message_boundaries in boundaries.values():
        start = message_boundaries.get("start")
        finish = message_boundaries.get("finish")
        if start is None or finish is None:
            missing_count += 1
            continue
        duration_ms = finish[0] - start[0]
        if duration_ms < 0:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "invalid_event_order",
                missing_count,
                duplicate_count,
            )
        durations.append(duration_ms)
        if start[1] is not None and finish[1] is not None:
            cross_differences.append(abs((finish[1] - start[1]) - duration_ms))

    return _finalize(
        "research",
        _research_trace_id(session_id, user_message_id),
        durations,
        missing_count,
        duplicate_count,
        cross_differences,
    )


def _finalize(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    durations: list[int],
    missing_count: int,
    duplicate_count: int = 0,
    cross_differences: list[int] | None = None,
) -> NormalizedModelStepLatencyTrace:
    differences = cross_differences or []
    return {
        "trace_id": trace_id,
        "mode": mode,
        "step_durations_ms": tuple(durations),
        "diagnostics": {
            "valid_step_count": len(durations),
            "latency_missing_count": missing_count,
            "duplicate_boundary_event_count": duplicate_count,
            "timestamp_cross_check_count": len(differences),
            "max_abs_boundary_difference_ms": max(differences) if differences else None,
        },
        "normalization_error": None if durations else "no_observable_model_steps",
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    missing_count: int = 0,
    duplicate_count: int = 0,
) -> NormalizedModelStepLatencyTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "step_durations_ms": (),
        "diagnostics": {
            "valid_step_count": 0,
            "latency_missing_count": missing_count,
            "duplicate_boundary_event_count": duplicate_count,
            "timestamp_cross_check_count": 0,
            "max_abs_boundary_difference_ms": None,
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


def _event_time_ms(value: object) -> tuple[str, int | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "invalid", None
    try:
        numeric = float(value)
    except OverflowError:
        return "invalid", None
    if not math.isfinite(numeric):
        return "invalid", None
    return "valid", round(numeric)


def _optional_iso_timestamp_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return round(parsed.timestamp() * 1000)


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
