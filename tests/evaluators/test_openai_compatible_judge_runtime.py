import json
from collections.abc import Mapping
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from evaluators.openai_compatible_judge_runtime import (
    JudgeExecutionError,
    OpenAICompatibleJudgeClient,
)
from evaluators.openai_compatible_parameter_judge import JudgeSettings

_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}},
    "required": ["verdict"],
    "additionalProperties": False,
}


def _response(content: object, *, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": json.dumps(content)},
                }
            ]
        }
    ).encode()


def test_client_requests_strict_json_schema() -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        captured.update(url=url, body=json.loads(body), authorization=headers["Authorization"])
        return _response({"verdict": "ok"})

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_attempts=1,
    )

    result = client.request(
        system_prompt="system",
        user_payload={"input": "untrusted"},
        schema_name="test_verdict",
        response_schema=_SCHEMA,
        max_tokens=64,
    )

    assert result == {"verdict": "ok"}
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "test_verdict", "strict": True, "schema": _SCHEMA},
    }
    assert captured["authorization"] == "Bearer secret-value"


def test_client_rejects_oversized_request_without_calling_transport() -> None:
    calls = 0

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        nonlocal calls
        calls += 1
        return _response({"verdict": "ok"})

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_request_bytes=128,
    )

    with pytest.raises(JudgeExecutionError) as error:
        client.request(
            system_prompt="system",
            user_payload={"input": "x" * 500},
            schema_name="test_verdict",
            response_schema=_SCHEMA,
            max_tokens=64,
        )

    assert error.value.reason == "judge_input_too_large"
    request_byte_count = error.value.safe_metadata["request_byte_count"]
    assert isinstance(request_byte_count, int)
    assert request_byte_count > 128
    assert calls == 0
    assert "secret-value" not in str(error.value)


def test_client_retries_timeout_then_succeeds() -> None:
    calls = 0
    delays: list[float] = []

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("sensitive input")
        return _response({"verdict": "ok"})

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_attempts=2,
        retry_backoff_seconds=0.25,
        sleeper=delays.append,
    )

    result = client.request(
        system_prompt="system",
        user_payload={"input": "untrusted"},
        schema_name="test_verdict",
        response_schema=_SCHEMA,
        max_tokens=64,
    )

    assert result == {"verdict": "ok"}
    assert calls == 2
    assert delays == [0.25]


def test_client_reports_truncated_output_without_exposing_content() -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        return _response({"sensitive": "content"}, finish_reason="length")

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
    )

    with pytest.raises(JudgeExecutionError) as error:
        client.request(
            system_prompt="system",
            user_payload={"input": "untrusted"},
            schema_name="test_verdict",
            response_schema=_SCHEMA,
            max_tokens=64,
        )

    assert error.value.reason == "judge_output_truncated"
    assert "sensitive" not in str(error.value)


def test_client_falls_back_when_provider_rejects_json_schema() -> None:
    bodies: list[dict[str, object]] = []

    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        bodies.append(json.loads(body))
        if len(bodies) == 1:
            raise HTTPError(url, 400, "unsupported response format", Message(), None)
        return _response({"verdict": "ok"})

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_attempts=1,
    )

    result = client.request(
        system_prompt="system",
        user_payload={"input": "untrusted"},
        schema_name="test_verdict",
        response_schema=_SCHEMA,
        max_tokens=64,
    )

    assert result == {"verdict": "ok"}
    assert bodies[0]["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert bodies[1]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    ("failure", "expected_reason", "expected_status"),
    [
        (
            HTTPError("https://example.test", 429, "rate limited", Message(), None),
            "judge_rate_limited",
            429,
        ),
        (
            HTTPError("https://example.test", 503, "unavailable", Message(), None),
            "judge_http_5xx",
            503,
        ),
        (
            HTTPError("https://example.test", 401, "unauthorized", Message(), None),
            "judge_http_error",
            401,
        ),
        (URLError("offline"), "judge_connection_error", None),
        (URLError(TimeoutError("slow")), "judge_timeout", None),
    ],
)
def test_client_classifies_terminal_transport_failures(
    failure: Exception, expected_reason: str, expected_status: int | None
) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        raise failure

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_attempts=1,
    )

    with pytest.raises(JudgeExecutionError) as error:
        client.request(
            system_prompt="system",
            user_payload={"input": "untrusted"},
            schema_name="test_verdict",
            response_schema=_SCHEMA,
            max_tokens=64,
        )

    assert error.value.reason == expected_reason
    assert error.value.safe_metadata.get("http_status") == expected_status
    assert error.value.safe_metadata["judge_attempt_count"] == 1


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        (b"not-json", "judge_invalid_json"),
        (json.dumps({"error": {"message": "sensitive"}}).encode(), "judge_invalid_response"),
        (json.dumps({"choices": []}).encode(), "judge_empty_response"),
        (
            json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode(),
            "judge_invalid_json",
        ),
    ],
)
def test_client_classifies_invalid_provider_responses(payload: bytes, expected_reason: str) -> None:
    def transport(
        url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float
    ) -> bytes:
        del url, body, headers, timeout_seconds
        return payload

    client = OpenAICompatibleJudgeClient(
        JudgeSettings("https://example.test/v1", "secret-value", "test-model"),
        transport=transport,
        max_attempts=1,
    )

    with pytest.raises(JudgeExecutionError) as error:
        client.request(
            system_prompt="system",
            user_payload={"input": "untrusted"},
            schema_name="test_verdict",
            response_schema=_SCHEMA,
            max_tokens=64,
        )

    assert error.value.reason == expected_reason
    assert "sensitive" not in str(error.value)
