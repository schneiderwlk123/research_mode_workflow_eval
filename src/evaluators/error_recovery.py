"""计算单条日志的错误自恢复标记并输出紧凑 JSON 值(稳定性维度 7.5)。

口径(《初步入库评测指标.md》7.5):
- 主值 `error_recovered ∈ {0,1}` = 闭环(turn_completed=1) ∧ 非空最终正文
  (body_present=1);恢复仅指技术完成,不判正文质量。
- 仅对 `had_error = 1` 的回合计算;had_error = 0 → NOT_COMPUTABLE /
  no_error_events(不进报告级分母,不得记 1)。聊天模式恒 NOT_COMPUTABLE /
  no_error_events_observable(无错误事件通道,禁止载荷代理)。
- 值列为单元素 JSON 数组(2026-09-21 需求方确认统一 [] 包围,键序固定):
  主值 + had_error +
  两层错误计数 + loop 级重试配对计数;报告级 error_recovery_rate =
  Σ error_recovered ÷ Σ had_error,由聚合层解析 JSON 单元格派生。
- 研究侧为 5.1 的降级实现,`recovery_semantics = degraded_v1` 随元数据声明,
  埋点 C/D 落地后对齐升版。
"""

import json
from collections.abc import Mapping

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset(
    {"no_error_events", "no_error_events_observable", "missing_turn_boundary"}
)
_INPUT_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    }
)
_EXPECTED_SEMANTICS = "degraded_v1"
_DIAGNOSTIC_KEYS = (
    "llm_error_event_count",
    "tool_error_call_count",
    "retry_recovered_error_count",
    "retry_unpaired_error_count",
    "llm_loop_missing_error_count",
)


class ErrorRecoveryRateEvaluator(BaseEvaluator):
    """输出 error_recovered 0/1 主值与伴随计数的紧凑 JSON。"""

    @property
    def metric_name(self) -> str:
        return "error_recovery_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        (
            trace_id,
            mode,
            semantics,
            had_error,
            turn_completed,
            body_present,
            body_channel,
            diagnostics,
            error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "recovery_semantics": semantics,
            "had_error": had_error,
            "turn_completed": turn_completed,
            "body_present": body_present,
            "body_channel": body_channel,
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

        # 可计算路径只存在于研究降级口径:语义标签、标志与守恒关系逐项校验。
        if mode != "research" or semantics != _EXPECTED_SEMANTICS:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        if had_error != 1 or turn_completed is None or body_present is None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        llm_count = diagnostics["llm_error_event_count"]
        tool_count = diagnostics["tool_error_call_count"]
        if (llm_count > 0 or tool_count > 0) != (had_error == 1):
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        error_recovered = 1 if (turn_completed == 1 and body_present == 1) else 0
        payload = {
            "error_recovered": error_recovered,
            "had_error": had_error,
            "llm_error_count": llm_count,
            "tool_error_call_count": tool_count,
            "retry_recovered_error_count": diagnostics["retry_recovered_error_count"],
        }
        # 单元格统一 [] 包围:单元素数组承载固定键序对象(2026-09-21 需求方确认)。
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
) -> (
    tuple[
        str | None,
        str,
        str | None,
        int | None,
        int | None,
        int | None,
        str | None,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    semantics_value = trace.get("recovery_semantics")
    semantics = _text(semantics_value) if semantics_value is not None else None
    had_error = _flag(trace.get("had_error"))
    turn_completed = _flag(trace.get("turn_completed"))
    body_present = _flag(trace.get("body_present"))
    body_channel_value = trace.get("body_channel")
    body_channel = _text(body_channel_value) if body_channel_value is not None else None
    raw_diagnostics = trace.get("diagnostics")
    raw_error = trace.get("normalization_error")
    error = _text(raw_error) if raw_error is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or (semantics_value is not None and semantics is None)
        or _is_invalid_flag(trace.get("had_error"), had_error)
        or _is_invalid_flag(trace.get("turn_completed"), turn_completed)
        or _is_invalid_flag(trace.get("body_present"), body_present)
        or (body_channel_value is not None and body_channel is None)
        or not isinstance(raw_diagnostics, Mapping)
        or (raw_error is not None and error is None)
    ):
        return None

    diagnostics: dict[str, int] = {}
    for key in _DIAGNOSTIC_KEYS:
        value = raw_diagnostics.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[key] = value
    # 未知原因码属规范化契约违约,在解析层拒绝为 invalid_normalized_trace。
    allowed_errors = _NOT_COMPUTABLE_REASONS | _INPUT_ERROR_REASONS
    if error is not None and error not in allowed_errors:
        return None
    return (
        _text(trace_id_value),
        mode,
        semantics,
        had_error,
        turn_completed,
        body_present,
        body_channel,
        diagnostics,
        error,
    )


def _flag(value: object) -> int | None:
    """把规范化 0/1 标志收窄为 int;None 保持 None,其余交给合法性检查。"""

    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return value
    return None


def _is_invalid_flag(raw: object, parsed: int | None) -> bool:
    return raw is not None and parsed is None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
