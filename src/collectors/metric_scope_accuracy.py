"""构造指标与口径提取评测所需的内存候选。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from collectors.parameter_value_accuracy import (
    NormalizedParameterValueTrace,
    ParameterValueCandidate,
    ParameterValueCatalog,
    normalize_chat_parameter_value_candidates,
    normalize_research_parameter_value_candidates,
)


@dataclass(frozen=True, slots=True)
class MetricScopeCandidate:
    """需求与工具证据；正文只允许在 Judge 调用生命周期内存在。"""

    query: str
    tool_calls: tuple[ParameterValueCandidate, ...]


class NormalizedMetricScopeTrace(TypedDict):
    trace_id: str | None
    mode: str
    manual_version: str
    tool_call_count: int
    candidate: MetricScopeCandidate | None
    diagnostics: dict[str, int]
    normalization_error: str | None


def normalize_chat_metric_scope(
    trace: Mapping[str, object], catalog: ParameterValueCatalog
) -> NormalizedMetricScopeTrace:
    base = normalize_chat_parameter_value_candidates(trace, catalog)
    error = base["normalization_error"]
    if base["diagnostics"]["malformed_arguments_call_count"]:
        error = error or "incomplete_metric_observation"
    if error is None and base["total_call_count"] == 0 and _is_pre_failure(trace.get("answer")):
        error = "pre_metric_extraction_failure"
    query = _text(trace.get("query"))
    candidate = MetricScopeCandidate(query, base["candidates"]) if query and not error else None
    return _result(base, candidate, error)


def normalize_research_metric_scope(
    events: Sequence[object],
    catalog: ParameterValueCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedMetricScopeTrace:
    base = normalize_research_parameter_value_candidates(
        events, catalog, session_id=session_id, user_message_id=user_message_id
    )
    error = base["normalization_error"]
    if base["diagnostics"]["malformed_arguments_call_count"]:
        error = error or "incomplete_metric_observation"
    query = _research_query(events)
    candidate = MetricScopeCandidate(query, base["candidates"]) if query and not error else None
    return _result(base, candidate, error)


def _result(
    base: NormalizedParameterValueTrace,
    candidate: MetricScopeCandidate | None,
    error: str | None,
) -> NormalizedMetricScopeTrace:
    return {
        "trace_id": base["trace_id"],
        "mode": base["mode"],
        "manual_version": base["manual_version"],
        "tool_call_count": base["total_call_count"],
        "candidate": candidate,
        "diagnostics": {
            "unknown_schema_call_count": base["diagnostics"]["unknown_schema_call_count"],
            "malformed_arguments_call_count": base["diagnostics"]["malformed_arguments_call_count"],
            "duplicate_call_definition_count": base["diagnostics"][
                "duplicate_call_definition_count"
            ],
            "malformed_event_count": base["diagnostics"]["malformed_event_count"],
        },
        "normalization_error": error,
    }


def _research_query(events: Sequence[object]) -> str | None:
    for nested in (False, True):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            source = event.get("body") if nested else event
            if isinstance(source, Mapping) and (value := _text(source.get("query"))):
                return value
    return None


def _is_pre_failure(value: object) -> bool:
    text = _text(value)
    return text is not None and text.rstrip("。") == "模型服务暂时不可用，请稍后重试"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
