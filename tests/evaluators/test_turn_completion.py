"""回合完成率评测器行为测试。

覆盖: 双模式 0/1 主值与 completion_semantics、缺回合边界不可计算、结构冲突
错误、非法规范化输入隔离、诊断计数透传与输入不可变。
"""

from collections.abc import Mapping
from copy import deepcopy

from evaluators.base import EvaluationStatus
from evaluators.turn_completion import TurnCompletionRateEvaluator


def _normalized(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "turn_completed": 1,
        "diagnostics": {
            "failure_template_hit_count": 0,
            "missing_or_empty_answer_count": 0,
            "duplicate_turn_end_count": 0,
            "mismatched_turn_end_count": 0,
            "duration_diagnostic": None,
        },
        "normalization_error": None,
    }
    base.update(overrides)
    return base


def test_research_closed_turn_computes_one_without_mutation() -> None:
    evaluator = TurnCompletionRateEvaluator()
    trace = _normalized()
    original = deepcopy(trace)

    result = evaluator.evaluate(trace)

    assert result.metric_name == "turn_completion_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1
    assert result.metadata["mode"] == "research"
    assert result.metadata["completion_semantics"] == "turn_end_event"
    assert result.metadata["trace_id"] == "ses-1:msg-1"
    assert trace == original


def test_chat_proxy_zero_carries_template_diagnostic() -> None:
    evaluator = TurnCompletionRateEvaluator()
    trace = _normalized(
        trace_id="trace-1",
        mode="chat",
        turn_completed=0,
        diagnostics={
            "failure_template_hit_count": 1,
            "missing_or_empty_answer_count": 0,
            "duplicate_turn_end_count": 0,
            "mismatched_turn_end_count": 0,
            "duration_diagnostic": None,
        },
    )

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0
    assert result.metadata["completion_semantics"] == "answer_presence_proxy"
    assert result.metadata["failure_template_hit_count"] == 1


def test_missing_turn_boundary_is_not_computable() -> None:
    evaluator = TurnCompletionRateEvaluator()

    result = evaluator.evaluate(
        _normalized(turn_completed=None, normalization_error="missing_turn_boundary")
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "missing_turn_boundary"


def test_structure_and_conflict_errors_map_to_error_status() -> None:
    evaluator = TurnCompletionRateEvaluator()

    for reason in (
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
    ):
        result = evaluator.evaluate(_normalized(turn_completed=None, normalization_error=reason))
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == reason


def test_invalid_normalized_inputs_are_isolated() -> None:
    evaluator = TurnCompletionRateEvaluator()

    bad_inputs: tuple[Mapping[str, object], ...] = (
        {},
        _normalized(turn_completed=2),
        _normalized(turn_completed=True),
        _normalized(mode="unknown"),
        _normalized(diagnostics=None),
        _normalized(diagnostics={"failure_template_hit_count": -1}),
        _normalized(normalization_error="made_up_reason"),
        _normalized(
            turn_completed=1,
            diagnostics={
                "failure_template_hit_count": 0,
                "missing_or_empty_answer_count": 0,
                "duplicate_turn_end_count": 0,
                "mismatched_turn_end_count": 0,
                "duration_diagnostic": "unexpected_diagnostic",
            },
        ),
    )

    for trace in bad_inputs:
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.ERROR
        assert result.value is None
        assert result.metadata["reason"] == "invalid_normalized_trace"


def test_duration_and_boundary_diagnostics_pass_through() -> None:
    evaluator = TurnCompletionRateEvaluator()

    result = evaluator.evaluate(
        _normalized(
            diagnostics={
                "failure_template_hit_count": 0,
                "missing_or_empty_answer_count": 0,
                "duplicate_turn_end_count": 2,
                "mismatched_turn_end_count": 1,
                "duration_diagnostic": "missing_duration_ms",
            },
        )
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["duplicate_turn_end_count"] == 2
    assert result.metadata["mismatched_turn_end_count"] == 1
    assert result.metadata["duration_diagnostic"] == "missing_duration_ms"


def test_unsupported_container_is_error() -> None:
    evaluator = TurnCompletionRateEvaluator()

    result = evaluator.evaluate("not-a-mapping")  # type: ignore[arg-type]

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
