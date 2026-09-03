from collections.abc import Mapping

import pytest

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.base import EvaluationStatus
from evaluators.parameter_value_accuracy import (
    ParameterValueAccuracyEvaluator,
    ParameterValueJudgeRequest,
)


class FakeJudge:
    judge_name = "fake-judge"
    judge_version = "2026-09-03"
    prompt_version = "1.0.0"

    def __init__(self, responses: Mapping[str, object]) -> None:
        self._responses = responses

    def judge(self, request: ParameterValueJudgeRequest) -> Mapping[str, object]:
        response = self._responses[request.call_id]
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, Mapping)
        return response


def _candidate(
    call_id: str,
    arguments: Mapping[str, object],
) -> ParameterValueCandidate:
    return ParameterValueCandidate(
        call_id=call_id,
        query="查询 ABC 在 2026 年 1 月的美元行情",
        tool_name="quote",
        tool_description="查询行情。",
        parameter_descriptions={
            "symbol": "证券代码。",
            "start_date": "开始日期。",
            "end_date": "结束日期。",
            "limit": "返回条数。",
        },
        schema_field_paths=frozenset({"symbol", "start_date", "end_date", "limit"}),
        arguments=arguments,
    )


def _trace(*candidates: ParameterValueCandidate) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "manual_version": "0.1.0",
        "total_call_count": len(candidates),
        "eligible_call_count": len(candidates),
        "candidates": candidates,
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "malformed_arguments_call_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }


def test_evaluator_aggregates_field_accuracy_full_match_and_coverage() -> None:
    judge = FakeJudge(
        {
            "call-1": {
                "fields": [
                    {
                        "field_path": "symbol",
                        "verdict": "correct",
                        "reason_code": "direct_match",
                        "confidence": 1.0,
                    },
                    {
                        "field_path": "start_date",
                        "verdict": "incorrect",
                        "reason_code": "contradicts_query",
                        "confidence": 0.99,
                    },
                    {
                        "field_path": "limit",
                        "verdict": "not_assessable",
                        "reason_code": "query_underspecified",
                        "confidence": 0.95,
                    },
                    {
                        "field_path": "end_date",
                        "verdict": "incorrect",
                        "reason_code": "missing_query_required",
                        "confidence": 0.98,
                    },
                ]
            },
            "call-2": {
                "fields": [
                    {
                        "field_path": "symbol",
                        "verdict": "correct",
                        "reason_code": "normalized_equivalent",
                        "confidence": 0.97,
                    }
                ]
            },
        }
    )
    evaluator = ParameterValueAccuracyEvaluator(judge)
    trace = _trace(
        _candidate("call-1", {"symbol": "ABC", "start_date": "2026-02", "limit": 10}),
        _candidate("call-2", {"symbol": "ABC"}),
    )

    result = evaluator.evaluate(trace)

    assert result.metric_name == "parameter_value_accuracy_rate"
    assert result.evaluator_version == "1.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(2 / 4)
    assert result.metadata["call_parameter_full_match_rate"] == pytest.approx(1 / 2)
    assert result.metadata["judge_assessable_coverage_rate"] == pytest.approx(4 / 5)
    assert result.metadata["correct_field_count"] == 2
    assert result.metadata["incorrect_field_count"] == 2
    assert result.metadata["not_assessable_field_count"] == 1
    assert result.metadata["judge_name"] == "fake-judge"
    assert "query" not in result.metadata
    assert "arguments" not in result.metadata


def test_partial_judge_failure_is_isolated_from_computable_calls() -> None:
    judge = FakeJudge(
        {
            "call-1": {
                "fields": [
                    {
                        "field_path": "symbol",
                        "verdict": "correct",
                        "reason_code": "direct_match",
                        "confidence": 1.0,
                    }
                ]
            },
            "call-2": RuntimeError("temporary failure"),
        }
    )

    result = ParameterValueAccuracyEvaluator(judge).evaluate(
        _trace(
            _candidate("call-1", {"symbol": "ABC"}),
            _candidate("call-2", {"symbol": "ABC"}),
        )
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["judge_failed_call_count"] == 1
    assert result.metadata["judged_call_count"] == 1


def test_all_not_assessable_fields_are_not_computable() -> None:
    judge = FakeJudge(
        {
            "call-1": {
                "fields": [
                    {
                        "field_path": "limit",
                        "verdict": "not_assessable",
                        "reason_code": "query_underspecified",
                        "confidence": 0.9,
                    }
                ]
            }
        }
    )

    result = ParameterValueAccuracyEvaluator(judge).evaluate(
        _trace(_candidate("call-1", {"limit": 10}))
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_assessable_fields"
    assert result.metadata["judge_assessable_coverage_rate"] == 0.0


@pytest.mark.parametrize(
    "response",
    [
        {"fields": []},
        {
            "fields": [
                {
                    "field_path": "unknown",
                    "verdict": "correct",
                    "reason_code": "direct_match",
                    "confidence": 1.0,
                }
            ]
        },
        {
            "fields": [
                {
                    "field_path": "symbol",
                    "verdict": "maybe",
                    "reason_code": "direct_match",
                    "confidence": 1.0,
                }
            ]
        },
        {
            "fields": [
                {
                    "field_path": "symbol",
                    "verdict": "incorrect",
                    "reason_code": "actual value was SECRET",
                    "confidence": 1.0,
                }
            ]
        },
    ],
)
def test_invalid_judge_output_isolated_as_error(response: Mapping[str, object]) -> None:
    result = ParameterValueAccuracyEvaluator(FakeJudge({"call-1": response})).evaluate(
        _trace(_candidate("call-1", {"symbol": "ABC"}))
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "judge_failure"
    assert result.metadata["judge_failed_call_count"] == 1


@pytest.mark.parametrize(
    ("trace", "reason"),
    [
        (_trace(), "no_tool_calls"),
        (
            _trace(_candidate("call-1", {"symbol": "ABC"}))
            | {"normalization_error": "missing_query"},
            "missing_query",
        ),
    ],
)
def test_missing_inputs_are_not_computable(trace: dict[str, object], reason: str) -> None:
    result = ParameterValueAccuracyEvaluator(FakeJudge({})).evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == reason
