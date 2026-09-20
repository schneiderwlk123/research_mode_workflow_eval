"""计算累计执行耗时最高的业务工具占全部工具耗时的比例。"""

import json
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset(
    {"no_tool_calls", "incomplete_tool_duration_coverage", "zero_tool_duration"}
)
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
        "invalid_duration",
        "invalid_event_order",
        "conflicting_duration_observation",
    }
)


class MaxToolLatencyShareEvaluator(BaseEvaluator):
    """严格要求全部业务工具耗时可用，并保留全部并列最大工具。"""

    @property
    def metric_name(self) -> str:
        return "max_tool_latency_share"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, total, tools, diagnostics, error = parsed
        metadata: dict[str, object] = {"trace_id": trace_id, "mode": mode, **diagnostics}
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error in _NOT_COMPUTABLE_REASONS
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)
        if total is None or total <= 0 or not tools:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        maximum = max(value for _, value in tools)
        if maximum > total:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        max_tool_names = tuple(sorted(name for name, value in tools if value == maximum))
        share = maximum / total
        metadata["max_latency_tool_names"] = max_tool_names
        metadata["max_tool_latency_total_ms"] = maximum
        metadata["total_tool_latency_ms"] = total
        value = json.dumps(
            [
                {"tool_name": tool_name, "max_tool_latency_share": share}
                for tool_name in max_tool_names
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
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
) -> (
    tuple[str | None, str, int | None, tuple[tuple[str, int], ...], dict[str, int], str | None]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    total_value = trace.get("total_tool_latency_ms")
    total = None if total_value is None else _non_negative_int(total_value)
    raw_tools = trace.get("per_tool_latency_totals")
    raw_diagnostics = trace.get("diagnostics")
    raw_error = trace.get("normalization_error")
    error = _text(raw_error) if raw_error is not None else None
    if (
        (total_value is not None and total is None)
        or (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_sequence(raw_tools)
        or not isinstance(raw_diagnostics, Mapping)
        or (raw_error is not None and error is None)
    ):
        return None

    tools: list[tuple[str, int]] = []
    names: set[str] = set()
    for raw in raw_tools:
        if not isinstance(raw, Mapping):
            return None
        name = _text(raw.get("tool_name"))
        value = _non_negative_int(raw.get("tool_latency_total_ms"))
        if name is None or name in names or value is None:
            return None
        names.add(name)
        tools.append((name, value))

    diagnostics: dict[str, int] = {}
    for name in (
        "duration_missing_count",
        "duplicate_observation_count",
        "orphan_result_count",
        "malformed_event_count",
        "excluded_agent_return_count",
    ):
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    if error is None and (total is None or not tools):
        return None
    if error is not None and (
        total is not None or tools or error not in _NOT_COMPUTABLE_REASONS | _ERROR_REASONS
    ):
        return None
    if total is not None and total != sum(value for _, value in tools):
        return None
    return _text(trace_id_value), mode, total, tuple(tools), diagnostics, error


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
