"""评估单次工具调用的耗时与 Token 可计算状态。"""

from collections.abc import Mapping
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_DURATION_NOT_COMPUTABLE = frozenset({"missing_tool_duration"})
_DURATION_ERRORS = frozenset(
    {
        "invalid_duration",
        "invalid_event_order",
        "conflicting_duration_observation",
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
    }
)
_TOOL_TOKEN_ERROR = "no_observable_tool_token_usage"


class ToolCallDurationEvaluator(BaseEvaluator):
    """返回单次工具调用耗时，缺失观测时不补零。"""

    @property
    def metric_name(self) -> str:
        return "tool_call_duration_ms"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_call(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        metadata = _metadata(parsed)
        duration = parsed[4]
        error = parsed[5]
        if parsed[1] not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error in _DURATION_NOT_COMPUTABLE
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)
        if duration is None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        return self._result(duration, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: int | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


class ToolTokenConsumptionEvaluator(BaseEvaluator):
    """明确报告当前日志无法观测工具执行自身 Token，禁止模型步骤分摊。"""

    @property
    def metric_name(self) -> str:
        return "tool_token_consumption"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_call(trace)
        if parsed is None:
            return self._result(EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"})
        metadata = _metadata(parsed)
        if parsed[1] not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(EvaluationStatus.NOT_COMPUTABLE, metadata)
        metadata["reason"] = _TOOL_TOKEN_ERROR
        return self._result(EvaluationStatus.NOT_COMPUTABLE, metadata)

    def _result(self, status: EvaluationStatus, metadata: Mapping[str, object]) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, None, status, metadata)


def _parse_call(
    trace: Mapping[str, object],
) -> tuple[str | None, str, str, str, int | None, str | None] | None:
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    call_id = _text(trace.get("call_id"))
    tool_name = _text(trace.get("tool_name"))
    duration_value = trace.get("duration_ms")
    duration_error_value = trace.get("duration_error")
    duration_error = _text(duration_error_value) if duration_error_value is not None else None
    token_error = _text(trace.get("token_error"))
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or call_id is None
        or tool_name is None
        or not _is_optional_non_negative_int(duration_value)
        or (duration_error_value is not None and duration_error is None)
        or trace.get("token_total") is not None
        or token_error != _TOOL_TOKEN_ERROR
    ):
        return None
    if (duration_value is None) == (duration_error is None):
        return None
    if (
        duration_error is not None
        and duration_error not in _DURATION_NOT_COMPUTABLE | _DURATION_ERRORS
    ):
        return None
    return _text(trace_id_value), mode, call_id, tool_name, duration_value, duration_error


def _metadata(
    parsed: tuple[str | None, str, str, str, int | None, str | None],
) -> dict[str, object]:
    return {
        "trace_id": parsed[0],
        "mode": parsed[1],
        "call_id": parsed[2],
        "tool_name": parsed[3],
    }


def _is_optional_non_negative_int(value: object) -> TypeGuard[int | None]:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
