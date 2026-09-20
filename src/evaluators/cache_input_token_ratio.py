"""计算 Trace 级缓存 Token 占真实输入 Token 的比例。"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE = frozenset(
    {
        "no_observable_cache_usage",
        "incomplete_cache_token_coverage",
        "zero_effective_input_token_consumption",
    }
)
_ERRORS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_step_id",
        "invalid_token_count",
        "conflicting_token_snapshot",
        "inconsistent_cache_components",
        "inconsistent_token_breakdown",
    }
)


class CacheInputTokenRatioEvaluator(BaseEvaluator):
    """仅在全部模型步骤的缓存与真实输入 Token 完整时计算比例。"""

    @property
    def metric_name(self) -> str:
        return "cache_input_token_ratio"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, cache, effective_input, diagnostics, error = parsed
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
        if (
            cache is None
            or effective_input is None
            or effective_input <= 0
            or cache > effective_input
        ):
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        metadata["cache_token_consumption"] = cache
        metadata["effective_input_token_consumption"] = effective_input
        return self._result(cache / effective_input, EvaluationStatus.COMPUTED, metadata)

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
    raw_cache = trace.get("cache_token_consumption")
    raw_effective_input = trace.get("effective_input_token_consumption")
    cache = None if raw_cache is None else _count(raw_cache)
    effective_input = None if raw_effective_input is None else _count(raw_effective_input)
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or (raw_cache is not None and cache is None)
        or (raw_effective_input is not None and effective_input is None)
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
    if error is None and (cache is None or effective_input is None):
        return None
    if error == "no_observable_cache_usage" and (cache is not None or effective_input is not None):
        return None
    if error == "incomplete_cache_token_coverage" and (
        cache is None or effective_input is None or diagnostics["token_missing_count"] == 0
    ):
        return None
    if error == "zero_effective_input_token_consumption" and (cache != 0 or effective_input != 0):
        return None
    if error in _ERRORS and (cache is not None or effective_input is not None):
        return None
    if error is not None and error not in _NOT_COMPUTABLE | _ERRORS:
        return None
    return _text(trace_id_value), mode, cache, effective_input, diagnostics, error


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
