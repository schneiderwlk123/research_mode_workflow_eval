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
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(url=url, body=json.loads(body), authorization=headers["Authorization"])
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
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

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
    )

    response = judge.judge(request)

    assert response["required_capability_ids"] == ["quote"]
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
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
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps(payload).encode()

    judge = OpenAICompatibleRoutingJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
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
