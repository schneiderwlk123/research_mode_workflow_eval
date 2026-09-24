"""计算单 Trace 续跑重启次数与闭环标记(稳定性维度 7.3)。

稳定指标名沿用权威口径 7.3 的 `continuation_restart_count`。宽表单元格为
单元素 JSON 数组(2026-09-21 需求方确认统一 [] 包围),同时承载重启计数与
7.1 闭环判定两因素:

    [{"continuation_restart_count":2,"turn_completed":0}]

研究模式两因素恒同时输出(零重启是合法值,不是不可计算);聊天模式无续跑
机制,返回 NOT_COMPUTABLE / no_continuation_mechanism,宽表单元格留空。
报告级 restart_turn_ratio / restart_recovery_rate 由聚合层基于单元格两因素
直接派生,评测器不做跨 Trace 聚合。
"""

import json
from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_continuation_mechanism", "missing_turn_boundary"})
_ERROR_REASONS = frozenset(
    {
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
    }
)
_DIAGNOSTIC_COUNT_KEYS = (
    "turn_start_count",
    "duplicate_turn_start_count",
    "invalid_ts_turn_start_count",
)
_SEMANTICS_BY_MODE = {
    "chat": "no_continuation_mechanism",
    "research": "turn_start_event",
}


class ContinuationRestartCountEvaluator(BaseEvaluator):
    """校验规范化续跑证据并输出双因素紧凑 JSON;缺边界不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "continuation_restart_count"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, restart_count, turn_completed, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # _parse_trace 已保证 mode ∈ _SEMANTICS_BY_MODE。
            "restart_semantics": _SEMANTICS_BY_MODE[mode],
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
        # error 为 None 时 _parse_trace 已保证两因素均为合法整数且 mode 为 research。
        assert restart_count is not None and turn_completed is not None  # noqa: S101
        # 闭环因素复用 7.1 研究口径(turn.end 事件),语义随元数据声明。
        metadata["completion_semantics"] = "turn_end_event"
        payload = {
            "continuation_restart_count": restart_count,
            "turn_completed": turn_completed,
        }
        # 单元格统一 [] 包围:单元素数组承载双因素对象(2026-09-21 需求方确认)。
        value = json.dumps([payload], ensure_ascii=False, separators=(",", ":"))
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
) -> tuple[str | None, str, int | None, int | None, Mapping[str, object], str | None] | None:
    if not isinstance(trace, Mapping):
        return None
    mode = _text(trace.get("mode"))
    if mode not in _SEMANTICS_BY_MODE:
        return None
    trace_id_value = trace.get("trace_id")
    trace_id = _text(trace_id_value)
    if trace_id_value is not None and trace_id is None:
        return None

    restart_count = trace.get("restart_count")
    if restart_count is not None and (
        isinstance(restart_count, bool) or not isinstance(restart_count, int) or restart_count < 0
    ):
        return None

    turn_completed = trace.get("turn_completed")
    if turn_completed is not None and (
        isinstance(turn_completed, bool)
        or not isinstance(turn_completed, int)
        or turn_completed not in (0, 1)
    ):
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
    if (error is None) != (restart_count is not None and turn_completed is not None):
        # error 与两因素互斥: 有 error 不得携带计数,无 error 必须同时携带两者。
        return None
    if mode == "chat" and error is None:
        # 聊天模式无续跑机制,契约上恒携带 no_continuation_mechanism;
        # 无 error 的 chat 规范化结果属契约违约。
        return None
    if error is None:
        # 主值必须与去重后 turn.start 计数自洽: restart = turn_start − 1。
        turn_start_count = diagnostics["turn_start_count"]
        assert isinstance(turn_start_count, int) and isinstance(restart_count, int)
        if restart_count != turn_start_count - 1:
            return None
    return trace_id, mode, restart_count, turn_completed, diagnostics, error


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
