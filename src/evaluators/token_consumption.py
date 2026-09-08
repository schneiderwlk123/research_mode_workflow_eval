"""计算单条 Trace 的完整 Token 消耗。"""

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_observable_token_usage", "incomplete_token_coverage"})
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_token_count",
        "conflicting_token_snapshot",
    }
)


class TotalTokenConsumptionEvaluator(BaseEvaluator):
    """仅在模型步骤 Token 覆盖完整时汇总单条 Trace 的 Token 消耗。"""

    @property
    def metric_name(self) -> str:
        return "total_token_consumption"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, totals, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            **diagnostics,
        }
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
        if not totals:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        return self._result(sum(totals), EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: int | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, tuple[int, ...], dict[str, int], str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    raw_totals = trace.get("step_token_totals")
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or not _is_sequence(raw_totals)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None

    totals: list[int] = []
    for value in raw_totals:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        totals.append(value)

    diagnostics: dict[str, int] = {}
    for name in (
        "valid_step_count",
        "token_missing_count",
        "duplicate_token_snapshot_count",
    ):
        value = raw_diagnostics.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[name] = value
    if diagnostics["valid_step_count"] != len(totals):
        return None
    if error is None and (not totals or diagnostics["token_missing_count"] != 0):
        return None
    if error == "no_observable_token_usage" and totals:
        return None
    if error == "incomplete_token_coverage" and (
        not totals or diagnostics["token_missing_count"] == 0
    ):
        return None
    if error is not None and error not in _NOT_COMPUTABLE_REASONS | _ERROR_REASONS:
        return None
    if error in _ERROR_REASONS and totals:
        return None
    return _text(trace_id_value), mode, tuple(totals), diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
