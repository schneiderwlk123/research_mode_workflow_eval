import json
from collections.abc import Mapping

import pytest

from collectors.routing import build_capability_catalog
from evaluators.openai_compatible_parameter_judge import JudgeSettings
from evaluators.openai_compatible_routing_judge import OpenAICompatibleRoutingJudge
from evaluators.routing import ReusableIntentRouteMapping, RoutingJudgeRequest


def test_routing_judge_sends_intents_schema_and_reusable_mappings() -> None:
    captured: dict[str, object] = {}

    def transport(
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        content = {
            "subtasks": [
                {
                    "intent_signature": ["market_data"],
                    "subtask_type": "quote_lookup",
                    "required_capability_ids": ["quote"],
                }
            ],
            "required_capability_ids": ["quote"],
        }
        if method == "POST" and url.endswith("/files"):
            assert body is not None
            text = body.decode("utf-8")
            request = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
            captured.update(
                url=url,
                body=request["body"],
                authorization=headers["Authorization"],
            )
            return b'{"id":"file-batch-input"}'
        if method == "POST" and url.endswith("/batches"):
            return b'{"id":"batch-1","status":"validating"}'
        if method == "GET" and url.endswith("/batches/batch-1"):
            return (
                b'{"id":"batch-1","status":"completed",'
                b'"output_file_id":"file-batch_output-1","error_file_id":null}'
            )
        if method == "GET" and url.endswith("/files/file-batch_output-1/content"):
            result = {
                "custom_id": "request-1",
                "response": {
                    "status_code": 200,
                    "body": {"choices": [{"message": {"content": json.dumps(content)}}]},
                },
                "error": None,
            }
            return (json.dumps(result) + "\n").encode()
        if method == "DELETE" and "/files/" in url:
            return b'{"deleted":true}'
        raise AssertionError(f"unexpected request: {method} {url}")

    catalog = build_capability_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {
                    "name": "quote",
                    "description": "查询行情。",
                    "parameters": [
                        {
                            "name": "query",
                            "type": "string",
                            "required": True,
                            "description": "证券名称。",
                        }
                    ],
                }
            ],
        }
    )
    request = RoutingJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        intent_fragments=("查询某公司的行情", "核对价格"),
        capabilities=tuple(catalog.capabilities.values()),
        reusable_mappings=(
            ReusableIntentRouteMapping(
                intent_signature=("market_data",),
                subtask_type="quote_lookup",
                capability_ids=("quote",),
                schema_version="0.1.0",
                judge_name="old",
                judge_version="old-model",
                prompt_version="0.9.0",
            ),
        ),
    )
    judge = OpenAICompatibleRoutingJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        sleep=lambda _: None,
    )

    response = judge.judge(request)

    assert response["required_capability_ids"] == ["quote"]
    assert captured["url"] == "https://example.test/v1/files"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["enable_thinking"] is False
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["intent_fragments"] == ["查询某公司的行情", "核对价格"]
    assert user_payload["capabilities"][0]["capability_id"] == "quote"
    assert user_payload["reusable_mappings"][0]["intent_signature"] == ["market_data"]


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
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        del body, headers, timeout_seconds
        if method == "POST" and url.endswith("/files"):
            return b'{"id":"file-batch-input"}'
        if method == "POST" and url.endswith("/batches"):
            return b'{"id":"batch-1","status":"validating"}'
        if method == "GET" and url.endswith("/batches/batch-1"):
            return (
                b'{"id":"batch-1","status":"completed",'
                b'"output_file_id":"file-batch_output-1","error_file_id":null}'
            )
        if method == "GET" and url.endswith("/files/file-batch_output-1/content"):
            result = {
                "custom_id": "request-1",
                "response": {"status_code": 200, "body": payload},
                "error": None,
            }
            return (json.dumps(result) + "\n").encode()
        if method == "DELETE" and "/files/" in url:
            return b'{"deleted":true}'
        raise AssertionError(f"unexpected request: {method} {url}")

    judge = OpenAICompatibleRoutingJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        sleep=lambda _: None,
    )
    request = RoutingJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        intent_fragments=("敏感用户意图",),
        capabilities=(),
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(request)

    assert "secret-value" not in str(error.value)
    assert "敏感用户意图" not in str(error.value)
