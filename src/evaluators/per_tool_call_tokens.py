"""计算单条 Trace 内逐工具关联模型步骤的平均 Token。"""

import json
from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_tool_calls"})
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "missing_step_id",
        "conflicting_call_definition",
        "conflicting_token_snapshot",
        "invalid_token_count",
    }
)


class _ParsedToolStatistic(TypedDict):
    tool_name: str
    call_count: int
    tool_step_count: int
    single_tool_step_count: int
    multi_tool_step_count: int
    token_observed_count: int
    token_missing_count: int
    tool_associated_token_total: int


class PerToolCallTokensEvaluator(BaseEvaluator):
    """输出允许逐工具局部不可计算的紧凑 JSON 数组。"""

    @property
    def metric_name(self) -> str:
        return "per_tool_call_tokens"

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
                if error in _NOT_COMPUTABLE_REASONS
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)

        output: list[dict[str, object]] = []
        partial_reasons: list[str] = []
        for item in statistics:
            if item["tool_step_count"] == 0:
                reason = "missing_tool_step_association"
                output.append(
                    {"tool_name": item["tool_name"], "per_tool_call_tokens": None, "reason": reason}
                )
                partial_reasons.append(reason)
            elif item["token_missing_count"]:
                reason = "incomplete_token_coverage"
                output.append(
                    {"tool_name": item["tool_name"], "per_tool_call_tokens": None, "reason": reason}
                )
                partial_reasons.append(reason)
            else:
                output.append(
                    {
                        "tool_name": item["tool_name"],
                        "per_tool_call_tokens": item["tool_associated_token_total"]
                        / item["tool_step_count"],
                    }
                )
        metadata["per_tool_statistics"] = statistics
        metadata["tool_token_attribution"] = "full_step_association"
        metadata["partial_issue_reasons"] = tuple(partial_reasons)
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
    names: set[str] = set()
    for raw in raw_statistics:
        if not isinstance(raw, Mapping):
            return None
        tool_name = _text(raw.get("tool_name"))
        call_count = _non_negative_int(raw.get("call_count"))
        steps = _non_negative_int(raw.get("tool_step_count"))
        single = _non_negative_int(raw.get("single_tool_step_count"))
        multi = _non_negative_int(raw.get("multi_tool_step_count"))
        observed = _non_negative_int(raw.get("token_observed_count"))
        missing = _non_negative_int(raw.get("token_missing_count"))
        total = _non_negative_int(raw.get("tool_associated_token_total"))
        if (
            tool_name is None
            or tool_name in names
            or call_count is None
            or steps is None
            or single is None
            or multi is None
            or observed is None
            or missing is None
            or total is None
        ):
            return None
        if (
            call_count == 0
            or call_count < steps
            or single + multi != steps
            or observed + missing != steps
            or (observed == 0 and total != 0)
        ):
            return None
        names.add(tool_name)
        statistics.append(
            {
                "tool_name": tool_name,
                "call_count": call_count,
                "tool_step_count": steps,
                "single_tool_step_count": single,
                "multi_tool_step_count": multi,
                "token_observed_count": observed,
                "token_missing_count": missing,
                "tool_associated_token_total": total,
            }
        )
    diagnostics: dict[str, int] = {}
    for name in (
        "shared_token_step_count",
        "duplicate_call_observation_count",
        "duplicate_token_snapshot_count",
        "excluded_agent_delegation_count",
        "excluded_agent_return_count",
        "malformed_event_count",
    ):
        value = _non_negative_int(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    if error is None and not statistics:
        return None
    if error is not None and (statistics or error not in _NOT_COMPUTABLE_REASONS | _ERROR_REASONS):
        return None
    return _text(trace_id_value), mode, tuple(statistics), diagnostics, error


def _non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
