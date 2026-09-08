import json
from collections.abc import Mapping

import pytest

from collectors.intent_recognition import IntentEvidenceFragment
from collectors.routing import CapabilityDefinition
from evaluators.intent_recognition import IntentRecognitionJudgeRequest
from evaluators.openai_compatible_intent_judge import OpenAICompatibleIntentRecognitionJudge
from evaluators.openai_compatible_parameter_judge import JudgeSettings


def _request() -> IntentRecognitionJudgeRequest:
    return IntentRecognitionJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析某公司的行情",
        evidence_fragments=(IntentEvidenceFragment("subtask_query", "查询该公司行情"),),
        capabilities=(CapabilityDefinition("quote", "查询证券行情。", ()),),
        schema_version="0.1.0",
    )


def test_intent_judge_sends_structured_request_and_parses_response() -> None:
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
            "assessment": "assessed",
            "primary_intent": "aligned",
            "intent_coverage": "complete",
            "planning_alignment": "aligned",
            "mcp_tool_rationality": "reasonable",
            "final_answer_alignment": "aligned",
            "reason_code": "all_material_intents_covered",
            "confidence": 0.95,
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleIntentRecognitionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.judge(_request())

    assert response["assessment"] == "assessed"
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
    schema = json_schema["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["query"] == "分析某公司的行情"
    assert user_payload["evidence_fragments"][0]["kind"] == "subtask_query"
    assert user_payload["capabilities"][0]["capability_id"] == "quote"
    assert "示例一" in body["messages"][0]["content"]


def test_validation_feedback_is_sent_without_echoing_invalid_output() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, headers, timeout_seconds
        captured.update(body=json.loads(body))
        content = {
            "assessment": "assessed",
            "primary_intent": "aligned",
            "intent_coverage": "complete",
            "planning_alignment": "aligned",
            "mcp_tool_rationality": "reasonable",
            "final_answer_alignment": "aligned",
            "reason_code": "all_material_intents_covered",
            "confidence": 1.0,
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleIntentRecognitionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )
    request = _request()
    request = IntentRecognitionJudgeRequest(
        trace_id=request.trace_id,
        mode=request.mode,
        query=request.query,
        evidence_fragments=request.evidence_fragments,
        capabilities=request.capabilities,
        schema_version=request.schema_version,
        validation_feedback="invalid_intent_judgment_contract",
    )

    judge.judge(request)

    body = captured["body"]
    assert isinstance(body, dict)
    payload = json.loads(body["messages"][1]["content"])
    assert payload["validation_feedback"] == "invalid_intent_judgment_contract"


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

    judge = OpenAICompatibleIntentRecognitionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "分析某公司的行情" not in str(error.value)
