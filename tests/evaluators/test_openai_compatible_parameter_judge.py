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
            "statementType": "财务报表类型。",
        },
        schema_field_paths=frozenset({"querys", "reportDates", "searchData", "statementType"}),
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
        request_body = json.loads(body)
        user_payload = json.loads(request_body["messages"][1]["content"])
        actual_fields = user_payload["actual_fields"]
        field_ids = {field["field_path"]: field["field_id"] for field in actual_fields}
        omitted_fields = user_payload["omitted_schema_fields"]
        omitted_ids = {field["field_path"]: field["field_id"] for field in omitted_fields}
        captured.update(
            url=url,
            body=request_body,
            authorization=headers["Authorization"],
            timeout_seconds=timeout_seconds,
        )
        content = {
            field_ids["querys"]: "correct",
            field_ids["reportDates"]: "correct",
            field_ids["searchData"]: "correct",
            "missing_required_field_ids": [omitted_ids["statementType"]],
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
    response_properties = json_schema["schema"]["properties"]
    assert set(response_properties) == {
        "field_0001",
        "field_0002",
        "field_0003",
        "missing_required_field_ids",
    }
    assert response_properties["missing_required_field_ids"]["items"]["enum"] == ["omitted_0001"]
    user_payload = json.loads(body["messages"][1]["content"])
    assert "arguments" not in user_payload
    assert [field["field_path"] for field in user_payload["actual_fields"]] == [
        "querys",
        "reportDates",
        "searchData",
    ]
    assert user_payload["actual_fields"][1]["actual_value"] == [
        {"fiscalYear": 2025, "reportType": "A"}
    ]
    assert [field["field_path"] for field in user_payload["omitted_schema_fields"]] == [
        "statementType"
    ]
    assert response["fields"][0]["field_path"] == "querys"  # type: ignore[index]
    assert response["fields"][-1] == {  # type: ignore[index]
        "field_path": "statementType",
        "verdict": "incorrect",
        "reason_code": "missing_query_required",
        "confidence": 1.0,
    }


@pytest.mark.parametrize(
    "content",
    [
        {
            "arguments.querys": "correct",
            "missing_required_field_ids": [],
        },
        {
            "field_0001": "correct",
            "field_0002": "correct",
            "missing_required_field_ids": [],
        },
    ],
)
def test_judge_rejects_unknown_or_missing_field_ids(content: dict[str, object]) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleParameterValueJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="invalid_judge_output"):
        judge.judge(_request())


@pytest.mark.parametrize("missing_value", [None, "absent"])
def test_judge_treats_missing_empty_omission_list_as_empty(missing_value: object) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, headers, timeout_seconds
        request_body = json.loads(body)
        user_payload = json.loads(request_body["messages"][1]["content"])
        content: dict[str, object] = {
            field["field_id"]: "correct" for field in user_payload["actual_fields"]
        }
        if missing_value != "absent":
            content["missing_required_field_ids"] = missing_value
        return json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode()

    judge = OpenAICompatibleParameterValueJudge(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    response = judge.judge(_request())

    assert len(response["fields"]) == 3  # type: ignore[arg-type]


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
