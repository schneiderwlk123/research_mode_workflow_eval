"""规范化聊天与研究模式的端到端时延边界。"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypedDict, TypeGuard


class EndToEndLatencyDiagnostics(TypedDict):
    """只保留边界去重计数，不持久化请求或回答正文。"""

    duplicate_boundary_event_count: int


class NormalizedEndToEndLatencyTrace(TypedDict):
    """供端到端时延评估器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    request_start_ms: int | None
    answer_end_ms: int | None
    reported_duration_ms: int | None
    timestamp_precision: Literal["seconds", "milliseconds"]
    diagnostics: EndToEndLatencyDiagnostics
    normalization_error: str | None


def normalize_chat_end_to_end_latency(
    trace: Mapping[str, object],
) -> NormalizedEndToEndLatencyTrace:
    """使用 canvas 创建时间和最终回答创建时间构造聊天模式墙钟时延边界。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "seconds", "invalid_chat_trace_structure")
    meta = canvas.get("meta")
    answer = canvas.get("answer")
    if not isinstance(meta, Mapping) or (answer is not None and not isinstance(answer, Mapping)):
        return _empty("chat", None, "seconds", "invalid_chat_trace_structure")

    trace_id = _text(meta.get("traceId"))
    start_state, start_ms = _timestamp_ms(meta.get("create_time"))
    end_value = answer.get("create_time") if isinstance(answer, Mapping) else None
    end_state, end_ms = _timestamp_ms(end_value)
    error = _boundary_error(start_state, end_state)
    return {
        "trace_id": trace_id,
        "mode": "chat",
        "request_start_ms": start_ms,
        "answer_end_ms": end_ms,
        # answer.durationSeconds 只是终止 Agent 消息耗时，不能当作 Trace 总耗时。
        "reported_duration_ms": None,
        "timestamp_precision": "seconds",
        "diagnostics": {"duplicate_boundary_event_count": 0},
        "normalization_error": error,
    }


def normalize_research_end_to_end_latency(
    events: Sequence[object],
) -> NormalizedEndToEndLatencyTrace:
    """合并研究回合边界，保留 turn.end 报告耗时和时间戳交叉校验输入。"""

    if not _is_sequence(events):
        return _empty("research", None, "milliseconds", "invalid_research_trace_structure")

    start: tuple[int | None, str | None, str | None] | None = None
    end: tuple[int | None, int | None, str | None, str | None] | None = None
    duplicate_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("type")
        if event_type not in {"turn.start", "turn.end"}:
            continue

        timestamp_state, timestamp_ms = _timestamp_ms(event.get("ts"))
        if timestamp_state == "invalid":
            return _empty("research", None, "milliseconds", "invalid_timestamp")
        session_id = _text(event.get("sessionID"))
        message_id = _text(event.get("userMessageID"))
        if event_type == "turn.start":
            start_candidate = (timestamp_ms, session_id, message_id)
            if start is None:
                start = start_candidate
            elif start == start_candidate:
                duplicate_count += 1
            else:
                return _empty(
                    "research",
                    _research_trace_id(session_id, message_id),
                    "milliseconds",
                    "conflicting_turn_boundary",
                    duplicate_count,
                )
            continue

        duration_state, duration_ms = _duration_ms(event.get("durationMs"))
        if duration_state == "invalid":
            return _empty("research", None, "milliseconds", "invalid_duration")
        end_candidate = (timestamp_ms, duration_ms, session_id, message_id)
        if end is None:
            end = end_candidate
        elif end == end_candidate:
            duplicate_count += 1
        else:
            return _empty(
                "research",
                _research_trace_id(session_id, message_id),
                "milliseconds",
                "conflicting_turn_boundary",
                duplicate_count,
            )

    start_ms = start[0] if start is not None else None
    end_ms = end[0] if end is not None else None
    reported_ms = end[1] if end is not None else None
    if (
        start is not None
        and end is not None
        and (_conflicts(start[1], end[2]) or _conflicts(start[2], end[3]))
    ):
        return _empty(
            "research",
            _research_trace_id(start[1], start[2]),
            "milliseconds",
            "conflicting_turn_boundary",
            duplicate_count,
        )
    session_id = (start[1] if start is not None else None) or (end[2] if end else None)
    message_id = (start[2] if start is not None else None) or (end[3] if end else None)
    error = _boundary_error(
        "missing" if start is None or start_ms is None else "valid",
        "missing" if end is None or end_ms is None else "valid",
    )
    return {
        "trace_id": _research_trace_id(session_id, message_id),
        "mode": "research",
        "request_start_ms": start_ms,
        "answer_end_ms": end_ms,
        "reported_duration_ms": reported_ms,
        "timestamp_precision": "milliseconds",
        "diagnostics": {"duplicate_boundary_event_count": duplicate_count},
        "normalization_error": error,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    precision: Literal["seconds", "milliseconds"],
    error: str,
    duplicate_count: int = 0,
) -> NormalizedEndToEndLatencyTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "request_start_ms": None,
        "answer_end_ms": None,
        "reported_duration_ms": None,
        "timestamp_precision": precision,
        "diagnostics": {"duplicate_boundary_event_count": duplicate_count},
        "normalization_error": error,
    }


def _boundary_error(start_state: str, end_state: str) -> str | None:
    if start_state == "invalid" or end_state == "invalid":
        return "invalid_timestamp"
    if start_state == "missing":
        return "missing_start_timestamp"
    if end_state == "missing":
        return "missing_end_timestamp"
    return None


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
    # 上游无时区字符串沿用现有 collector 约定按 UTC 解释，避免依赖运行机器时区。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return "valid", round(parsed.timestamp() * 1000)


def _duration_ms(value: object) -> tuple[str, int | None]:
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
    return "valid", round(numeric)


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _conflicts(left: str | None, right: str | None) -> bool:
    return left is not None and right is not None and left != right


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
