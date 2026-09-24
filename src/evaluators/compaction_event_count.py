"""计算单 Trace 上下文压缩事件数(稳定性维度 7.6)。

稳定指标名沿用权威口径 7.6 的 `compaction_event_count`: 研究模式主值为
非负整数(零压缩是合法值,不是不可计算);聊天模式无压缩事件通道,恒返回
固定不可计算原因。报告级 compaction_turn_ratio 与平均压缩次数由聚合层
基于单 Trace 值派生,评测器不做跨 Trace 聚合。
"""

from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_compaction_events"})
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_compaction_definition",
    }
)
_DIAGNOSTIC_COUNT_KEYS = (
    "duplicate_compaction_projection_count",
    "manual_compaction_count",
    "auto_flag_invalid_count",
    "missing_compaction_part_id_count",
    "message_compaction_projection_count",
)
_SEMANTICS_BY_MODE = {
    "chat": "no_compaction_channel",
    "research": "compaction_part_event",
}


class CompactionEventCountEvaluator(BaseEvaluator):
    """校验规范化压缩证据并输出单 Trace 非负整数;缺通道不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "compaction_event_count"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, count, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # _parse_trace 已保证 mode ∈ _SEMANTICS_BY_MODE。
            "compaction_semantics": _SEMANTICS_BY_MODE[mode],
            **{key: diagnostics[key] for key in _DIAGNOSTIC_COUNT_KEYS},
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
        # error 为 None 时 _parse_trace 已保证 count 为非负整数。
        assert count is not None  # noqa: S101 - 规范化契约保证
        return self._result(count, EvaluationStatus.COMPUTED, metadata)

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

    count = trace.get("compaction_event_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        return None

    diagnostics = trace.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in _DIAGNOSTIC_COUNT_KEYS:
        value = diagnostics.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None

    error_value = trace.get("normalization_error")
    error = _text(error_value)
    if error_value is not None and error is None:
        return None
    if (error is None) != (count is not None):
        # error 与主值互斥: 有 error 不得携带计数,无 error 必须携带计数。
        return None
    if mode == "chat" and error is None:
        # 聊天模式无压缩事件通道,契约上恒携带 no_compaction_events;
        # 无 error 的 chat 规范化结果属契约违约。
        return None
    return trace_id, mode, count, diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
