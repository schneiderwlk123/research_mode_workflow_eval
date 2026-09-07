import json
from collections.abc import Mapping

import pytest

from collectors.evidence_quality import EvidenceUnit
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError
from evaluators.openai_compatible_parameter_judge import JudgeSettings
from evaluators.openai_compatible_statement_support_judge import (
    OpenAICompatibleStatementSupportJudge,
)
from evaluators.statement_support import StatementSupportJudgeRequest


def _request() -> StatementSupportJudgeRequest:
    return StatementSupportJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析收入",
        answer="收入为 100 亿元。",
        evidence_units=(EvidenceUnit("call-1:0", "search", '{"revenue":100}'),),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )


def test_statement_support_judge_sends_answer_and_evidence_as_untrusted_data() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(url=url, body=json.loads(body), authorization=headers["Authorization"])
        content = {
            "claims": [
                {
                    "claim_id": "c1",
                    "claim_type": "verifiable",
                    "verdict": "supported",
                    "reason_code": "directly_supported",
                    "supporting_unit_ids": ["call-1:0"],
                    "confidence": 0.95,
                }
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleStatementSupportJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    response = judge.judge(_request())

    assert response["claims"]
    body = captured["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert isinstance(messages, list)
    user_payload = json.loads(messages[1]["content"])
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
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

    judge = OpenAICompatibleStatementSupportJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "收入为 100 亿元" not in str(error.value)


def test_oversized_statement_request_fails_before_transport() -> None:
    calls = 0

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        nonlocal calls
        calls += 1
        raise AssertionError("超大请求不应发送到供应商")

    judge = OpenAICompatibleStatementSupportJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_request_bytes=256,
    )
    request = StatementSupportJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析收入",
        answer="收入为 100 亿元。" * 100,
        evidence_units=(EvidenceUnit("call-1:0", "search", '{"revenue":100}'),),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )

    with pytest.raises(JudgeExecutionError) as error:
        judge.judge(request)

    assert error.value.reason == "judge_input_too_large"
    assert error.value.safe_metadata["judge_attempt_count"] == 0
    assert calls == 0
