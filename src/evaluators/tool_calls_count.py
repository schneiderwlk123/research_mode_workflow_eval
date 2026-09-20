"""计算单条 Trace 的唯一业务工具调用总次数。"""

from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    }
)


class _ParsedToolStatistic(TypedDict):
    tool_name: str
    call_count: int
    repeat_call_count: int


class ToolCallsCountEvaluator(BaseEvaluator):
    """汇总规范化后的逐工具调用计数，空调用集合按零计算。"""

    @property
    def metric_name(self) -> str:
        return "tool_calls_count"

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
        total_call_count = sum(item["call_count"] for item in statistics)
        total_repeat_count = sum(item["repeat_call_count"] for item in statistics)
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "per_tool_statistics": statistics,
            "unique_tool_name_count": len(statistics),
            "total_repeat_call_count": total_repeat_count,
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.ERROR, metadata)
        return self._result(total_call_count, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: int | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        tuple[_ParsedToolStatistic, ...],
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    raw_statistics = trace.get("per_tool_statistics")
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_sequence(raw_statistics)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None

    statistics: list[_ParsedToolStatistic] = []
    observed_names: set[str] = set()
    for raw_item in raw_statistics:
        if not isinstance(raw_item, Mapping):
            return None
        tool_name = _text(raw_item.get("tool_name"))
        call_count = _non_negative_int(raw_item.get("call_count"))
        repeat_count = _non_negative_int(raw_item.get("repeat_call_count"))
        if (
            tool_name is None
            or tool_name in observed_names
            or call_count is None
            or call_count == 0
            or repeat_count is None
            or repeat_count != max(call_count - 1, 0)
        ):
            return None
        observed_names.add(tool_name)
        statistics.append(
            {
                "tool_name": tool_name,
                "call_count": call_count,
                "repeat_call_count": repeat_count,
            }
        )

    diagnostics: dict[str, int] = {}
    for name in (
        "duplicate_call_observation_count",
        "excluded_agent_delegation_count",
        "excluded_agent_return_count",
        "malformed_event_count",
    ):
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    if error is not None and (statistics or error not in _ERROR_REASONS):
        return None
    return _text(trace_id_value), mode, tuple(statistics), diagnostics, error


def _non_negative_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
