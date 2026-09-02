"""调用成功率的严格确定性评测器。"""

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class ToolCallSuccessEvaluator(BaseEvaluator):
    """仅在所有唯一调用均有确定结果时计算调用成功率。"""

    @property
    def metric_name(self) -> str:
        return "tool_call_success_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        """计算会话级成功率，未知业务结果不会被猜测为成功或失败。"""

        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                value=None,
                status=EvaluationStatus.ERROR,
                metadata={"reason": "invalid_normalized_trace"},
            )

        (
            trace_id,
            mode,
            calls,
            diagnostics,
            normalization_error,
            success_normalization_error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "duplicate_state_event_count": diagnostics["duplicate_state_event_count"],
            "malformed_event_count": diagnostics["malformed_event_count"],
        }
        if mode not in {"chat", "research"}:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if normalization_error is not None:
            metadata["reason"] = normalization_error
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if success_normalization_error is not None:
            metadata["reason"] = success_normalization_error
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )

        unique_call_count = len(calls)
        successful_call_count = sum(call["success_observed"] is True for call in calls)
        unsuccessful_call_count = sum(call["success_observed"] is False for call in calls)
        undetermined_call_count = sum(call["success_observed"] is None for call in calls)
        success_rule_ids = tuple(
            sorted(
                {rule_id for call in calls if isinstance((rule_id := call["success_rule_id"]), str)}
            )
        )
        metadata.update(
            {
                "successful_call_count": successful_call_count,
                "unsuccessful_call_count": unsuccessful_call_count,
                "undetermined_call_count": undetermined_call_count,
                "unique_call_count": unique_call_count,
                "success_rule_ids": success_rule_ids,
            }
        )
        if unique_call_count == 0:
            metadata["reason"] = "no_tool_calls"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )
        if undetermined_call_count > 0:
            metadata["reason"] = "undetermined_tool_outcome"
            return self._result(
                value=None,
                status=EvaluationStatus.NOT_COMPUTABLE,
                metadata=metadata,
            )

        return self._result(
            value=successful_call_count / unique_call_count,
            status=EvaluationStatus.COMPUTED,
            metadata=metadata,
        )

    def _result(
        self,
        *,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(
            metric_name=self.metric_name,
            evaluator_version=self.evaluator_version,
            value=value,
            status=status,
            metadata=metadata,
        )


def _parse_trace(
    trace: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        tuple[dict[str, object], ...],
        dict[str, int],
        str | None,
        str | None,
    ]
    | None
):
    mode = trace.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        return None

    trace_id_value = trace.get("trace_id")
    if trace_id_value is not None and not isinstance(trace_id_value, str):
        return None
    trace_id = trace_id_value.strip() if isinstance(trace_id_value, str) else None

    normalization_error = trace.get("normalization_error")
    if normalization_error is not None and (
        not isinstance(normalization_error, str) or not normalization_error.strip()
    ):
        return None

    success_normalization_error = trace.get("success_normalization_error")
    if success_normalization_error is not None and (
        not isinstance(success_normalization_error, str) or not success_normalization_error.strip()
    ):
        return None

    diagnostics_value = trace.get("diagnostics")
    if not isinstance(diagnostics_value, Mapping):
        return None
    duplicate_count = _non_negative_int(diagnostics_value.get("duplicate_state_event_count"))
    malformed_count = _non_negative_int(diagnostics_value.get("malformed_event_count"))
    if duplicate_count is None or malformed_count is None:
        return None

    calls_value = trace.get("tool_calls")
    if not _is_sequence(calls_value):
        return None

    calls: list[dict[str, object]] = []
    call_ids: set[str] = set()
    for value in calls_value:
        if not isinstance(value, Mapping):
            return None
        call_id = value.get("call_id")
        tool_name = value.get("tool_name")
        request_observed = value.get("request_observed")
        terminal_observed = value.get("terminal_observed")
        success_observed = value.get("success_observed")
        success_rule_id = value.get("success_rule_id")
        source_event_count = _positive_int(value.get("source_event_count"))
        if (
            not isinstance(call_id, str)
            or not call_id.strip()
            or call_id in call_ids
            or (tool_name is not None and (not isinstance(tool_name, str) or not tool_name.strip()))
            or not isinstance(request_observed, bool)
            or not isinstance(terminal_observed, bool)
            or (success_observed is not None and not isinstance(success_observed, bool))
            or (
                success_rule_id is not None
                and (not isinstance(success_rule_id, str) or not success_rule_id.strip())
            )
            or (terminal_observed is False and success_observed is not False)
            or success_observed is True
            and (not isinstance(success_rule_id, str) or not success_rule_id.strip())
            or source_event_count is None
        ):
            return None
        call_ids.add(call_id)
        calls.append(
            {
                "success_observed": success_observed,
                "success_rule_id": (
                    success_rule_id.strip() if isinstance(success_rule_id, str) else None
                ),
            }
        )

    return (
        trace_id,
        mode.strip().lower(),
        tuple(calls),
        {
            "duplicate_state_event_count": duplicate_count,
            "malformed_event_count": malformed_count,
        },
        normalization_error.strip() if isinstance(normalization_error, str) else None,
        (
            success_normalization_error.strip()
            if isinstance(success_normalization_error, str)
            else None
        ),
    )


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_int(value: object) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
