import json
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from evaluators.evidence_precision import EvidenceRelevanceJudgeRequest
from evaluators.openai_compatible_evidence_judge import OpenAICompatibleEvidenceRelevanceJudge
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
    assert body["response_format"] == {"type": "json_object"}
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
