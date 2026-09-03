import json
from collections.abc import Mapping

import pytest

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.openai_compatible_parameter_judge import JudgeSettings
from evaluators.openai_compatible_time_range_judge import OpenAICompatibleTimeRangeJudge
from evaluators.time_range_accuracy import TimeRangeJudgeRequest


def _request() -> TimeRangeJudgeRequest:
    call = ParameterValueCandidate(
        call_id="call-1",
        query="查询近三年年度行情",
        tool_name="quote",
        tool_description="查询指定时间范围内的行情。",
        parameter_descriptions={"start_date": "开始日期。", "end_date": "结束日期。"},
        schema_field_paths=frozenset({"start_date", "end_date"}),
        arguments={
            "start_date": "2023-09-03",
            "end_date": "2026-09-03",
            "options": {"periods": ["2025A", "2026E"]},
        },
    )
    return TimeRangeJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="查询近三年年度行情",
        reference_time="2026-09-03T00:00:00Z",
        tool_calls=(call,),
        manual_version="0.1.0",
    )


def test_time_range_judge_sends_schema_context_and_parses_response() -> None:
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
            "verdict": "assessed",
            "reason_code": "time_fields_compared",
            "correct_field_count": 4,
            "incorrect_field_count": 0,
            "not_assessable_field_count": 0,
            "unexpected_constraint_count": 0,
            "confidence": 0.98,
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleTimeRangeJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.judge(_request())

    assert response["verdict"] == "assessed"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["reference_time"] == "2026-09-03T00:00:00Z"
    assert user_payload["tool_calls"][0]["arguments"]["start_date"] == "2023-09-03"
    assert user_payload["tool_calls"][0]["arguments"]["options"]["periods"] == [
        "2025A",
        "2026E",
    ]
    assert user_payload["tool_calls"][0]["parameter_descriptions"]["end_date"] == "结束日期。"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not-json"}}]},
        {"error": {"message": "provider failure"}},
    ],
)
def test_invalid_provider_response_is_safely_wrapped(payload: Mapping[str, object]) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps(payload).encode()

    judge = OpenAICompatibleTimeRangeJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "查询近三年年度行情" not in str(error.value)
