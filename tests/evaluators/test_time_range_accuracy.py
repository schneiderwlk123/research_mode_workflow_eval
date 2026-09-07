import math
from collections.abc import Mapping

import pytest

from collectors.parameter_value_accuracy import (
    ParameterValueCandidate,
    build_parameter_value_catalog,
)
from collectors.time_range_accuracy import TimeRangeCandidate, normalize_chat_time_range
from evaluators.base import EvaluationStatus
from evaluators.time_range_accuracy import (
    TimeRangeAccuracyEvaluator,
    TimeRangeJudgeRequest,
)


class FakeJudge:
    judge_name = "fake-time-range-judge"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[TimeRangeJudgeRequest] = []

    def judge(self, request: TimeRangeJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


def _tool_call() -> ParameterValueCandidate:
    return ParameterValueCandidate(
        call_id="call-1",
        query="查询 2020A-2025A 和 2026E-2028E 年度数据",
        tool_name="finance",
        tool_description="查询财务数据。",
        parameter_descriptions={
            "start_year": "开始年度。",
            "end_year": "结束年度。",
        },
        schema_field_paths=frozenset({"start_year", "end_year"}),
        arguments={"start_year": 2020, "end_year": 2028},
    )


def _trace(*, error: str | None = None, with_call: bool = True) -> dict[str, object]:
    calls = (_tool_call(),) if with_call else ()
    candidate = TimeRangeCandidate(
        query="查询 2020A-2025A 和 2026E-2028E 年度数据",
        reference_time="2026-09-03T00:00:00Z",
        tool_calls=calls,
    )
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "manual_version": "0.1.0",
        "tool_call_count": len(calls),
        "reference_time_source": "provided",
        "candidate": None if error else candidate,
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "malformed_arguments_call_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def _assessed_response() -> dict[str, object]:
    return {
        "verdict": "assessed",
        "reason_code": "time_fields_compared",
        "correct_field_count": 6,
        "incorrect_field_count": 1,
        "not_assessable_field_count": 0,
        "unexpected_constraint_count": 0,
        "confidence": 0.96,
    }


def _manual() -> dict[str, object]:
    return {
        "manual_version": "0.1.0",
        "tools": [
            {
                "name": "quote",
                "description": "查询指定时间范围内的行情。",
                "parameters": [
                    {
                        "name": "start_date",
                        "type": "string",
                        "required": False,
                        "description": "开始日期。",
                    }
                ],
            }
        ],
    }


def test_assessed_fields_produce_primary_accuracy_and_safe_counts() -> None:
    result = TimeRangeAccuracyEvaluator(FakeJudge(_assessed_response())).evaluate(_trace())

    assert result.metric_name == "time_range_extraction_accuracy"
    assert result.evaluator_version == "1.1.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(6 / 7)
    assert result.metadata["correct_field_count"] == 6
    assert result.metadata["incorrect_field_count"] == 1
    assert result.metadata["assessable_field_count"] == 7
    assert "query" not in result.metadata
    assert "arguments" not in result.metadata


def test_canvas_collector_output_with_meta_reference_time_is_accepted() -> None:
    normalized = normalize_chat_time_range(
        {
            "canvas": {
                "meta": {
                    "traceId": "trace-1",
                    "query": "查询近三年行情",
                    "create_time": "2026-09-03T10:00:00+08:00",
                },
                "agents": [
                    {
                        "messages": [
                            {
                                "content": {
                                    "tool_call": [
                                        {
                                            "toolId": "call-1",
                                            "toolName": "quote",
                                            "arguments": {"start_date": "2023-09-03"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ],
            }
        },
        build_parameter_value_catalog(_manual()),
    )

    result = TimeRangeAccuracyEvaluator(FakeJudge(_assessed_response())).evaluate(normalized)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.evaluator_version == "1.1.0"
    assert result.metadata["reference_time_source"] == "canvas_meta"
    assert result.metadata["reference_time_present"] is True


def test_no_calls_can_be_scored_as_total_omission() -> None:
    response = _assessed_response() | {
        "correct_field_count": 0,
        "incorrect_field_count": 4,
    }
    judge = FakeJudge(response)

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(with_call=False))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    assert judge.requests[0].tool_calls == ()


def test_research_mode_uses_the_same_field_accuracy_formula() -> None:
    result = TimeRangeAccuracyEvaluator(FakeJudge(_assessed_response())).evaluate(
        _trace() | {"mode": "research", "reference_time_source": "event"}
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(6 / 7)
    assert result.metadata["mode"] == "research"


@pytest.mark.parametrize(
    "reason",
    [
        "no_temporal_requirement",
        "missing_reference_time",
        "ambiguous_temporal_requirement",
        "insufficient_time_evidence",
    ],
)
def test_not_assessable_reason_is_preserved(reason: str) -> None:
    response = {
        "verdict": "not_assessable",
        "reason_code": reason,
        "correct_field_count": 0,
        "incorrect_field_count": 0,
        "not_assessable_field_count": 1,
        "unexpected_constraint_count": 0,
        "confidence": 0.7,
    }

    result = TimeRangeAccuracyEvaluator(FakeJudge(response)).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == reason


@pytest.mark.parametrize(
    "response",
    [
        {},
        _assessed_response() | {"verdict": "unknown"},
        _assessed_response() | {"reason_code": "secret input"},
        _assessed_response() | {"correct_field_count": -1},
        _assessed_response() | {"confidence": math.nan},
        _assessed_response() | {"explanation": "不得持久化"},
        _assessed_response() | {"correct_field_count": 0, "incorrect_field_count": 0},
    ],
)
def test_invalid_judge_output_is_not_computable(response: Mapping[str, object]) -> None:
    result = TimeRangeAccuracyEvaluator(FakeJudge(response)).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"
    assert "不得持久化" not in repr(result.metadata)


def test_judge_failure_and_normalization_error_are_isolated() -> None:
    failure = TimeRangeAccuracyEvaluator(FakeJudge(RuntimeError("敏感供应商错误"))).evaluate(
        _trace()
    )
    judge = FakeJudge(_assessed_response())
    normalization_error = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(error="missing_query"))

    assert failure.status is EvaluationStatus.NOT_COMPUTABLE
    assert failure.metadata["reason"] == "judge_failure"
    assert "敏感供应商错误" not in repr(failure.metadata)
    assert normalization_error.status is EvaluationStatus.NOT_COMPUTABLE
    assert normalization_error.metadata["reason"] == "missing_query"
    assert judge.requests == []


def test_invalid_normalized_trace_is_error() -> None:
    result = TimeRangeAccuracyEvaluator(FakeJudge(_assessed_response())).evaluate(
        _trace() | {"tool_call_count": 2}
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
