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
            "verdict": "correct",
            "reason_code": "material_intents_aligned",
            "confidence": 0.95,
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleIntentRecognitionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.judge(_request())

    assert response["verdict"] == "correct"
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["query"] == "分析某公司的行情"
    assert user_payload["evidence_fragments"][0]["kind"] == "subtask_query"
    assert user_payload["capabilities"][0]["capability_id"] == "quote"


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
