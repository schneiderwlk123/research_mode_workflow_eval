"""组合逐工具耗时统计，供最大工具时延占比评测。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from collectors.tool_call_latency import (
    NormalizedToolCallLatencyTrace,
    normalize_chat_tool_call_latency,
    normalize_research_tool_call_latency,
)

_CHAT_AGENT_RETURN_TOOL_NAMES = frozenset({"respond"})
_DURATION_ERRORS = frozenset(
    {"invalid_duration", "invalid_event_order", "conflicting_duration_observation"}
)


class ToolLatencyTotal(TypedDict):
    tool_name: str
    tool_latency_total_ms: int


class NormalizedMaxToolLatencyShareTrace(TypedDict):
    trace_id: str | None
    mode: Literal["chat", "research"]
    total_tool_latency_ms: int | None
    per_tool_latency_totals: tuple[ToolLatencyTotal, ...]
    diagnostics: dict[str, int]
    normalization_error: str | None


def normalize_chat_max_tool_latency_share(
    trace: Mapping[str, object],
) -> NormalizedMaxToolLatencyShareTrace:
    """聚合聊天业务工具耗时，并排除结果侧 Agent 返回。"""

    return _combine(normalize_chat_tool_call_latency(trace), exclude_agent_returns=True)


def normalize_research_max_tool_latency_share(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedMaxToolLatencyShareTrace:
    """聚合研究模式业务工具耗时。"""

    return _combine(
        normalize_research_tool_call_latency(
            events,
            session_id=session_id,
            user_message_id=user_message_id,
        ),
        exclude_agent_returns=False,
    )


def _combine(
    trace: NormalizedToolCallLatencyTrace, *, exclude_agent_returns: bool
) -> NormalizedMaxToolLatencyShareTrace:
    diagnostics: dict[str, int] = {
        "duration_missing_count": trace["diagnostics"]["duration_missing_count"],
        "duplicate_observation_count": trace["diagnostics"]["duplicate_observation_count"],
        "orphan_result_count": trace["diagnostics"]["orphan_result_count"],
        "malformed_event_count": trace["diagnostics"]["malformed_event_count"],
    }
    statistics = trace["per_tool_statistics"]
    if exclude_agent_returns:
        excluded = tuple(
            item
            for item in statistics
            if item["tool_name"].casefold() in _CHAT_AGENT_RETURN_TOOL_NAMES
        )
        statistics = tuple(
            item
            for item in statistics
            if item["tool_name"].casefold() not in _CHAT_AGENT_RETURN_TOOL_NAMES
        )
        diagnostics["excluded_agent_return_count"] = sum(item["call_count"] for item in excluded)
        # 排除结果侧 Agent 后，缺失计数也必须使用相同业务工具范围。
        diagnostics["duration_missing_count"] -= sum(
            item["latency_missing_count"] for item in excluded
        )
    else:
        diagnostics["excluded_agent_return_count"] = 0

    source_error = trace["normalization_error"]
    if source_error is not None and source_error != "no_tool_calls":
        return _empty(trace, diagnostics, source_error)
    if not statistics:
        return _empty(trace, diagnostics, "no_tool_calls")

    errors = sorted(
        {
            reason
            for item in statistics
            for reason in item["duration_error_reasons"]
            if reason in _DURATION_ERRORS
        }
    )
    if errors:
        return _empty(trace, diagnostics, errors[0])
    if any(item["latency_missing_count"] for item in statistics):
        return _empty(trace, diagnostics, "incomplete_tool_duration_coverage")

    totals: tuple[ToolLatencyTotal, ...] = tuple(
        {
            "tool_name": item["tool_name"],
            "tool_latency_total_ms": item["tool_latency_total_ms"],
        }
        for item in statistics
    )
    total = sum(item["tool_latency_total_ms"] for item in totals)
    if total == 0:
        return _empty(trace, diagnostics, "zero_tool_duration")
    return {
        "trace_id": trace["trace_id"],
        "mode": trace["mode"],
        "total_tool_latency_ms": total,
        "per_tool_latency_totals": totals,
        "diagnostics": diagnostics,
        "normalization_error": None,
    }


def _empty(
    trace: NormalizedToolCallLatencyTrace,
    diagnostics: dict[str, int],
    error: str,
) -> NormalizedMaxToolLatencyShareTrace:
    return {
        "trace_id": trace["trace_id"],
        "mode": trace["mode"],
        "total_tool_latency_ms": None,
        "per_tool_latency_totals": (),
        "diagnostics": diagnostics,
        "normalization_error": error,
    }
