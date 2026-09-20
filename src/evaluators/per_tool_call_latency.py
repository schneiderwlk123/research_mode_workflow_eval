"""计算单条 Trace 内逐工具平均执行时延。"""

import json
from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_LOCAL_ERROR_REASONS = frozenset(
    {"invalid_duration", "invalid_event_order", "conflicting_duration_observation"}
)
_TRACE_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    }
)


class _ParsedToolLatency(TypedDict):
    tool_name: str
    call_count: int
    latency_observed_count: int
    latency_missing_count: int
    latency_error_count: int
    tool_latency_total_ms: int
    duration_error_reasons: tuple[str, ...]


class PerToolCallLatencyEvaluator(BaseEvaluator):
    """输出逐工具时延；局部异常不阻止其他工具结果。"""

    @property
    def metric_name(self) -> str:
        return "per_tool_call_latency_ms"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, statistics, diagnostics, error = parsed
        metadata: dict[str, object] = {"trace_id": trace_id, "mode": mode, **diagnostics}
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error == "no_tool_calls"
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)

        output: list[dict[str, object]] = []
        partial_issues: list[dict[str, str]] = []
        for item in statistics:
            if item["duration_error_reasons"]:
                reason = item["duration_error_reasons"][0]
                output.append(
                    {
                        "tool_name": item["tool_name"],
                        "per_tool_call_latency_ms": None,
                    }
                )
                partial_issues.append({"status": EvaluationStatus.ERROR.value, "reason": reason})
            elif item["latency_observed_count"] == 0:
                reason = "no_observable_tool_duration"
                output.append(
                    {
                        "tool_name": item["tool_name"],
                        "per_tool_call_latency_ms": None,
                    }
                )
                partial_issues.append(
                    {"status": EvaluationStatus.NOT_COMPUTABLE.value, "reason": reason}
                )
            else:
                output.append(
                    {
                        "tool_name": item["tool_name"],
                        "per_tool_call_latency_ms": item["tool_latency_total_ms"]
                        / item["latency_observed_count"],
                    }
                )

        metadata["per_tool_statistics"] = statistics
        metadata["partial_issues"] = tuple(partial_issues)
        value = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
        return self._result(value, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: str | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, tuple[_ParsedToolLatency, ...], dict[str, int], str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    raw_statistics = trace.get("per_tool_statistics")
    raw_diagnostics = trace.get("diagnostics")
    raw_error = trace.get("normalization_error")
    error = _text(raw_error) if raw_error is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_sequence(raw_statistics)
        or not isinstance(raw_diagnostics, Mapping)
        or (raw_error is not None and error is None)
    ):
        return None

    statistics: list[_ParsedToolLatency] = []
    names: set[str] = set()
    for raw in raw_statistics:
        if not isinstance(raw, Mapping):
            return None
        tool_name = _text(raw.get("tool_name"))
        call_count = _non_negative_int(raw.get("call_count"))
        observed = _non_negative_int(raw.get("latency_observed_count"))
        missing = _non_negative_int(raw.get("latency_missing_count"))
        error_count = _non_negative_int(raw.get("latency_error_count"))
        total = _non_negative_int(raw.get("tool_latency_total_ms"))
        raw_reasons = raw.get("duration_error_reasons")
        if (
            tool_name is None
            or tool_name in names
            or call_count is None
            or observed is None
            or missing is None
            or error_count is None
            or total is None
            or not _is_sequence(raw_reasons)
        ):
            return None
        reasons = tuple(_text(reason) for reason in raw_reasons)
        if (
            any(reason is None or reason not in _LOCAL_ERROR_REASONS for reason in reasons)
            or len(reasons) != len(set(reasons))
            or call_count == 0
            or observed + missing + error_count != call_count
            or (observed == 0 and total != 0)
            or (error_count == 0) != (not reasons)
        ):
            return None
        names.add(tool_name)
        statistics.append(
            {
                "tool_name": tool_name,
                "call_count": call_count,
                "latency_observed_count": observed,
                "latency_missing_count": missing,
                "latency_error_count": error_count,
                "tool_latency_total_ms": total,
                "duration_error_reasons": tuple(reason for reason in reasons if reason is not None),
            }
        )

    diagnostics: dict[str, int] = {}
    for name in (
        "duration_missing_count",
        "duplicate_observation_count",
        "orphan_result_count",
        "malformed_event_count",
    ):
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    allowed_errors = _TRACE_ERROR_REASONS | {"no_tool_calls"}
    if error is None and not statistics:
        return None
    if error is not None and error not in allowed_errors:
        return None
    return _text(trace_id_value), mode, tuple(statistics), diagnostics, error


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
