"""计算单条 Trace 的高危回合画像标记并输出紧凑 JSON 值(稳定性维度 7.11)。

口径(《初步入库评测指标.md》7.11,2026-09-21 需求方确认双条件阈值):

    high_risk_turn = (step_count > 30) ∧ (api_retry_rate > 0.4)

- 报告级组合规则,非单值指标,不进宽表单值列;报告级高危回合率由聚合层
  基于单 Trace 标记派生,评测器不做跨 Trace 聚合。
- 阈值为冻结常量(严格不等式),规则版本 `high_risk_profile_v2`
  (v1 四条件公式实测 47 回合 0 命中已废弃);阈值与版本随元数据声明,
  版本不匹配隔离为 invalid_normalized_trace,禁止跨版本混比。
- 值列为紧凑 JSON 对象(键序固定):0/1 标记 + 命中特征清单(step 数、
  重试率与分子分母、压缩次数、闭环标记);空结果数特征项待 7.9 立项后
  补入(2026-09-21 需求方确认本批省略),届时须升版。
- 命中语义是"高摩擦/高成本回合筛查",不等价死亡回合预测;闭环标记为
  None(缺回合边界)不阻断主值,交报告级与 7.1/7.2 联合解读。
- 依赖 7.8 不可计算(no_tool_debug_events / no_observable_api_calls)时
  透传 NOT_COMPUTABLE——不代表无风险;组件结构错误透传 ERROR。
- 守恒校验: rate 必须与 retry/call 商一致、重试 <= 调用、压缩计数与
  step 计数为非负整数、闭环标记 ∈ {0,1,None};违约隔离。
"""

import json
import math
from collections.abc import Mapping
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_EXPECTED_PROFILE_VERSION = "high_risk_profile_v2"
# 冻结阈值(严格不等式):step 数 > 30 且 api_retry_rate > 0.4。
_STEP_COUNT_THRESHOLD = 30
_API_RETRY_RATE_THRESHOLD = 0.4

_NOT_COMPUTABLE_REASONS = frozenset({"no_tool_debug_events", "no_observable_api_calls"})
_ERROR_REASONS = frozenset(
    {
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
        "conflicting_compaction_definition",
    }
)
_DIAGNOSTIC_KEYS = (
    "duplicate_step_start_projection_count",
    "missing_step_message_id_count",
)


class HighRiskTurnProfileEvaluator(BaseEvaluator):
    """校验规范化画像特征并输出双条件 0/1 标记;缺依赖不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "high_risk_turn_profile"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, features, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # 聊天模式画像版本恒为 None(依赖通道不存在,无计算路径)。
            "profile_version": features["profile_version"],
            "step_count_threshold": _STEP_COUNT_THRESHOLD,
            "api_retry_rate_threshold": _API_RETRY_RATE_THRESHOLD,
            **{key: diagnostics[key] for key in _DIAGNOSTIC_KEYS},
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

        step_count = features["step_count"]
        rate = features["api_retry_rate"]
        # _parse_trace 已保证计算路径下各特征为合法数值。
        assert isinstance(step_count, int)  # noqa: S101 - 规范化契约保证
        assert isinstance(rate, float)  # noqa: S101 - 规范化契约保证
        high_risk = (
            1 if (step_count > _STEP_COUNT_THRESHOLD and rate > _API_RETRY_RATE_THRESHOLD) else 0
        )
        payload = {
            "high_risk_turn": high_risk,
            "step_count": step_count,
            "api_retry_rate": rate,
            "api_retry_count": features["api_retry_count"],
            "api_call_count": features["api_call_count"],
            "compaction_event_count": features["compaction_event_count"],
            "turn_completed": features["turn_completed"],
        }
        value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
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
) -> tuple[str | None, str, dict[str, object], Mapping[str, object], str | None] | None:
    if not isinstance(trace, Mapping):
        return None
    mode = _text(trace.get("mode"))
    if mode not in {"chat", "research"}:
        return None
    trace_id_value = trace.get("trace_id")
    trace_id = _text(trace_id_value)
    if trace_id_value is not None and trace_id is None:
        return None

    version_value = trace.get("profile_version")
    version = _text(version_value)
    if mode == "research":
        # 研究路径(含错误路径)恒携带冻结版本;版本漂移即口径违约。
        if version != _EXPECTED_PROFILE_VERSION:
            return None
    elif version_value is not None:
        return None

    diagnostics = trace.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in _DIAGNOSTIC_KEYS:
        if not _is_count(diagnostics.get(key)):
            return None

    error_value = trace.get("normalization_error")
    error = _text(error_value)
    if error_value is not None and error is None:
        return None
    if mode == "chat" and error is None:
        # 聊天模式依赖通道不存在,契约上恒携带原因码。
        return None

    features = _parse_features(trace, error)
    if features is None:
        return None
    features["profile_version"] = version
    return trace_id, mode, features, diagnostics, error


def _parse_features(trace: Mapping[str, object], error: str | None) -> dict[str, object] | None:
    """校验特征字段;error 路径执行互斥校验,计算路径执行守恒校验。"""

    completed = trace.get("turn_completed")
    if error is not None:
        # error 与特征互斥:任何特征字段不得携带值。
        if any(
            trace.get(key) is not None
            for key in (
                "step_count",
                "api_retry_rate",
                "api_retry_count",
                "api_call_count",
                "compaction_event_count",
                "turn_completed",
            )
        ):
            return None
        return {
            "step_count": None,
            "api_retry_rate": None,
            "api_retry_count": None,
            "api_call_count": None,
            "compaction_event_count": None,
            "turn_completed": None,
        }

    step_count = trace.get("step_count")
    rate = trace.get("api_retry_rate")
    retry_count = trace.get("api_retry_count")
    call_count = trace.get("api_call_count")
    compaction_count = trace.get("compaction_event_count")
    if (
        not _is_count(step_count)
        or not _is_count(retry_count)
        or not _is_count(call_count)
        or not _is_count(compaction_count)
        or call_count < 1
        or retry_count > call_count
        or isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0.0 <= float(rate) <= 1.0
        or not math.isclose(float(rate), retry_count / call_count, rel_tol=1e-9, abs_tol=1e-12)
        # 布尔与 0/1 相等,必须显式拒绝,防止 True/False 混入闭环标记。
        or isinstance(completed, bool)
        or completed not in (0, 1, None)
    ):
        return None
    return {
        "step_count": step_count,
        "api_retry_rate": float(rate),
        "api_retry_count": retry_count,
        "api_call_count": call_count,
        "compaction_event_count": compaction_count,
        "turn_completed": completed,
    }


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
