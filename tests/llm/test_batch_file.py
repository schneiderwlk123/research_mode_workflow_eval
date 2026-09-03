import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from llm.batch_file import (
    BatchFileSettings,
    load_batch_file_settings,
    request_json_completion,
)


def test_load_settings_prefers_batch_file_names(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "BATCH_FILE_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
        "BATCH_FILE_API_KEY=batch-secret\n"
        "BATCH_FILE_MODEL=qwen-plus\n"
        "BATCH_FILE_COMPLETION_WINDOW=24h\n"
        "BATCH_FILE_POLL_INTERVAL_SECONDS=60\n"
        "BATCH_FILE_TIMEOUT_SECONDS=86400\n"
        "BATCH_FILE_REQUEST_TIMEOUT_SECONDS=90\n"
        "BATCH_FILE_MAX_RETRIES=2\n"
        "BATCH_FILE_ENABLE_THINKING=false\n",
        encoding="utf-8",
    )

    settings = load_batch_file_settings(env_path, environ={})

    assert settings.api_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.api_key == "batch-secret"
    assert settings.model == "qwen-plus"
    assert settings.completion_window == "24h"
    assert settings.poll_interval_seconds == 60
    assert settings.timeout_seconds == 86400
    assert settings.request_timeout_seconds == 90
    assert settings.max_retries == 2
    assert settings.enable_thinking is False
    assert "batch-secret" not in repr(settings)


def test_request_uses_in_memory_jsonl_and_downloads_completed_result() -> None:
    calls: list[dict[str, object]] = []
    retrieve_count = 0

    def transport(
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        nonlocal retrieve_count
        calls.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        if method == "POST" and url.endswith("/files"):
            assert body is not None
            text = body.decode("utf-8")
            jsonl_line = next(line for line in text.splitlines() if line.startswith("{"))
            request = json.loads(jsonl_line)
            assert request["custom_id"] == "request-1"
            assert request["url"] == "/v1/chat/completions"
            assert request["body"]["enable_thinking"] is False
            assert request["body"]["response_format"] == {"type": "json_object"}
            assert 'purpose"\r\n\r\nbatch' in text
            return b'{"id":"file-batch-input"}'
        if method == "POST" and url.endswith("/batches"):
            assert body is not None
            payload = json.loads(body)
            assert payload == {
                "input_file_id": "file-batch-input",
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            }
            return b'{"id":"batch-1","status":"validating"}'
        if method == "GET" and url.endswith("/batches/batch-1"):
            retrieve_count += 1
            if retrieve_count == 1:
                return b'{"id":"batch-1","status":"in_progress"}'
            return (
                b'{"id":"batch-1","status":"completed",'
                b'"output_file_id":"file-batch_output-1","error_file_id":null}'
            )
        if method == "GET" and url.endswith("/files/file-batch_output-1/content"):
            return (
                b'{"custom_id":"request-1","response":{"status_code":200,"body":'
                b'{"choices":[{"message":{"content":"{\\"result\\":\\"ok\\"}"}}]}},'
                b'"error":null}\n'
            )
        if method == "DELETE" and "/files/" in url:
            return b'{"deleted":true}'
        raise AssertionError(f"unexpected request: {method} {url}")

    sleeps: list[float] = []
    response = request_json_completion(
        BatchFileSettings(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "secret-value",
            "qwen-plus",
            poll_interval_seconds=60,
            timeout_seconds=86400,
        ),
        system_prompt="system",
        user_payload={"input": "value"},
        max_tokens=512,
        transport=transport,
        sleep=sleeps.append,
    )

    assert response == {"result": "ok"}
    assert sleeps == [60]
    assert any(call["method"] == "DELETE" for call in calls)


def test_row_failure_is_reported_without_provider_content() -> None:
    def transport(
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
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
            return (
                b'{"custom_id":"request-1","response":null,'
                b'"error":{"code":"BadRequest","message":"sensitive provider detail"}}\n'
            )
        if method == "DELETE" and "/files/" in url:
            return b'{"deleted":true}'
        raise AssertionError(f"unexpected request: {method} {url}")

    with pytest.raises(RuntimeError, match="Batch File 请求失败") as captured:
        request_json_completion(
            BatchFileSettings(
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "secret-value",
                "qwen-plus",
            ),
            system_prompt="system",
            user_payload={"secret": "must-not-leak"},
            max_tokens=512,
            transport=transport,
            sleep=lambda _: None,
        )

    assert "sensitive provider detail" not in str(captured.value)
    assert "must-not-leak" not in str(captured.value)


def test_upload_failure_is_not_retried_to_avoid_duplicate_files() -> None:
    upload_count = 0

    def transport(
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        nonlocal upload_count
        del body, headers, timeout_seconds
        if method == "POST" and url.endswith("/files"):
            upload_count += 1
            raise TimeoutError("response lost")
        raise AssertionError(f"unexpected request: {method} {url}")

    with pytest.raises(RuntimeError, match="Batch File 请求失败"):
        request_json_completion(
            BatchFileSettings(
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "secret-value",
                "qwen-plus",
                max_retries=2,
            ),
            system_prompt="system",
            user_payload={"input": "value"},
            max_tokens=512,
            transport=transport,
            sleep=lambda _: None,
        )

    assert upload_count == 1
