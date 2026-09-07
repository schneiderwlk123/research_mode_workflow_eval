import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from collectors.parameter_value_accuracy import ParameterValueCandidate
from evaluators.openai_compatible_parameter_judge import (
    JudgeSettings,
    OpenAICompatibleParameterValueJudge,
    load_judge_settings,
)
from evaluators.parameter_value_accuracy import ParameterValueJudgeRequest


def _request() -> ParameterValueJudgeRequest:
    candidate = ParameterValueCandidate(
        call_id="call-1",
        query="查询贵州茅台 2025 年营业收入",
        tool_name="company_finance_search",
        tool_description="查询上市公司财务数据。",
        parameter_descriptions={
            "querys": "公司名称或股票代码。",
            "reportDates": "财报报告期。",
            "searchData": "查询指标。",
        },
        schema_field_paths=frozenset({"querys", "reportDates", "searchData"}),
        arguments={
            "querys": ["贵州茅台"],
            "reportDates": [{"fiscalYear": 2025, "reportType": "A"}],
            "searchData": ["营业收入"],
        },
    )
    return ParameterValueJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        call_id=candidate.call_id,
        query=candidate.query,
        tool_name=candidate.tool_name,
        tool_description=candidate.tool_description,
        parameter_descriptions=candidate.parameter_descriptions,
        arguments=candidate.arguments,
    )


def test_load_judge_settings_uses_common_dotenv_names(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "JUDGE_API_URL=https://example.test/compatible-mode/v1\n"
        "JUDGE_API_KEY=secret-value\n"
        "JUDGE_MODEL=test-model\n",
        encoding="utf-8",
    )

    settings = load_judge_settings(env_path)

    assert settings.api_url == "https://example.test/compatible-mode/v1"
    assert settings.api_key == "secret-value"
    assert settings.model == "test-model"
    assert "secret-value" not in repr(settings)


def test_remote_judge_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        JudgeSettings("http://example.test/v1", "secret-value", "test-model")


def test_judge_sends_json_mode_request_and_parses_field_verdicts() -> None:
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
            "fields": [
                {
                    "field_path": "querys",
                    "verdict": "correct",
                    "reason_code": "direct_match",
                    "confidence": 1.0,
                },
                {
                    "field_path": "reportDates",
                    "verdict": "correct",
                    "reason_code": "normalized_equivalent",
                    "confidence": 0.98,
                },
                {
                    "field_path": "searchData",
                    "verdict": "correct",
                    "reason_code": "direct_match",
                    "confidence": 1.0,
                },
            ]
        }
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleParameterValueJudge(
        JudgeSettings(
            api_url="https://example.test/compatible-mode/v1",
            api_key="secret-value",
            model="test-model",
        ),
        transport=transport,
        timeout_seconds=12,
    )

    response = judge.judge(_request())

    assert captured["url"] == "https://example.test/compatible-mode/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    assert captured["timeout_seconds"] == 12
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    assert json_schema["strict"] is True
    assert response["fields"][0]["field_path"] == "querys"  # type: ignore[index]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not-json"}}]},
        {"error": {"message": "provider failure"}},
    ],
)
def test_invalid_provider_response_raises_without_echoing_payload(
    payload: Mapping[str, object],
) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps(payload).encode()

    judge = OpenAICompatibleParameterValueJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError) as error:
        judge.judge(_request())

    assert "secret-value" not in str(error.value)
    assert "贵州茅台" not in str(error.value)
