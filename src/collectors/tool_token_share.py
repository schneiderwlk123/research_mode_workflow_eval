"""组合逐工具关联 Token 与 Trace 总 Token，供最大占比评测。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from collectors.token_consumption import (
    NormalizedTokenConsumptionTrace,
    normalize_chat_token_consumption,
    normalize_research_token_consumption,
)
from collectors.tool_step_tokens import (
    NormalizedToolStepTokenTrace,
    normalize_chat_tool_step_tokens,
    normalize_research_tool_step_tokens,
)


class ToolAssociatedTokenTotal(TypedDict):
    tool_name: str
    tool_associated_token_total: int


class NormalizedToolTokenShareTrace(TypedDict):
    trace_id: str | None
    mode: Literal["chat", "research"]
    total_token_consumption: int | None
    per_tool_token_totals: tuple[ToolAssociatedTokenTotal, ...]
    diagnostics: dict[str, int]
    normalization_error: str | None


def normalize_chat_tool_token_share(
    trace: Mapping[str, object],
) -> NormalizedToolTokenShareTrace:
    return _combine(
        normalize_chat_tool_step_tokens(trace),
        normalize_chat_token_consumption(trace),
    )


def normalize_research_tool_token_share(
    events: Sequence[object],
) -> NormalizedToolTokenShareTrace:
    return _combine(
        normalize_research_tool_step_tokens(events),
        normalize_research_token_consumption(events),
    )


def _combine(
    tool_trace: NormalizedToolStepTokenTrace,
    total_trace: NormalizedTokenConsumptionTrace,
) -> NormalizedToolTokenShareTrace:
    mode = tool_trace["mode"]
    trace_id = tool_trace["trace_id"] or total_trace["trace_id"]
    tool_error = tool_trace["normalization_error"]
    if tool_error == "no_tool_calls":
        return _empty(mode, trace_id, "no_tool_calls")
    if tool_error is not None:
        return _empty(mode, trace_id, tool_error)

    statistics = tool_trace["per_tool_statistics"]
    if any(item["tool_step_count"] == 0 for item in statistics):
        return _empty(mode, trace_id, "missing_tool_step_association")
    if any(item["token_missing_count"] for item in statistics):
        return _empty(mode, trace_id, "incomplete_token_coverage")

    total_error = total_trace["normalization_error"]
    if total_error in {"no_observable_token_usage", "incomplete_token_coverage"}:
        return _empty(mode, trace_id, "incomplete_token_coverage")
    if total_error is not None:
        return _empty(mode, trace_id, total_error)
    total = sum(total_trace["step_token_totals"])
    if total == 0:
        return _empty(mode, trace_id, "zero_total_token_consumption")

    return {
        "trace_id": trace_id,
        "mode": mode,
        "total_token_consumption": total,
        "per_tool_token_totals": tuple(
            {
                "tool_name": item["tool_name"],
                "tool_associated_token_total": item["tool_associated_token_total"],
            }
            for item in statistics
        ),
        "diagnostics": {
            "shared_token_step_count": tool_trace["diagnostics"]["shared_token_step_count"]
        },
        "normalization_error": None,
    }


def _empty(
    mode: Literal["chat", "research"], trace_id: str | None, error: str
) -> NormalizedToolTokenShareTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "total_token_consumption": None,
        "per_tool_token_totals": (),
        "diagnostics": {"shared_token_step_count": 0},
        "normalization_error": error,
    }
