"""把逐调用耗时安全聚合为逐工具时延统计。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from collectors.tool_resource_usage import (
    NormalizedToolResourceTrace,
    normalize_chat_tool_resource_usage,
    normalize_research_tool_resource_usage,
)


class PerToolLatencyStatistics(TypedDict):
    """同名工具的有效、缺失和错误耗时统计。"""

    tool_name: str
    call_count: int
    latency_observed_count: int
    latency_missing_count: int
    latency_error_count: int
    tool_latency_total_ms: int
    duration_error_reasons: tuple[str, ...]


class ToolCallLatencyDiagnostics(TypedDict):
    """继承逐调用规范化阶段的安全诊断计数。"""

    duration_missing_count: int
    duplicate_observation_count: int
    orphan_result_count: int
    malformed_event_count: int


class NormalizedToolCallLatencyTrace(TypedDict):
    """一个 Trace 的逐工具时延聚合结果。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    per_tool_statistics: tuple[PerToolLatencyStatistics, ...]
    diagnostics: ToolCallLatencyDiagnostics
    normalization_error: str | None


def normalize_chat_tool_call_latency(
    trace: Mapping[str, object],
) -> NormalizedToolCallLatencyTrace:
    """复用聊天工具生命周期关联，再按精确工具名汇总耗时。"""

    return _aggregate(normalize_chat_tool_resource_usage(trace))


def normalize_research_tool_call_latency(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolCallLatencyTrace:
    """复用研究工具生命周期合并，再按精确工具名汇总耗时。"""

    return _aggregate(
        normalize_research_tool_resource_usage(
            events,
            session_id=session_id,
            user_message_id=user_message_id,
        )
    )


def _aggregate(trace: NormalizedToolResourceTrace) -> NormalizedToolCallLatencyTrace:
    call_counts: dict[str, int] = {}
    observed_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}
    duration_totals: dict[str, int] = {}
    error_reasons: dict[str, set[str]] = {}
    for call in trace["tool_calls"]:
        tool_name = call["tool_name"]
        call_counts[tool_name] = call_counts.get(tool_name, 0) + 1
        duration = call["duration_ms"]
        error = call["duration_error"]
        if duration is not None:
            observed_counts[tool_name] = observed_counts.get(tool_name, 0) + 1
            duration_totals[tool_name] = duration_totals.get(tool_name, 0) + duration
        elif error == "missing_tool_duration":
            missing_counts[tool_name] = missing_counts.get(tool_name, 0) + 1
        elif error is not None:
            error_reasons.setdefault(tool_name, set()).add(error)

    statistics: list[PerToolLatencyStatistics] = []
    for tool_name, call_count in sorted(call_counts.items()):
        observed = observed_counts.get(tool_name, 0)
        missing = missing_counts.get(tool_name, 0)
        statistics.append(
            {
                "tool_name": tool_name,
                "call_count": call_count,
                "latency_observed_count": observed,
                "latency_missing_count": missing,
                "latency_error_count": call_count - observed - missing,
                "tool_latency_total_ms": duration_totals.get(tool_name, 0),
                "duration_error_reasons": tuple(sorted(error_reasons.get(tool_name, set()))),
            }
        )
    return {
        "trace_id": trace["trace_id"],
        "mode": trace["mode"],
        "per_tool_statistics": tuple(statistics),
        "diagnostics": trace["diagnostics"],
        "normalization_error": trace["normalization_error"],
    }
