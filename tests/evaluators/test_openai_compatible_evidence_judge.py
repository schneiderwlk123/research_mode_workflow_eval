import json
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from evaluators.evidence_precision import EvidenceRelevanceJudgeRequest
from evaluators.openai_compatible_evidence_judge import OpenAICompatibleEvidenceRelevanceJudge
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError
from evaluators.openai_compatible_parameter_judge import JudgeSettings


def _request() -> EvidenceRelevanceJudgeRequest:
    return EvidenceRelevanceJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析公司收入",
        evidence_units=(EvidenceUnit("call-1:0", "search", '{"revenue":10}'),),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )


def test_evidence_judge_sends_units_and_parses_structured_response() -> None:
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
            "judgments": [
                {
                    "unit_id": "call-1:0",
                    "verdict": "relevant",
                    "reason_code": "supports_required_fact",
                    "confidence": 0.95,
                }
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleEvidenceRelevanceJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.judge(_request())

    judgments = response["judgments"]
    assert isinstance(judgments, list)
    first_judgment = judgments[0]
    assert isinstance(first_judgment, dict)
    assert first_judgment["verdict"] == "relevant"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    messages = body["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    content = user_message["content"]
    assert isinstance(content, str)
    user_payload = json.loads(content)
    assert user_payload["query"] == "分析公司收入"
    assert user_payload["evidence_units"] == [
        {
            "unit_id": "call-1:0",
            "tool_name": "search",
            "content": {"revenue": 10},
        }
    ]


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

    judge = OpenAICompatibleEvidenceRelevanceJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "分析公司收入" not in str(error.value)


def test_evidence_judge_batches_all_units_without_dropping_any() -> None:
    observed_batches: list[list[str]] = []

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, headers, timeout_seconds
        payload = json.loads(body)
        user_payload = json.loads(payload["messages"][1]["content"])
        unit_ids = [unit["unit_id"] for unit in user_payload["evidence_units"]]
        observed_batches.append(unit_ids)
        content = {
            "judgments": [
                {
                    "unit_id": unit_id,
                    "verdict": "relevant",
                    "reason_code": "supports_required_fact",
                    "confidence": 0.95,
                }
                for unit_id in unit_ids
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    request = EvidenceRelevanceJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析公司收入",
        evidence_units=tuple(
            EvidenceUnit(f"call-1:{index}", "search", f'{{"revenue":{index}}}')
            for index in range(5)
        ),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )
    judge = OpenAICompatibleEvidenceRelevanceJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        batch_size=2,
    )

    response = judge.judge(request)

    assert observed_batches == [["call-1:0", "call-1:1"], ["call-1:2", "call-1:3"], ["call-1:4"]]
    judgments = response["judgments"]
    assert isinstance(judgments, list)
    assert [item["unit_id"] for item in judgments] == [f"call-1:{index}" for index in range(5)]


def test_evidence_judge_discards_partial_batches_when_a_later_batch_fails() -> None:
    calls = 0

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, headers, timeout_seconds
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("sensitive evidence")
        payload = json.loads(body)
        user_payload = json.loads(payload["messages"][1]["content"])
        content = {
            "judgments": [
                {
                    "unit_id": unit["unit_id"],
                    "verdict": "relevant",
                    "reason_code": "supports_required_fact",
                    "confidence": 0.95,
                }
                for unit in user_payload["evidence_units"]
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    request = EvidenceRelevanceJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析公司收入",
        evidence_units=tuple(
            EvidenceUnit(f"call-1:{index}", "search", f'{{"revenue":{index}}}')
            for index in range(3)
        ),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )
    judge = OpenAICompatibleEvidenceRelevanceJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        batch_size=2,
        max_attempts=1,
    )

    with pytest.raises(JudgeExecutionError) as error:
        judge.judge(request)

    assert error.value.reason == "judge_timeout"
    assert calls == 2
    assert "sensitive evidence" not in str(error.value)
