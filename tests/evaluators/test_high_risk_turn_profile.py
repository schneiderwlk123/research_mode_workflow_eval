"""高危回合画像评测器行为测试(稳定性维度 7.11)。

覆盖: 紧凑 JSON 单元格键序、双条件严格不等式阈值边界(steps>30 ∧ rate>0.4)、
rate 与 retry/call 自洽校验、turn_completed 缺失形态、依赖不可计算透传、
组件结构错误透传、未知原因与聊天无 error 契约违约隔离、profile_version
强校验、非法类型拒绝、阈值与版本随元数据声明。
"""

import json
from copy import deepcopy

from evaluators.base import EvaluationStatus
from evaluators.high_risk_turn_profile import HighRiskTurnProfileEvaluator

_CELL_KEYS = [
    "high_risk_turn",
    "step_count",
    "api_retry_rate",
    "api_retry_count",
    "api_call_count",
    "compaction_event_count",
    "turn_completed",
]


def _research_trace(**overrides: object) -> dict[str, object]:
    trace: dict[str, object] = {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "profile_version": "high_risk_profile_v2",
        "step_count": 31,
        "api_retry_rate": 0.5,
        "api_retry_count": 1,
        "api_call_count": 2,
        "compaction_event_count": 1,
        "turn_completed": 1,
        "diagnostics": {
            "duplicate_step_start_projection_count": 0,
            "missing_step_message_id_count": 0,
        },
        "normalization_error": None,
    }
    trace.update(overrides)
    return trace


def _error_trace(reason: str, mode: str = "chat") -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": mode,
        "profile_version": "high_risk_profile_v2" if mode == "research" else None,
        "step_count": None,
        "api_retry_rate": None,
        "api_retry_count": None,
        "api_call_count": None,
        "compaction_event_count": None,
        "turn_completed": None,
        "diagnostics": {
            "duplicate_step_start_projection_count": 0,
            "missing_step_message_id_count": 0,
        },
        "normalization_error": reason,
    }


def test_computed_hit_cell_and_metadata() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    trace = _research_trace()
    original = deepcopy(trace)

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    cell = json.loads(str(result.value))
    assert list(cell.keys()) == _CELL_KEYS
    assert cell["high_risk_turn"] == 1
    assert cell["step_count"] == 31
    assert cell["api_retry_rate"] == 0.5
    assert cell["compaction_event_count"] == 1
    assert cell["turn_completed"] == 1
    assert result.metadata["profile_version"] == "high_risk_profile_v2"
    assert result.metadata["step_count_threshold"] == 30
    assert result.metadata["api_retry_rate_threshold"] == 0.4
    assert trace == original


def test_thresholds_are_strict_inequalities() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    # steps == 30 不满足 > 30
    result = evaluator.evaluate(_research_trace(step_count=30))
    assert json.loads(str(result.value))["high_risk_turn"] == 0
    # rate == 0.4 不满足 > 0.4
    result = evaluator.evaluate(
        _research_trace(api_retry_rate=0.4, api_retry_count=2, api_call_count=5)
    )
    assert json.loads(str(result.value))["high_risk_turn"] == 0
    # 双条件同时越界才命中
    result = evaluator.evaluate(
        _research_trace(api_retry_rate=3 / 7, api_retry_count=3, api_call_count=7)
    )
    assert json.loads(str(result.value))["high_risk_turn"] == 1


def test_rate_must_be_consistent_with_counts() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    result = evaluator.evaluate(_research_trace(api_retry_rate=0.9))
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_missing_turn_completed_is_null_in_cell() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    result = evaluator.evaluate(_research_trace(turn_completed=None))
    assert result.status is EvaluationStatus.COMPUTED
    cell = json.loads(str(result.value))
    assert cell["turn_completed"] is None
    assert cell["high_risk_turn"] == 1


def test_dependency_not_computable_passes_through() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    for reason in ("no_tool_debug_events", "no_observable_api_calls"):
        result = evaluator.evaluate(_error_trace(reason, mode="research"))
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.value is None
        assert result.metadata["reason"] == reason
        assert result.metadata["profile_version"] == "high_risk_profile_v2"


def test_component_structural_errors_pass_through() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    reasons = (
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
        "conflicting_compaction_definition",
    )
    for reason in reasons:
        result = evaluator.evaluate(_error_trace(reason))
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == reason


def test_unknown_reason_and_chat_without_error_are_isolated() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    result = evaluator.evaluate(_error_trace("weird_reason"))
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"

    result = evaluator.evaluate(_error_trace(None))  # type: ignore[arg-type]
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_profile_version_mismatch_is_isolated() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    result = evaluator.evaluate(_research_trace(profile_version="high_risk_profile_v1"))
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_invalid_values_are_isolated() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    violations = [
        _research_trace(step_count=-1),
        _research_trace(step_count=True),
        _research_trace(api_call_count=0),  # 计算路径分母必须 >= 1
        _research_trace(api_retry_count=3),  # 重试 > 调用
        _research_trace(api_retry_rate=1.5),
        _research_trace(turn_completed=2),
        _research_trace(turn_completed=True),  # 布尔与 1 相等,必须显式拒绝
        _research_trace(turn_completed=False),  # 布尔与 0 相等,必须显式拒绝
        _research_trace(compaction_event_count=-1),
        _research_trace(compaction_event_count=None),  # 计算路径压缩计数必须为整数
        _research_trace(profile_version=None),
        _research_trace(
            diagnostics={
                "duplicate_step_start_projection_count": -1,
                "missing_step_message_id_count": 0,
            }
        ),
    ]
    for trace in violations:
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.ERROR, trace
        assert result.metadata["reason"] == "invalid_normalized_trace", trace


def test_error_state_must_not_carry_values() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    trace = _error_trace("no_tool_debug_events", mode="research")
    trace["step_count"] = 40

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_non_mapping_trace_is_isolated() -> None:
    evaluator = HighRiskTurnProfileEvaluator()
    for trace in ("junk", None, [1, 2]):
        result = evaluator.evaluate(trace)  # type: ignore[arg-type]
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
