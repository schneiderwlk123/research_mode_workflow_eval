import json
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from evaluators.numeric_consistency import NumericConsistencyJudgeRequest
from evaluators.openai_compatible_numeric_consistency_judge import (
    OpenAICompatibleNumericConsistencyJudge,
)
from evaluators.openai_compatible_parameter_judge import JudgeSettings


def _request() -> NumericConsistencyJudgeRequest:
    return NumericConsistencyJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析收入",
        answer="收入为 123.5 亿元。",
        evidence_units=(EvidenceUnit("call-1:0", "search", '{"revenue":123.46}'),),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )


def test_judge_sends_untrusted_answer_and_evidence_with_deterministic_settings() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(url=url, body=json.loads(body), authorization=headers["Authorization"])
        return json.dumps({"choices": [{"message": {"content": '{"claims":[]}'}}]}).encode()

    judge = OpenAICompatibleNumericConsistencyJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    assert judge.judge(_request()) == {"claims": []}
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    payload = json.loads(body["messages"][1]["content"])
    assert payload["answer"] == "收入为 123.5 亿元。"
    assert payload["evidence_units"][0]["content"] == {"revenue": 123.46}
    assert captured["authorization"] == "Bearer secret-value"


def test_provider_failure_does_not_expose_sensitive_content() -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps({"error": {"message": "收入为 123.5 亿元"}}).encode()

    judge = OpenAICompatibleNumericConsistencyJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "123.5" not in str(error.value)
