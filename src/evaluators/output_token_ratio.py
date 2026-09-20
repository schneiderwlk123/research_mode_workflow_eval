"""计算 Trace 级模型输出 Token 占总 Token 的比例。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE = frozenset(
    {
        "no_observable_token_usage",
        "incomplete_token_coverage",
        "zero_total_token_consumption",
    }
)
_ERRORS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_token_count",
        "conflicting_token_snapshot",
        "inconsistent_token_components",
    }
)


class OutputTokenRatioEvaluator(BaseEvaluator):
    """仅在模型步骤 Token 覆盖完整且分母非零时计算输出占比。"""

    @property
    def metric_name(self) -> str:
        return "output_token_ratio"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, output, total, diagnostics, error = parsed
        metadata: dict[str, object] = {"trace_id": trace_id, "mode": mode, **diagnostics}
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error in _NOT_COMPUTABLE
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)
        if output is None or total is None or total <= 0 or output > total:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        metadata["output_token_consumption"] = output
        metadata["total_token_consumption"] = total
        return self._result(output / total, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse(
    trace: Mapping[str, object],
) -> tuple[str | None, str, int | None, int | None, dict[str, int], str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    raw_output = trace.get("output_token_consumption")
    raw_total = trace.get("total_token_consumption")
    output = None if raw_output is None else _count(raw_output)
    total = None if raw_total is None else _count(raw_total)
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or (raw_output is not None and output is None)
        or (raw_total is not None and total is None)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None
    diagnostics: dict[str, int] = {}
    for name in ("valid_step_count", "token_missing_count", "duplicate_token_snapshot_count"):
        value = _count(raw_diagnostics.get(name))
        if value is None:
            return None
        diagnostics[name] = value
    if error is None and (output is None or total is None):
        return None
    if error == "no_observable_token_usage" and (output is not None or total is not None):
        return None
    if error == "incomplete_token_coverage" and (
        output is None or total is None or diagnostics["token_missing_count"] == 0
    ):
        return None
    if error == "zero_total_token_consumption" and (output != 0 or total != 0):
        return None
    if error in _ERRORS and (output is not None or total is not None):
        return None
    if error is not None and error not in _NOT_COMPUTABLE | _ERRORS:
        return None
    return _text(trace_id_value), mode, output, total, diagnostics, error


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
