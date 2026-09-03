import json
from collections.abc import Mapping
from typing import Any, cast

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.metric_scope_accuracy import (
    MetricRequirement,
    MetricRequirementExtractionRequest,
    MetricScopeAssessmentRequest,
)
from evaluators.openai_compatible_metric_scope_judge import OpenAICompatibleMetricScopeJudge
from evaluators.openai_compatible_parameter_judge import JudgeSettings


def test_requirement_extraction_sends_query_without_tool_evidence() -> None:
    captured = {}

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        captured.update(json.loads(body))
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "not_assessable",
                                    "reason_code": "no_metric_requirement",
                                    "requirements": [],
                                    "confidence": 1.0,
                                }
                            )
                        }
                    }
                ]
            }
        ).encode()

    judge = OpenAICompatibleMetricScopeJudge(
        JudgeSettings("https://example.test/v1", "secret", "model"), transport=transport
    )
    judge.extract_requirements(MetricRequirementExtractionRequest("t1", "chat", "查询公司情况"))
    messages = cast(Any, captured["messages"])
    message_content = cast(str, messages[1]["content"])
    payload = cast(Any, json.loads(message_content))
    assert set(payload) == {"query"}
    assert "tool_calls" not in payload


def test_assessment_sends_frozen_requirements_without_original_query() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
        del url, headers, timeout
        captured.update(json.loads(body))
        content = {"fields": [], "unexpected_field_count": 0, "confidence": 1.0}
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleMetricScopeJudge(
        JudgeSettings("https://example.test/v1", "secret", "model"), transport=transport
    )
    call = ParameterValueCandidate(
        "c1",
        "不得发送的原始 query",
        "finance",
        "查询财务指标。",
        {"metrics": "指标列表。"},
        frozenset({"metrics"}),
        {"metrics": ["营业收入"], "options": {"unit": "百万元"}},
    )
    judge.assess(
        MetricScopeAssessmentRequest(
            "t1",
            "chat",
            (MetricRequirement("m1", "营业收入", {"unit": "百万元"}),),
            (call,),
            "0.1.0",
        )
    )
    messages = cast(Any, captured["messages"])
    message_content = cast(str, messages[1]["content"])
    payload = cast(Any, json.loads(message_content))
    assert set(payload) == {"requirements", "tool_calls"}
    assert payload["tool_calls"][0]["arguments"]["options"]["unit"] == "百万元"
    assert "不得发送的原始 query" not in message_content
