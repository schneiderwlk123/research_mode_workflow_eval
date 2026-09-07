"""OpenAI-compatible Judge 的共享请求、重试与安全错误分类。"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

JudgeTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]
Sleeper = Callable[[float], None]


class JudgeConnectionSettings(Protocol):
    @property
    def api_url(self) -> str: ...

    @property
    def api_key(self) -> str: ...

    @property
    def model(self) -> str: ...


@dataclass(frozen=True, slots=True)
class JudgeExecutionError(RuntimeError):
    """只携带稳定原因码和安全计数，不包含请求或响应正文。"""

    reason: str
    safe_metadata: Mapping[str, int | str]

    def __str__(self) -> str:
        return self.reason


class OpenAICompatibleJudgeClient:
    """统一执行严格结构化 Judge 请求，并隔离供应商差异。"""

    def __init__(
        self,
        settings: JudgeConnectionSettings,
        *,
        transport: JudgeTransport | None = None,
        timeout_seconds: float = 90,
        max_request_bytes: int = 512 * 1024,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 1.0,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于零")
        if max_request_bytes <= 0:
            raise ValueError("max_request_bytes 必须大于零")
        if max_attempts <= 0:
            raise ValueError("max_attempts 必须大于零")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds 不能小于零")
        self._settings = settings
        self._transport = transport or _post_json
        self._timeout_seconds = timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleeper = sleeper

    def request(
        self,
        *,
        system_prompt: str,
        user_payload: Mapping[str, object],
        schema_name: str,
        response_schema: Mapping[str, object],
        max_tokens: int,
    ) -> Mapping[str, object]:
        """优先使用严格 Schema；供应商明确拒绝时回退到 JSON 对象模式。"""

        strict_format: Mapping[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": response_schema,
            },
        }
        try:
            return self._request_with_format(
                system_prompt=system_prompt,
                user_payload=user_payload,
                response_format=strict_format,
                max_tokens=max_tokens,
            )
        except urllib.error.HTTPError:
            # 严格结构输出被供应商以 400 拒绝时，兼容回退到 JSON 对象模式。
            pass
        return self._request_with_format(
            system_prompt=system_prompt,
            user_payload=user_payload,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    def _request_with_format(
        self,
        *,
        system_prompt: str,
        user_payload: Mapping[str, object],
        response_format: Mapping[str, object],
        max_tokens: int,
    ) -> Mapping[str, object]:
        payload = {
            "model": self._settings.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_size = len(body)
        if request_size > self._max_request_bytes:
            raise JudgeExecutionError(
                "judge_input_too_large",
                {
                    "request_byte_count": request_size,
                    "max_request_bytes": self._max_request_bytes,
                    "judge_attempt_count": 0,
                },
            )
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = _chat_completions_endpoint(self._settings.api_url)
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw_response = self._transport(
                    endpoint,
                    body,
                    headers,
                    self._timeout_seconds,
                )
                return _parse_response(
                    raw_response,
                    request_byte_count=request_size,
                    attempt_count=attempt,
                )
            except urllib.error.HTTPError as error:
                if error.code == 400 and response_format.get("type") == "json_schema":
                    raise
                classified = self._http_error(
                    error,
                    request_size=request_size,
                    attempt_count=attempt,
                )
            except TimeoutError:
                classified = self._error(
                    "judge_timeout", request_size=request_size, attempt_count=attempt
                )
            except urllib.error.URLError as error:
                reason = (
                    "judge_timeout"
                    if isinstance(error.reason, TimeoutError)
                    else "judge_connection_error"
                )
                classified = self._error(reason, request_size=request_size, attempt_count=attempt)
            except JudgeExecutionError:
                raise
            except Exception:
                raise self._error(
                    "judge_transport_error",
                    request_size=request_size,
                    attempt_count=attempt,
                ) from None
            if (
                classified.reason
                not in {
                    "judge_timeout",
                    "judge_rate_limited",
                    "judge_http_5xx",
                    "judge_connection_error",
                }
                or attempt == self._max_attempts
            ):
                raise classified
            self._sleeper(self._retry_backoff_seconds * (2 ** (attempt - 1)))
        raise self._error(
            "judge_retry_exhausted",
            request_size=request_size,
            attempt_count=self._max_attempts,
        )

    def _http_error(
        self,
        error: urllib.error.HTTPError,
        *,
        request_size: int,
        attempt_count: int,
    ) -> JudgeExecutionError:
        if error.code == 429:
            reason = "judge_rate_limited"
        elif 500 <= error.code < 600:
            reason = "judge_http_5xx"
        else:
            reason = "judge_http_error"
        return JudgeExecutionError(
            reason,
            {
                "http_status": error.code,
                "request_byte_count": request_size,
                "judge_attempt_count": attempt_count,
            },
        )

    @staticmethod
    def _error(reason: str, *, request_size: int, attempt_count: int) -> JudgeExecutionError:
        return JudgeExecutionError(
            reason,
            {
                "request_byte_count": request_size,
                "judge_attempt_count": attempt_count,
            },
        )


def judge_error_metadata(error: Exception) -> tuple[str, dict[str, int | str]]:
    """把共享运行时异常转换为 evaluator 可持久化的安全诊断。"""

    if isinstance(error, JudgeExecutionError):
        return error.reason, dict(error.safe_metadata)
    return "judge_failure", {}


def _parse_response(
    raw_response: bytes,
    *,
    request_byte_count: int,
    attempt_count: int,
) -> Mapping[str, object]:
    metadata = {
        "request_byte_count": request_byte_count,
        "response_byte_count": len(raw_response),
        "judge_attempt_count": attempt_count,
    }
    try:
        envelope = json.loads(raw_response)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JudgeExecutionError("judge_invalid_json", metadata) from None
    if not isinstance(envelope, Mapping) or envelope.get("error") is not None:
        raise JudgeExecutionError("judge_invalid_response", metadata)
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise JudgeExecutionError("judge_empty_response", metadata)
    first = choices[0]
    finish_reason = first.get("finish_reason")
    if finish_reason == "length":
        raise JudgeExecutionError("judge_output_truncated", metadata)
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise JudgeExecutionError("judge_empty_response", metadata)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise JudgeExecutionError("judge_empty_response", metadata)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise JudgeExecutionError("judge_invalid_json", metadata) from None
    if not isinstance(parsed, Mapping):
        raise JudgeExecutionError("judge_invalid_json", metadata)
    return cast(Mapping[str, object], parsed)


def _post_json(url: str, body: bytes, headers: Mapping[str, str], timeout_seconds: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return cast(bytes, response.read())


def _chat_completions_endpoint(api_url: str) -> str:
    normalized = api_url.strip().rstrip("/")
    return (
        normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"
    )
