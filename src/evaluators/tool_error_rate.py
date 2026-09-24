"""计算单条 Trace 的工具错误率并按归因目录 v1 输出紧凑 JSON 值。

口径(《初步入库评测指标.md》7.7):
- `tool_error_rate = error 终态唯一调用数 ÷ 有终态唯一调用数`;降级(degraded)
  调用进分母、不进硬失败分子;合法零值正常计算。
- 值列为单元素 JSON 数组(2026-09-21 需求方确认统一 [] 包围,键序固定):
  比率 + 分子分母 + 四类归因计数;逐项局部
  状态、诊断与 error 调用的工具级明细留在元数据,报告级"归因类 × 工具名"
  矩阵由 reporter 批次聚合。
- 聊天为载荷代理口径、研究为显式终态口径,`terminal_semantics` 随元数据声明,
  两种语义禁止混比;评测器对语义标签与版本声明做一致性校验。
- 无终态调用 → NOT_COMPUTABLE / no_terminal_tool_calls;无业务工具调用 →
  NOT_COMPUTABLE / no_tool_calls;结构问题 → ERROR 并携带稳定原因码。
"""

import json
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_tool_calls", "no_terminal_tool_calls"})
_INPUT_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    }
)
_TERMINAL_STATES = frozenset({"success", "error", "degraded"})
_ATTRIBUTIONS = frozenset(
    {"model_misuse", "infrastructure_failure", "data_availability", "unclassified"}
)
_EXPECTED_SEMANTICS = {"chat": "payload_proxy_v1", "research": "explicit_status_v1"}
_DIAGNOSTIC_KEYS = (
    "model_misuse_error_count",
    "infrastructure_error_count",
    "data_availability_error_count",
    "unclassified_error_count",
    "degraded_result_count",
    "terminal_missing_count",
    "undeterminable_payload_count",
    "duplicate_observation_count",
    "orphan_result_count",
    "conflicting_terminal_state_count",
    "excluded_agent_delegation_count",
    "excluded_agent_return_count",
    "malformed_event_count",
)
# 归因计数与逐项终态必须一致,防止规范化与聚合脱节。
_DERIVED_DIAGNOSTIC_KEYS = (
    "model_misuse_error_count",
    "infrastructure_error_count",
    "data_availability_error_count",
    "unclassified_error_count",
    "degraded_result_count",
)


class ToolErrorRateEvaluator(BaseEvaluator):
    """计算 error 终态唯一调用占有终态唯一调用的比例。"""

    @property
    def metric_name(self) -> str:
        return "tool_error_rate"

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
            terminal_semantics,
            catalog_version,
            failure_rules_version,
            calls,
            diagnostics,
            error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            "terminal_semantics": terminal_semantics,
            "attribution_catalog_version": catalog_version,
            "failure_signal_rules_version": failure_rules_version,
            **diagnostics,
        }
        if mode not in _EXPECTED_SEMANTICS:
            metadata["reason"] = "unsupported_trace_mode"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        # 语义标签与失败信号版本声明必须和模式一致,禁止跨口径混比。
        if terminal_semantics != _EXPECTED_SEMANTICS[mode]:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        if mode == "chat" and failure_rules_version is None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)
        if mode == "research" and failure_rules_version is not None:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        if error is not None:
            metadata["reason"] = error
            status = (
                EvaluationStatus.NOT_COMPUTABLE
                if error in _NOT_COMPUTABLE_REASONS
                else EvaluationStatus.ERROR
            )
            return self._result(None, status, metadata)
        if not calls:
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        derived = {key: 0 for key in _DERIVED_DIAGNOSTIC_KEYS}
        for call in calls:
            if call["terminal_state"] == "degraded":
                derived["degraded_result_count"] += 1
            elif call["terminal_state"] == "error":
                attribution = call["attribution"]
                if attribution == "model_misuse":
                    derived["model_misuse_error_count"] += 1
                elif attribution == "infrastructure_failure":
                    derived["infrastructure_error_count"] += 1
                elif attribution == "data_availability":
                    derived["data_availability_error_count"] += 1
                else:
                    derived["unclassified_error_count"] += 1
        if any(diagnostics[key] != value for key, value in derived.items()):
            metadata["reason"] = "invalid_normalized_trace"
            return self._result(None, EvaluationStatus.ERROR, metadata)

        error_calls = [call for call in calls if call["terminal_state"] == "error"]
        metadata["error_call_count"] = len(error_calls)
        metadata["terminal_call_count"] = len(calls)
        metadata["error_calls"] = tuple(
            {"tool_name": call["tool_name"], "attribution": call["attribution"]}
            for call in error_calls
        )
        payload = {
            "tool_error_rate": len(error_calls) / len(calls),
            "error_call_count": len(error_calls),
            "terminal_call_count": len(calls),
            "model_misuse_error_count": derived["model_misuse_error_count"],
            "infrastructure_error_count": derived["infrastructure_error_count"],
            "data_availability_error_count": derived["data_availability_error_count"],
            "degraded_result_count": derived["degraded_result_count"],
            "unclassified_error_count": derived["unclassified_error_count"],
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
        str,
        str,
        str | None,
        tuple[dict[str, object], ...],
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id_value = trace.get("trace_id")
    mode = _text(trace.get("mode"))
    terminal_semantics = _text(trace.get("terminal_semantics"))
    catalog_version = _text(trace.get("attribution_catalog_version"))
    failure_rules_value = trace.get("failure_signal_rules_version")
    failure_rules = _text(failure_rules_value) if failure_rules_value is not None else None
    raw_calls = trace.get("tool_calls")
    raw_diagnostics = trace.get("diagnostics")
    raw_error = trace.get("normalization_error")
    error = _text(raw_error) if raw_error is not None else None
    if (
        (trace_id_value is not None and _text(trace_id_value) is None)
        or mode is None
        or terminal_semantics is None
        or catalog_version is None
        or (failure_rules_value is not None and failure_rules is None)
        or not _is_sequence(raw_calls)
        or not isinstance(raw_diagnostics, Mapping)
        or (raw_error is not None and error is None)
    ):
        return None

    calls: list[dict[str, object]] = []
    call_ids: set[str] = set()
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            return None
        call_id = _text(raw_call.get("call_id"))
        tool_name = _text(raw_call.get("tool_name"))
        terminal_state = _text(raw_call.get("terminal_state"))
        attribution_value = raw_call.get("attribution")
        attribution = _text(attribution_value) if attribution_value is not None else None
        if (
            call_id is None
            or call_id in call_ids
            or tool_name is None
            or terminal_state not in _TERMINAL_STATES
            or (attribution_value is not None and attribution is None)
        ):
            return None
        # error 终态必须携带目录内归因;非 error 终态不得携带归因。
        if terminal_state == "error":
            if attribution not in _ATTRIBUTIONS:
                return None
        elif attribution is not None:
            return None
        call_ids.add(call_id)
        calls.append(
            {
                "call_id": call_id,
                "tool_name": tool_name,
                "terminal_state": terminal_state,
                "attribution": attribution,
            }
        )

    diagnostics: dict[str, int] = {}
    for key in _DIAGNOSTIC_KEYS:
        value = raw_diagnostics.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        diagnostics[key] = value

    allowed_errors = _NOT_COMPUTABLE_REASONS | _INPUT_ERROR_REASONS
    if error is not None and error not in allowed_errors:
        return None
    return (
        _text(trace_id_value),
        mode,
        terminal_semantics,
        catalog_version,
        failure_rules,
        tuple(calls),
        diagnostics,
        error,
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
