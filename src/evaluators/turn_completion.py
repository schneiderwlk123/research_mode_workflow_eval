"""计算聊天与研究模式统一的回合完成率(单 Trace 输出 0/1 二值标记)。

稳定指标名沿用权威口径 7.1 的 `turn_completion_rate`: 单 Trace 主值为
`turn_completed ∈ {0, 1}`,报告级完成率由聚合层对单 Trace 值求微平均,
评测器自身不做跨 Trace 聚合。聊天模式为显式声明的代理口径
(`answer_presence_proxy`),研究模式为 turn.end 事件口径(`turn_end_event`),
语义标签随结果输出,禁止混比。
"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"missing_turn_boundary"})
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
    }
)
_DURATION_DIAGNOSTICS = frozenset({"missing_duration_ms", "invalid_duration_ms"})
_DIAGNOSTIC_COUNT_KEYS = (
    "failure_template_hit_count",
    "missing_or_empty_answer_count",
    "duplicate_turn_end_count",
    "mismatched_turn_end_count",
)
_SEMANTICS_BY_MODE = {
    "chat": "answer_presence_proxy",
    "research": "turn_end_event",
}


class TurnCompletionRateEvaluator(BaseEvaluator):
    """校验规范化闭环证据并输出单 Trace 0/1 标记;缺边界不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "turn_completion_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, turn_completed, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # _parse_trace 已保证 mode ∈ _SEMANTICS_BY_MODE。
            "completion_semantics": _SEMANTICS_BY_MODE[mode],
            **{key: diagnostics[key] for key in _DIAGNOSTIC_COUNT_KEYS},
            "duration_diagnostic": diagnostics["duration_diagnostic"],
        }
        if error is not None:
            metadata["reason"] = error
            if error in _NOT_COMPUTABLE_REASONS:
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            if error in _ERROR_REASONS:
                return self._result(None, EvaluationStatus.ERROR, metadata)
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        # error 为 None 时 collector 保证 turn_completed ∈ {0, 1}。
        assert turn_completed is not None  # noqa: S101 - 规范化契约保证
        return self._result(turn_completed, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: int | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, int | None, Mapping[str, object], str | None] | None:
    if not isinstance(trace, Mapping):
        return None
    mode = _text(trace.get("mode"))
    if mode not in _SEMANTICS_BY_MODE:
        return None
    trace_id_value = trace.get("trace_id")
    trace_id = _text(trace_id_value)
    if trace_id_value is not None and trace_id is None:
        return None

    completed = trace.get("turn_completed")
    if completed is not None and (
        isinstance(completed, bool) or not isinstance(completed, int) or completed not in (0, 1)
    ):
        return None

    diagnostics = trace.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in _DIAGNOSTIC_COUNT_KEYS:
        value = diagnostics.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
    duration_diagnostic = diagnostics.get("duration_diagnostic")
    if duration_diagnostic is not None and (
        not isinstance(duration_diagnostic, str) or duration_diagnostic not in _DURATION_DIAGNOSTICS
    ):
        return None

    error_value = trace.get("normalization_error")
    error = _text(error_value)
    if error_value is not None and error is None:
        return None
    if (error is None) != (completed is not None):
        # error 与 0/1 标记互斥: 有 error 不得携带标记,无 error 必须携带标记。
        return None
    return trace_id, mode, completed, diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
