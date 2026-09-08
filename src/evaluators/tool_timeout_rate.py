"""计算版本化工具 SLA 下的会话级超时率。"""

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_TIMEOUT_REASONS = frozenset({"explicit_timeout", "sla_exceeded", "missing_timestamps"})
_INPUT_ERROR_REASONS = frozenset(
    {
        "invalid_timestamp",
        "invalid_event_order",
        "conflicting_call_definition",
        "invalid_chat_trace_structure",
    }
)


class ToolTimeoutRateEvaluator(BaseEvaluator):
    """计算超时唯一调用数占全部唯一调用数的比例。"""

    @property
    def metric_name(self) -> str:
        return "tool_timeout_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, policy_version, calls, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "policy_version": policy_version,
            "timeout_call_count": sum(reason is not None for reason in calls),
            "total_call_count": len(calls),
            **diagnostics,
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.ERROR
                if error in _INPUT_ERROR_REASONS
                else EvaluationStatus.NOT_COMPUTABLE
            )
            return self._result(None, status, metadata)
        if not calls:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        expected = {
            "explicit_timeout_count": calls.count("explicit_timeout"),
            "sla_exceeded_count": calls.count("sla_exceeded"),
            "missing_time_timeout_count": calls.count("missing_timestamps"),
        }
        if any(diagnostics[name] != value for name, value in expected.items()):
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        timeout_count = sum(reason is not None for reason in calls)
        return self._result(timeout_count / len(calls), EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, str, list[str | None], dict[str, int], str | None] | None:
    trace_id = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    policy_version = _text(trace.get("policy_version"))
    raw_calls = trace.get("tool_calls")
    raw_diagnostics = trace.get("diagnostics")
    error_value = trace.get("normalization_error")
    error = _text(error_value) if error_value is not None else None
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or mode is None
        or policy_version is None
        or not _is_sequence(raw_calls)
        or not isinstance(raw_diagnostics, Mapping)
        or (error_value is not None and error is None)
    ):
        return None

    calls: list[str | None] = []
    call_ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            return None
        call_id = _text(raw_call.get("call_id"))
        reason_value = raw_call.get("timeout_reason")
        reason = _text(reason_value) if reason_value is not None else None
        if call_id is None or call_id in call_ids or reason not in _TIMEOUT_REASONS | {None}:
            return None
        call_ids.add(call_id)
        calls.append(reason)

    diagnostics: dict[str, int] = {}
    for name in (
        "explicit_timeout_count",
        "sla_exceeded_count",
        "missing_time_timeout_count",
        "duplicate_state_event_count",
        "malformed_event_count",
    ):
        value = raw_diagnostics.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[name] = value
    return trace_id, mode, policy_version, calls, diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
