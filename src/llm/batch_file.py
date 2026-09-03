"""阿里云百炼 OpenAI-compatible Batch File 公共客户端。"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

BatchFileTransport = Callable[[str, str, bytes | None, Mapping[str, str], float], bytes]
Sleep = Callable[[float], None]
Clock = Callable[[], float]

_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})


@dataclass(frozen=True, slots=True)
class BatchFileSettings:
    """Batch File 连接配置；密钥不会出现在对象表示中。"""

    api_url: str
    api_key: str = field(repr=False)
    model: str = ""
    completion_window: str = "24h"
    poll_interval_seconds: float = 60
    timeout_seconds: float = 86400
    request_timeout_seconds: float = 90
    max_retries: int = 2
    enable_thinking: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("BATCH_FILE_API_URL", self.api_url),
            ("BATCH_FILE_API_KEY", self.api_key),
            ("BATCH_FILE_MODEL", self.model),
        ):
            if not value.strip():
                raise ValueError(f"{name} 不能为空")
        parsed_url = urlparse(self.api_url)
        local_hosts = {"127.0.0.1", "::1", "localhost"}
        if parsed_url.scheme != "https" and not (
            parsed_url.scheme == "http" and parsed_url.hostname in local_hosts
        ):
            raise ValueError("远程 BATCH_FILE_API_URL 必须使用 HTTPS")
        if parsed_url.hostname == "batch.dashscope.aliyuncs.com":
            raise ValueError("Batch File 必须使用 compatible-mode 标准端点")
        _completion_window_hours(self.completion_window)
        _positive_number(self.poll_interval_seconds, "BATCH_FILE_POLL_INTERVAL_SECONDS")
        _positive_number(self.timeout_seconds, "BATCH_FILE_TIMEOUT_SECONDS")
        _positive_number(self.request_timeout_seconds, "BATCH_FILE_REQUEST_TIMEOUT_SECONDS")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 10
        ):
            raise ValueError("BATCH_FILE_MAX_RETRIES 必须位于 0 到 10")
        if not isinstance(self.enable_thinking, bool):
            raise ValueError("BATCH_FILE_ENABLE_THINKING 必须是布尔值")


def load_batch_file_settings(
    path: str | Path = ".env", *, environ: Mapping[str, str] | None = None
) -> BatchFileSettings:
    """读取 Batch File 配置，并兼容旧的通用 Judge 名称。"""

    values = _read_dotenv(Path(path))
    active_environ = os.environ if environ is None else environ
    names = (
        "BATCH_FILE_API_URL",
        "BATCH_FILE_API_KEY",
        "BATCH_FILE_MODEL",
        "BATCH_FILE_COMPLETION_WINDOW",
        "BATCH_FILE_POLL_INTERVAL_SECONDS",
        "BATCH_FILE_TIMEOUT_SECONDS",
        "BATCH_FILE_REQUEST_TIMEOUT_SECONDS",
        "BATCH_FILE_MAX_RETRIES",
        "BATCH_FILE_ENABLE_THINKING",
        "JUDGE_API_URL",
        "JUDGE_API_KEY",
        "JUDGE_MODEL",
    )
    for name in names:
        environment_value = active_environ.get(name)
        if environment_value is not None:
            values[name] = environment_value

    api_url = _preferred(values, "BATCH_FILE_API_URL", "JUDGE_API_URL")
    api_key = _preferred(values, "BATCH_FILE_API_KEY", "JUDGE_API_KEY")
    model = _preferred(values, "BATCH_FILE_MODEL", "JUDGE_MODEL")
    missing = [
        name
        for name, value in (
            ("BATCH_FILE_API_URL", api_url),
            ("BATCH_FILE_API_KEY", api_key),
            ("BATCH_FILE_MODEL", model),
        )
        if value is None
    ]
    if missing:
        raise ValueError(f"缺少 Batch File 配置: {', '.join(missing)}")
    return BatchFileSettings(
        api_url=cast(str, api_url),
        api_key=cast(str, api_key),
        model=cast(str, model),
        completion_window=values.get("BATCH_FILE_COMPLETION_WINDOW", "24h"),
        poll_interval_seconds=_optional_float(values, "BATCH_FILE_POLL_INTERVAL_SECONDS", 60),
        timeout_seconds=_optional_float(values, "BATCH_FILE_TIMEOUT_SECONDS", 86400),
        request_timeout_seconds=_optional_float(values, "BATCH_FILE_REQUEST_TIMEOUT_SECONDS", 90),
        max_retries=_optional_int(values, "BATCH_FILE_MAX_RETRIES", 2),
        enable_thinking=_optional_bool(values, "BATCH_FILE_ENABLE_THINKING", False),
    )


def request_json_completion(
    settings: BatchFileSettings,
    *,
    system_prompt: str,
    user_payload: Mapping[str, object],
    max_tokens: int,
    transport: BatchFileTransport | None = None,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> Mapping[str, object]:
    """提交单行 Batch File，等待任务结束并返回结构化模型结果。"""

    if max_tokens <= 0:
        raise ValueError("max_tokens 必须大于零")
    active_transport = transport or _request
    input_file_id: str | None = None
    output_file_id: str | None = None
    error_file_id: str | None = None
    batch_id: str | None = None
    try:
        jsonl = _build_jsonl(
            settings,
            system_prompt=system_prompt,
            user_payload=user_payload,
            max_tokens=max_tokens,
        )
        upload = _upload_file(settings, jsonl, active_transport)
        input_file_id = _required_field(upload, "id")
        created = _json_request(
            settings,
            active_transport,
            "POST",
            "/batches",
            {
                "input_file_id": input_file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": settings.completion_window,
            },
        )
        batch_id = _required_field(created, "id")
        batch = _wait_for_batch(settings, batch_id, active_transport, sleep, clock)
        status = _required_field(batch, "status")
        output_file_id = _optional_string(batch.get("output_file_id"))
        error_file_id = _optional_string(batch.get("error_file_id"))
        if status != "completed" or output_file_id is None:
            raise RuntimeError("Batch File 请求失败")
        content = _raw_request(
            settings,
            active_transport,
            "GET",
            f"/files/{output_file_id}/content",
        )
        return _parse_output(content, "request-1")
    except Exception:
        if batch_id is not None:
            _best_effort_request(settings, active_transport, "POST", f"/batches/{batch_id}/cancel")
        raise RuntimeError("Batch File 请求失败") from None
    finally:
        for file_id in (input_file_id, output_file_id, error_file_id):
            if file_id is not None:
                _best_effort_request(settings, active_transport, "DELETE", f"/files/{file_id}")


def _build_jsonl(
    settings: BatchFileSettings,
    *,
    system_prompt: str,
    user_payload: Mapping[str, object],
    max_tokens: int,
) -> bytes:
    request = {
        "custom_id": "request-1",
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": settings.model,
            "temperature": 0,
            "enable_thinking": settings.enable_thinking,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        },
    }
    return (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _upload_file(
    settings: BatchFileSettings,
    jsonl: bytes,
    transport: BatchFileTransport,
) -> Mapping[str, object]:
    boundary = f"----full-trace-audit-{uuid.uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="purpose"\r\n\r\n'
            "batch\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="batch_input.jsonl"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + jsonl
        + f"\r\n--{boundary}--\r\n".encode("ascii")
    )
    raw = _send_with_retries(
        settings,
        transport,
        "POST",
        "/files",
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return _parse_object(raw)


def _wait_for_batch(
    settings: BatchFileSettings,
    batch_id: str,
    transport: BatchFileTransport,
    sleep: Sleep,
    clock: Clock,
) -> Mapping[str, object]:
    deadline = clock() + settings.timeout_seconds
    while True:
        batch = _parse_object(_raw_request(settings, transport, "GET", f"/batches/{batch_id}"))
        status = _required_field(batch, "status")
        if status in _TERMINAL_STATUSES:
            return batch
        if clock() + settings.poll_interval_seconds > deadline:
            raise RuntimeError("Batch File 请求失败")
        sleep(settings.poll_interval_seconds)


def _json_request(
    settings: BatchFileSettings,
    transport: BatchFileTransport,
    method: str,
    path: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    raw = _send_with_retries(
        settings,
        transport,
        method,
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    return _parse_object(raw)


def _raw_request(
    settings: BatchFileSettings,
    transport: BatchFileTransport,
    method: str,
    path: str,
) -> bytes:
    return _send_with_retries(settings, transport, method, path, None, {})


def _send_with_retries(
    settings: BatchFileSettings,
    transport: BatchFileTransport,
    method: str,
    path: str,
    body: bytes | None,
    extra_headers: Mapping[str, str],
) -> bytes:
    headers = {"Authorization": f"Bearer {settings.api_key}", **extra_headers}
    url = f"{settings.api_url.strip().rstrip('/')}{path}"
    # 上传和创建任务不是幂等操作；响应丢失后重试可能产生重复任务与重复计费。
    retry_count = settings.max_retries if _is_retryable(method, path) else 0
    for attempt in range(retry_count + 1):
        try:
            return transport(method, url, body, headers, settings.request_timeout_seconds)
        except Exception:
            if attempt == retry_count:
                raise RuntimeError("Batch File 请求失败") from None
    raise RuntimeError("Batch File 请求失败")


def _is_retryable(method: str, path: str) -> bool:
    return method in {"GET", "DELETE"} or path.endswith("/cancel")


def _best_effort_request(
    settings: BatchFileSettings,
    transport: BatchFileTransport,
    method: str,
    path: str,
) -> None:
    try:
        _raw_request(settings, transport, method, path)
    except Exception:
        pass


def _parse_output(content: bytes, custom_id: str) -> Mapping[str, object]:
    for raw_line in content.decode("utf-8").splitlines():
        if not raw_line.strip():
            continue
        item = _parse_object(raw_line.encode("utf-8"))
        if item.get("custom_id") != custom_id:
            continue
        response = item.get("response")
        if item.get("error") is not None or not isinstance(response, Mapping):
            break
        if response.get("status_code") != 200:
            break
        body = response.get("body")
        if not isinstance(body, Mapping):
            break
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            break
        choice = choices[0]
        if not isinstance(choice, Mapping):
            break
        message = choice.get("message")
        if not isinstance(message, Mapping):
            break
        model_content = message.get("content")
        if not isinstance(model_content, str):
            break
        parsed = json.loads(model_content)
        if isinstance(parsed, Mapping):
            return cast(Mapping[str, object], parsed)
        break
    raise RuntimeError("Batch File 请求失败")


def _parse_object(raw: bytes) -> Mapping[str, object]:
    parsed = json.loads(raw)
    if not isinstance(parsed, Mapping):
        raise RuntimeError("Batch File 请求失败")
    return cast(Mapping[str, object], parsed)


def _required_field(value: Mapping[str, object], name: str) -> str:
    parsed = _optional_string(value.get(name))
    if parsed is None:
        raise RuntimeError("Batch File 请求失败")
    return parsed


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _request(
    method: str,
    url: str,
    body: bytes | None,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return cast(bytes, response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        raise RuntimeError("Batch File 请求失败") from None


def _completion_window_hours(value: str) -> int:
    normalized = value.strip().lower()
    try:
        if normalized.endswith("h"):
            hours = int(normalized[:-1])
        elif normalized.endswith("d"):
            hours = int(normalized[:-1]) * 24
        else:
            raise ValueError
    except ValueError:
        raise ValueError("BATCH_FILE_COMPLETION_WINDOW 必须使用整数 h 或 d") from None
    if not 24 <= hours <= 336:
        raise ValueError("BATCH_FILE_COMPLETION_WINDOW 必须位于 24h 到 336h")
    return hours


def _positive_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} 必须大于零")


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"Batch File 配置文件不存在: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(".env 必须使用 KEY=VALUE 格式")
        name, raw_value = line.split("=", 1)
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _preferred(values: Mapping[str, str], primary: str, legacy: str) -> str | None:
    return values.get(primary) or values.get(legacy)


def _optional_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是数值") from None


def _optional_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是整数") from None


def _optional_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")
