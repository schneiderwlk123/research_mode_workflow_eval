from collections.abc import Mapping

import pytest

from collectors.parameter_value_accuracy import ParameterValueCandidate
from collectors.time_range_accuracy import TimeRangeCandidate
from evaluators.base import EvaluationStatus
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError
from evaluators.time_range_accuracy import (
    TimeRangeAccuracyEvaluator,
    TimeRangeFieldMatchRequest,
    TimeRangeRequirementRequest,
)


class FakeJudge:
    judge_name = "fake-time-range-judge"
    judge_version = "test-model"
    prompt_version = "2.0.0"

    def __init__(self, extraction: object, matching: object | None = None) -> None:
        self.extraction = extraction
        self.matching = matching
        self.extraction_requests: list[TimeRangeRequirementRequest] = []
        self.match_requests: list[TimeRangeFieldMatchRequest] = []

    def extract_requirements(
        self, request: TimeRangeRequirementRequest
    ) -> Mapping[str, object]:
        self.extraction_requests.append(request)
        if isinstance(self.extraction, Exception):
            raise self.extraction
        assert isinstance(self.extraction, Mapping)
        return self.extraction

    def match_fields(self, request: TimeRangeFieldMatchRequest) -> Mapping[str, object]:
        self.match_requests.append(request)
        if isinstance(self.matching, Exception):
            raise self.matching
        assert isinstance(self.matching, Mapping)
        return self.matching


class FlakyJudge(FakeJudge):
    def __init__(
        self,
        extractions: list[Mapping[str, object]],
        matchings: list[Mapping[str, object]],
    ) -> None:
        super().__init__(extractions[-1], matchings[-1])
        self._extractions = iter(extractions)
        self._matchings = iter(matchings)

    def extract_requirements(
        self, request: TimeRangeRequirementRequest
    ) -> Mapping[str, object]:
        self.extraction_requests.append(request)
        return next(self._extractions)

    def match_fields(self, request: TimeRangeFieldMatchRequest) -> Mapping[str, object]:
        self.match_requests.append(request)
        return next(self._matchings)


def _tool_call() -> ParameterValueCandidate:
    return ParameterValueCandidate(
        call_id="call-1",
        query="查询 2025 年年度数据",
        tool_name="finance",
        tool_description="查询财务数据。",
        parameter_descriptions={"start_year": "开始年度。", "end_year": "结束年度。"},
        schema_field_paths=frozenset({"start_year", "end_year"}),
        arguments={"start_year": 2025, "end_year": 2025},
    )


def _trace(
    *,
    query: str = "查询 2025 年年度数据",
    error: str | None = None,
    with_call: bool = True,
    unknown_schema_call_count: int = 0,
) -> dict[str, object]:
    calls = (_tool_call(),) if with_call else ()
    candidate = TimeRangeCandidate(
        query=query,
        reference_time="2026-09-03T00:00:00Z",
        tool_calls=calls,
    )
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "manual_version": "0.1.0",
        "tool_call_count": len(calls) + unknown_schema_call_count,
        "reference_time_source": "provided",
        "candidate": None if error else candidate,
        "diagnostics": {
            "unknown_schema_call_count": unknown_schema_call_count,
            "malformed_arguments_call_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def _extraction(*fields: tuple[int, str, str]) -> dict[str, object]:
    return {
        "assessment": "assessable",
        "reason_code": "temporal_requirements_extracted",
        "fields": [
            {"range_index": index, "field_type": field_type, "expected_value": value}
            for index, field_type, value in fields
        ],
        "confidence": 0.91,
    }


def _matching(*verdicts: str) -> dict[str, object]:
    response: dict[str, object] = {
        f"time_field_{index:04d}": verdict
        for index, verdict in enumerate(verdicts, start=1)
    }
    response.update(
        {
            "missing_expected_field_ids": [],
            "unexpected_constraint_field_ids": [],
            "incompatible_temporal_role_field_ids": [],
        }
    )
    return response


def test_field_ids_drive_accuracy_and_safe_metadata() -> None:
    judge = FakeJudge(
        _extraction(
            (1, "start_time", "2025-01-01"),
            (1, "end_time", "2025-12-31"),
            (1, "value_type", "historical_actual"),
        ),
        _matching("correct", "incorrect", "correct"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(query="查询 2025 年历史实际年度数据")
    )

    assert result.metric_name == "time_range_extraction_accuracy"
    assert result.evaluator_version == "2.0.0"
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == pytest.approx(2 / 3)
    assert result.metadata["correct_field_count"] == 2
    assert result.metadata["incorrect_field_count"] == 1
    assert result.metadata["assessable_field_count"] == 3
    assert result.metadata["expected_field_types"] == (
        "start_time",
        "end_time",
        "value_type",
    )
    assert result.metadata["field_verdicts"] == (
        ("time_field_0001", "correct", "matched"),
        ("time_field_0002", "incorrect", "contradicts_requirement"),
        ("time_field_0003", "correct", "matched"),
    )
    assert "query" not in result.metadata
    assert "expected_value" not in repr(result.metadata)
    assert "arguments" not in result.metadata


def test_query_only_extraction_prevents_tool_arguments_from_creating_denominator() -> None:
    judge = FakeJudge(
        {
            "assessment": "not_assessable",
            "reason_code": "no_temporal_requirement",
            "fields": [],
            "confidence": 0.95,
        }
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(query="冰轮环境"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_temporal_requirement"
    assert len(judge.extraction_requests) == 1
    assert not hasattr(judge.extraction_requests[0], "tool_calls")
    assert judge.match_requests == []


@pytest.mark.parametrize(
    ("query", "field_type", "expected_value"),
    [("有什么最新动态", "freshness", "latest"), ("未来走势如何", "value_type", "forecast")],
)
def test_relative_freshness_and_forecast_can_form_explicit_fields(
    query: str, field_type: str, expected_value: str
) -> None:
    judge = FakeJudge(_extraction((0, field_type, expected_value)), _matching("correct"))

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(query=query))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["expected_field_types"] == (field_type,)


def test_explicit_year_recovers_from_false_no_temporal_judgment() -> None:
    judge = FakeJudge(
        {
            "assessment": "not_assessable",
            "reason_code": "no_temporal_requirement",
            "fields": [],
            "confidence": 0.9,
        },
        _matching("correct", "correct"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(query="验证一下25年国内需求量")
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["expected_field_types"] == ("start_time", "end_time")
    assert result.value == 1.0


def test_rolling_range_drops_redundant_freshness_and_frequency_fields() -> None:
    judge = FakeJudge(
        _extraction(
            (1, "start_time", "2026-03-03"),
            (1, "end_time", "2026-09-03"),
            (1, "frequency", "monthly"),
            (0, "freshness", "latest"),
        ),
        _matching("correct", "correct"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(query="查询最近半年的汽车出口数据")
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["expected_field_types"] == ("start_time", "end_time")


def test_future_signal_recovers_from_false_no_temporal_judgment() -> None:
    judge = FakeJudge(
        {
            "assessment": "not_assessable",
            "reason_code": "no_temporal_requirement",
            "fields": [],
            "confidence": 0.9,
        },
        _matching("correct"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(query="未来走势如何"))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["expected_field_types"] == ("value_type",)


def test_no_calls_are_deterministically_scored_as_total_omission() -> None:
    judge = FakeJudge(
        _extraction((1, "start_time", "2025-01-01"), (1, "end_time", "2025-12-31"))
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace(with_call=False))

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.0
    assert result.metadata["incorrect_field_count"] == 2
    assert result.metadata["field_verdicts"] == (
        ("time_field_0001", "incorrect", "missing_expected_field"),
        ("time_field_0002", "incorrect", "missing_expected_field"),
    )
    assert judge.match_requests == []


def test_only_unknown_schema_calls_are_not_treated_as_total_omission() -> None:
    judge = FakeJudge(_extraction((1, "start_time", "2025-01-01")))

    result = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(with_call=False, unknown_schema_call_count=1)
    )

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "insufficient_time_evidence"
    assert judge.match_requests == []


def test_retrieval_window_keeps_the_same_expected_field_ids() -> None:
    judge = FakeJudge(
        _extraction(
            (1, "start_time", "2025-01-01"),
            (1, "end_time", "2025-12-31"),
            (1, "value_type", "historical_actual"),
        ),
        _matching("correct", "correct", "correct"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(query="查询 2025 年历史实际年度数据")
    )

    assert result.status is EvaluationStatus.COMPUTED
    assert [field.field_id for field in judge.match_requests[0].expected_fields] == [
        "time_field_0001",
        "time_field_0002",
        "time_field_0003",
    ]


def test_missing_field_id_makes_judge_output_invalid() -> None:
    response = _matching("correct", "correct")
    del response["time_field_0002"]
    result = TimeRangeAccuracyEvaluator(
        FakeJudge(
            _extraction((1, "start_time", "2025-01-01"), (1, "end_time", "2025-12-31")),
            response,
        )
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_invalid_matching_output_is_retried_once() -> None:
    valid = _matching("correct", "correct")
    invalid = dict(valid)
    del invalid["time_field_0002"]
    judge = FlakyJudge(
        [_extraction((1, "start_time", "2025-01-01"), (1, "end_time", "2025-12-31"))],
        [invalid, valid],
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["judge_validation_retry_count"] == 1
    assert len(judge.match_requests) == 2


def test_omitted_empty_matching_diagnostics_default_to_empty() -> None:
    matching = _matching("correct", "incorrect")
    del matching["missing_expected_field_ids"]
    del matching["unexpected_constraint_field_ids"]
    del matching["incompatible_temporal_role_field_ids"]

    result = TimeRangeAccuracyEvaluator(
        FakeJudge(
            _extraction((1, "start_time", "2025-01-01"), (1, "end_time", "2025-12-31")),
            matching,
        )
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 0.5
    assert result.metadata["unexpected_constraint_count"] == 0


def test_invalid_requirement_output_is_retried_once() -> None:
    judge = FlakyJudge(
        [
            {},
            _extraction(
                (1, "start_time", "2025-01-01"),
                (1, "end_time", "2025-12-31"),
            ),
        ],
        [_matching("correct", "correct")],
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["judge_validation_retry_count"] == 1
    assert len(judge.extraction_requests) == 2


def test_not_assessable_field_keeps_complete_denominator_out_of_metrics() -> None:
    judge = FakeJudge(
        _extraction((1, "start_time", "2025-01-01"), (1, "end_time", "2025-12-31")),
        _matching("correct", "not_assessable"),
    )

    result = TimeRangeAccuracyEvaluator(judge).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "insufficient_time_evidence"
    assert result.metadata["not_assessable_field_count"] == 1


def test_unexpected_constraints_require_an_incorrect_expected_field() -> None:
    matching = _matching("correct")
    matching["unexpected_constraint_field_ids"] = ["time_field_0001"]
    result = TimeRangeAccuracyEvaluator(
        FakeJudge(_extraction((0, "freshness", "latest")), matching)
    ).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {
            "assessment": "assessable",
            "reason_code": "temporal_requirements_extracted",
            "fields": [],
            "confidence": 0.9,
        },
        {
            "assessment": "not_assessable",
            "reason_code": "no_temporal_requirement",
            "fields": [
                {"range_index": 0, "field_type": "freshness", "expected_value": "latest"}
            ],
            "confidence": 0.9,
        },
        {
            "assessment": "assessable",
            "reason_code": "temporal_requirements_extracted",
            "fields": [
                {"range_index": 0, "field_type": "unknown", "expected_value": "latest"}
            ],
            "confidence": 0.9,
        },
    ],
)
def test_invalid_requirement_extraction_is_not_computable(response: Mapping[str, object]) -> None:
    result = TimeRangeAccuracyEvaluator(FakeJudge(response)).evaluate(_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_judge_failures_and_normalization_errors_are_isolated() -> None:
    extraction_failure = TimeRangeAccuracyEvaluator(
        FakeJudge(RuntimeError("敏感供应商错误"))
    ).evaluate(_trace())
    matching_failure = TimeRangeAccuracyEvaluator(
        FakeJudge(
            _extraction((1, "start_time", "2025-01-01")),
            JudgeExecutionError("judge_output_truncated", {"response_byte_count": 2048}),
        )
    ).evaluate(_trace())
    judge = FakeJudge(_extraction((1, "start_time", "2025-01-01")), _matching("correct"))
    normalization_error = TimeRangeAccuracyEvaluator(judge).evaluate(
        _trace(error="missing_query")
    )

    assert extraction_failure.status is EvaluationStatus.NOT_COMPUTABLE
    assert extraction_failure.metadata["reason"] == "judge_failure"
    assert "敏感供应商错误" not in repr(extraction_failure.metadata)
    assert matching_failure.metadata["reason"] == "judge_output_truncated"
    assert matching_failure.metadata["response_byte_count"] == 2048
    assert normalization_error.metadata["reason"] == "missing_query"
    assert judge.extraction_requests == []


def test_invalid_normalized_trace_is_error() -> None:
    result = TimeRangeAccuracyEvaluator(
        FakeJudge(_extraction((1, "start_time", "2025-01-01")), _matching("correct"))
    ).evaluate(_trace() | {"tool_call_count": 2})

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"
