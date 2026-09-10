from evaluators.base import EvaluationStatus
from evaluators.tool_resource_usage import (
    ToolCallDurationEvaluator,
    ToolTokenConsumptionEvaluator,
)


def _call(*, duration_ms: object = 1_250, duration_error: object = None) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "call_id": "call-1",
        "tool_name": "search",
        "duration_ms": duration_ms,
        "duration_error": duration_error,
        "token_total": None,
        "token_error": "no_observable_tool_token_usage",
    }


def test_duration_evaluator_returns_per_call_value_and_safe_identifiers() -> None:
    result = ToolCallDurationEvaluator().evaluate(_call())

    assert result.metric_name == "tool_call_duration_ms"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1_250
    assert dict(result.metadata) == {
        "trace_id": "trace-1",
        "mode": "chat",
        "call_id": "call-1",
        "tool_name": "search",
    }


def test_duration_evaluator_separates_missing_and_invalid_observations() -> None:
    missing = ToolCallDurationEvaluator().evaluate(
        _call(duration_ms=None, duration_error="missing_tool_duration")
    )
    invalid = ToolCallDurationEvaluator().evaluate(
        _call(duration_ms=None, duration_error="invalid_duration")
    )

    assert missing.status is EvaluationStatus.NOT_COMPUTABLE
    assert missing.value is None
    assert missing.metadata["reason"] == "missing_tool_duration"
    assert invalid.status is EvaluationStatus.ERROR
    assert invalid.metadata["reason"] == "invalid_duration"


def test_tool_token_evaluator_is_explicitly_not_computable_in_both_modes() -> None:
    evaluator = ToolTokenConsumptionEvaluator()
    chat = evaluator.evaluate(_call())
    research_call = _call()
    research_call["mode"] = "research"
    research = evaluator.evaluate(research_call)

    for result in (chat, research):
        assert result.metric_name == "tool_token_consumption"
        assert result.evaluator_version == "1.0.0"
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.value is None
        assert result.metadata["reason"] == "no_observable_tool_token_usage"


def test_evaluators_reject_inconsistent_or_malformed_normalized_calls() -> None:
    inconsistent_duration = ToolCallDurationEvaluator().evaluate(
        _call(duration_ms=1, duration_error="missing_tool_duration")
    )
    fake_token = _call()
    fake_token["token_total"] = 10
    malformed_token = ToolTokenConsumptionEvaluator().evaluate(fake_token)

    assert inconsistent_duration.status is EvaluationStatus.ERROR
    assert inconsistent_duration.metadata["reason"] == "invalid_normalized_trace"
    assert malformed_token.status is EvaluationStatus.ERROR
    assert malformed_token.metadata["reason"] == "invalid_normalized_trace"
