import json
from collections.abc import Mapping

import pytest

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.openai_compatible_parameter_judge import JudgeSettings
from evaluators.openai_compatible_time_range_judge import OpenAICompatibleTimeRangeJudge
from evaluators.time_range_accuracy import (
    TimeRangeExpectedField,
    TimeRangeFieldMatchRequest,
    TimeRangeRequirementRequest,
)


def _requirement_request() -> TimeRangeRequirementRequest:
    return TimeRangeRequirementRequest(
        trace_id="trace-1",
        mode="chat",
        query="查询近三年年度行情",
        reference_time="2026-09-03T00:00:00Z",
    )


def _match_request() -> TimeRangeFieldMatchRequest:
    quote_call = ParameterValueCandidate(
        call_id="call-1",
        query="查询近三年年度行情",
        tool_name="quote",
        tool_description="查询指定时间范围内的行情。",
        parameter_descriptions={
            "query": "查询要求，可包含时间范围。",
            "start_date": "业务数据开始日期。",
            "end_date": "业务数据结束日期。",
        },
        schema_field_paths=frozenset({"query", "start_date", "end_date"}),
        arguments={
            "query": "近三年年度行情",
            "start_date": "2023-09-03",
            "end_date": "2026-09-03",
        },
    )
    search_call = ParameterValueCandidate(
        call_id="call-2",
        query="查询近三年年度行情",
        tool_name="searchDomesticReports",
        tool_description="检索研报。",
        parameter_descriptions={
            "start_date": "检索资料的开始日期。",
            "end_date": "检索资料的结束日期。",
        },
        schema_field_paths=frozenset({"start_date", "end_date"}),
        arguments={"start_date": "2020-01-01", "end_date": "2026-09-03"},
    )
    return TimeRangeFieldMatchRequest(
        trace_id="trace-1",
        mode="chat",
        reference_time="2026-09-03T00:00:00Z",
        expected_fields=(
            TimeRangeExpectedField("time_field_0001", 1, "start_time", "2023-09-03"),
            TimeRangeExpectedField("time_field_0002", 1, "end_time", "2026-09-03"),
        ),
        tool_calls=(quote_call, search_call),
        manual_version="0.1.0",
    )


def test_requirement_extraction_does_not_send_tool_arguments() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(
            url=url,
            body=json.loads(body),
            authorization=headers["Authorization"],
            timeout_seconds=timeout_seconds,
        )
        content = {
            "assessment": "assessable",
            "reason_code": "temporal_requirements_extracted",
            "fields": [
                {
                    "range_index": 1,
                    "field_type": "start_time",
                    "expected_value": "2023-09-03",
                },
                {
                    "range_index": 1,
                    "field_type": "end_time",
                    "expected_value": "2026-09-03",
                },
            ],
            "confidence": 0.98,
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleTimeRangeJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.extract_requirements(_requirement_request())

    assert response["assessment"] == "assessable"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload == {
        "query": "查询近三年年度行情",
        "reference_time": "2026-09-03T00:00:00Z",
    }
    assert "tool_calls" not in user_payload


def test_field_matching_sends_only_actual_temporal_evidence_with_roles() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, headers, timeout_seconds
        captured["body"] = json.loads(body)
        content = {
            "time_field_0001": "correct",
            "time_field_0002": "correct",
            "missing_expected_field_ids": [],
            "unexpected_constraint_field_ids": [],
            "incompatible_temporal_role_field_ids": [],
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleTimeRangeJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    response = judge.match_fields(_match_request())

    assert response["time_field_0001"] == "correct"
    assert response["time_field_0002"] == "correct"
    body = captured["body"]
    assert isinstance(body, dict)
    assert "JSON" in body["messages"][0]["content"]
    user_payload = json.loads(body["messages"][1]["content"])
    assert "query" not in user_payload
    assert [field["field_id"] for field in user_payload["expected_fields"]] == [
        "time_field_0001",
        "time_field_0002",
    ]
    evidence_by_tool = {
        call["tool_name"]: call["temporal_evidence"] for call in user_payload["tool_calls"]
    }
    quote_roles = {item["field_path"]: item["temporal_role"] for item in evidence_by_tool["quote"]}
    assert quote_roles == {
        "query": "free_text_query",
        "start_date": "data_period",
        "end_date": "data_period",
    }
    assert {
        item["field_path"]: item["temporal_role"]
        for item in evidence_by_tool["searchDomesticReports"]
    } == {"start_date": "retrieval_window", "end_date": "retrieval_window"}


@pytest.mark.parametrize("method_name", ["extract_requirements", "match_fields"])
def test_invalid_provider_response_is_safely_wrapped(method_name: str) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode()

    judge = OpenAICompatibleTimeRangeJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        if method_name == "extract_requirements":
            judge.extract_requirements(_requirement_request())
        else:
            judge.match_fields(_match_request())

    assert "secret-value" not in str(error.value)
    assert "查询近三年年度行情" not in str(error.value)
