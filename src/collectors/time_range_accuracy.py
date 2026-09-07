"""构造时间范围提取评测所需的内存候选。"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict, TypeGuard

from collectors.parameter_value_accuracy import (
    NormalizedParameterValueTrace,
    ParameterValueCandidate,
    ParameterValueCatalog,
    normalize_chat_parameter_value_candidates,
    normalize_research_parameter_value_candidates,
)


@dataclass(frozen=True, slots=True)
class TimeRangeCandidate:
    """单会话时间评测候选；正文不得进入持久化结果。"""

    query: str
    reference_time: str | None
    tool_calls: tuple[ParameterValueCandidate, ...]


class TimeRangeDiagnostics(TypedDict):
    """不包含 query、参数值或时间原文的安全诊断。"""

    unknown_schema_call_count: int
    malformed_arguments_call_count: int
    duplicate_call_definition_count: int
    malformed_event_count: int


class NormalizedTimeRangeTrace(TypedDict):
    """供时间范围评测器消费的规范化会话。"""

    trace_id: str | None
    mode: str
    manual_version: str
    tool_call_count: int
    reference_time_source: str
    candidate: TimeRangeCandidate | None
    diagnostics: TimeRangeDiagnostics
    normalization_error: str | None


def normalize_chat_time_range(
    trace: Mapping[str, object],
    catalog: ParameterValueCatalog,
    *,
    reference_time: object = None,
) -> NormalizedTimeRangeTrace:
    """提取聊天 query、工具参数和可复现的相对时间锚点。"""

    base = normalize_chat_parameter_value_candidates(trace, catalog)
    query = _chat_meta_text(trace, "query")
    resolved_reference, source = _resolve_reference_time(
        reference_time,
        _chat_reference_time(trace),
        inferred_source="canvas_meta",
    )
    error = base["normalization_error"]
    if base["diagnostics"]["malformed_arguments_call_count"] > 0:
        error = error or "incomplete_time_observation"
    if (
        error is None
        and base["total_call_count"] == 0
        and _is_pre_time_failure(_chat_answer(trace))
    ):
        error = "pre_time_extraction_failure"
    candidate = None
    if error is None and query is not None:
        candidate = TimeRangeCandidate(query, resolved_reference, base["candidates"])
    return _build_result(base, source, candidate, error)


def normalize_research_time_range(
    events: Sequence[object],
    catalog: ParameterValueCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
    reference_time: object = None,
) -> NormalizedTimeRangeTrace:
    """提取研究 query、去重调用参数和事件时间锚点。"""

    base = normalize_research_parameter_value_candidates(
        events,
        catalog,
        session_id=session_id,
        user_message_id=user_message_id,
    )
    query = _research_query(events)
    resolved_reference, source = _resolve_reference_time(
        reference_time,
        _research_reference_time(events),
        inferred_source="event",
    )
    error = base["normalization_error"]
    if base["diagnostics"]["malformed_arguments_call_count"] > 0:
        error = error or "incomplete_time_observation"
    candidate = None
    if error is None and query is not None:
        candidate = TimeRangeCandidate(query, resolved_reference, base["candidates"])
    return _build_result(base, source, candidate, error)


def _build_result(
    base: NormalizedParameterValueTrace,
    reference_time_source: str,
    candidate: TimeRangeCandidate | None,
    error: str | None,
) -> NormalizedTimeRangeTrace:
    diagnostics = base["diagnostics"]
    assert isinstance(diagnostics, Mapping)
    return {
        "trace_id": base["trace_id"] if isinstance(base["trace_id"], str) else None,
        "mode": str(base["mode"]),
        "manual_version": str(base["manual_version"]),
        "tool_call_count": int(base["total_call_count"]),
        "reference_time_source": reference_time_source,
        "candidate": candidate,
        "diagnostics": {
            "unknown_schema_call_count": int(diagnostics["unknown_schema_call_count"]),
            "malformed_arguments_call_count": int(diagnostics["malformed_arguments_call_count"]),
            "duplicate_call_definition_count": int(diagnostics["duplicate_call_definition_count"]),
            "malformed_event_count": int(diagnostics["malformed_event_count"]),
        },
        "normalization_error": error,
    }


def _resolve_reference_time(
    provided: object,
    inferred: object,
    *,
    inferred_source: str,
) -> tuple[str | None, str]:
    normalized = _timestamp_text(provided)
    if normalized is not None:
        return normalized, "provided"
    normalized = _timestamp_text(inferred)
    if normalized is not None:
        return normalized, inferred_source
    return None, "missing"


def _chat_reference_time(trace: Mapping[str, object]) -> object:
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return None
    meta = canvas.get("meta")
    if isinstance(meta, Mapping):
        return meta.get("create_time")
    return None


def _chat_meta_text(trace: Mapping[str, object], name: str) -> str | None:
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return None
    meta = canvas.get("meta")
    return _text(meta.get(name)) if isinstance(meta, Mapping) else None


def _chat_answer(trace: Mapping[str, object]) -> object:
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return None
    answer = canvas.get("answer")
    return answer.get("answer") if isinstance(answer, Mapping) else None


def _research_reference_time(events: Sequence[object]) -> object:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        for name in ("timestamp", "create_time"):
            if event.get(name) is not None:
                return event.get(name)
        part = event.get("part")
        if not isinstance(part, Mapping):
            continue
        state = part.get("state")
        if not isinstance(state, Mapping):
            continue
        time_value = state.get("time")
        if isinstance(time_value, Mapping) and time_value.get("start") is not None:
            return time_value.get("start")
    return None


def _research_query(events: Sequence[object]) -> str | None:
    for event in events:
        if isinstance(event, Mapping):
            direct = _text(event.get("query"))
            if direct is not None:
                return direct
    for event in events:
        if not isinstance(event, Mapping):
            continue
        body = event.get("body")
        if isinstance(body, Mapping):
            nested = _text(body.get("query"))
            if nested is not None:
                return nested
    return None


def _timestamp_text(value: object) -> str | None:
    text = _text(value)
    if text is not None:
        return text
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    if abs(numeric) >= 100_000_000_000:
        numeric /= 1000
    try:
        return datetime.fromtimestamp(numeric, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _is_pre_time_failure(value: object) -> bool:
    text = _text(value)
    return text is not None and text.rstrip("。") == "模型服务暂时不可用，请稍后重试"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
