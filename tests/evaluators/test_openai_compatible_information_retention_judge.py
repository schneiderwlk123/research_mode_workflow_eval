import json
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from evaluators.information_retention import InformationRetentionJudgeRequest
from evaluators.openai_compatible_information_retention_judge import (
    OpenAICompatibleInformationRetentionJudge,
)
from evaluators.openai_compatible_parameter_judge import JudgeSettings


def _request() -> InformationRetentionJudgeRequest:
    return InformationRetentionJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析收入",
        answer="收入为 100 亿元。",
        evidence_units=(EvidenceUnit("call-1:0", "search", '{"revenue":100}'),),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )


def test_judge_sends_final_output_and_evidence_as_untrusted_data() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(url=url, body=json.loads(body), authorization=headers["Authorization"])
        content = {
            "facts": [
                {
                    "fact_id": "f1",
                    "fact_type": "required",
                    "verdict": "retained",
                    "reason_code": "explicitly_retained",
                    "source_unit_ids": ["call-1:0"],
                    "confidence": 0.95,
                }
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleInformationRetentionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    response = judge.judge(_request())

    assert response["facts"]
    body = captured["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert isinstance(messages, list)
    user_payload = json.loads(messages[1]["content"])
    assert user_payload["query"] == "分析收入"
    assert user_payload["answer"] == "收入为 100 亿元。"
    assert user_payload["evidence_units"][0]["content"] == {"revenue": 100}
    assert captured["authorization"] == "Bearer secret-value"


def test_provider_failure_is_wrapped_without_sensitive_content() -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps({"error": {"message": "收入为 100 亿元"}}).encode()

    judge = OpenAICompatibleInformationRetentionJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "收入为 100 亿元" not in str(error.value)
