"""规范化完全重复工具调用，只输出聚合计数而不保留调用正文。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard


class ExactDuplicateDiagnostics(TypedDict):
    """不包含原始请求或响应内容的重复检测诊断。"""

    malformed_event_count: int
    missing_response_payload_count: int
    duplicate_state_event_count: int


class NormalizedExactDuplicateTrace(TypedDict):
    """供完全重复调用率评估器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    unique_call_count: int
    comparable_call_count: int
    exact_duplicate_call_count: int
    diagnostics: ExactDuplicateDiagnostics
    normalization_error: str | None


_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


def normalize_chat_exact_duplicates(
    trace: Mapping[str, object],
) -> NormalizedExactDuplicateTrace:
    """比较聊天模式中工具名、参数和响应均相同的后续调用。"""

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _result(
            trace_id=_optional_identifier(trace.get("traceId")),
            mode="chat",
            normalization_error="invalid_tool_call_queue",
            malformed_event_count=1,
        )

    unique_call_count = 0
    comparable_call_count = 0
    exact_duplicate_call_count = 0
    malformed_event_count = 0
    missing_response_payload_count = 0
    normalization_error: str | None = None
    observed_fingerprints: set[str] = set()

    for event in raw_queue:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if event.get("event_type") != "tool_call":
            continue

        unique_call_count += 1
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            normalization_error = "missing_duplicate_key"
            continue
        tool_name = _optional_identifier(request.get("toolName"))
        if tool_name is None or "arguments" not in request:
            normalization_error = "missing_duplicate_key"
            continue

        payload_found, payload = _chat_response_payload(event.get("tool_response_result"))
        if not payload_found:
            missing_response_payload_count += 1
            continue

        request_digest = _canonical_digest(request["arguments"])
        response_digest = _canonical_digest(payload)
        if request_digest is None or response_digest is None:
            normalization_error = "unserializable_duplicate_value"
            continue

        comparable_call_count += 1
        fingerprint = _combined_digest(tool_name, request_digest, response_digest)
        if fingerprint in observed_fingerprints:
            exact_duplicate_call_count += 1
        else:
            observed_fingerprints.add(fingerprint)

    return _result(
        trace_id=_optional_identifier(trace.get("traceId")),
        mode="chat",
        unique_call_count=unique_call_count,
        comparable_call_count=comparable_call_count,
        exact_duplicate_call_count=exact_duplicate_call_count,
        malformed_event_count=malformed_event_count,
        missing_response_payload_count=missing_response_payload_count,
        normalization_error=normalization_error,
    )


def normalize_research_exact_duplicates(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedExactDuplicateTrace:
    """按 `callID` 去重后比较研究模式调用，不把输入输出正文带出规范化阶段。"""

    requests: dict[str, tuple[str, str] | None] = {}
    terminal_payloads: dict[str, str | None] = {}
    observed_statuses: dict[str, set[str]] = {}
    observed_call_ids: set[str] = set()
    malformed_event_count = 0
    duplicate_state_event_count = 0
    normalization_error: str | None = None
    resolved_session_id = session_id
    resolved_user_message_id = user_message_id

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if resolved_session_id is None:
            resolved_session_id = _optional_identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _optional_identifier(event.get("userMessageID"))

        result = event.get("result")
        if isinstance(result, Mapping):
            raw_tool_calls = result.get("toolCalls")
            if _is_sequence(raw_tool_calls):
                for raw_call in raw_tool_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_event_count += 1
                        continue
                    call_id = _optional_identifier(raw_call.get("toolCallId"))
                    tool_name = _optional_identifier(raw_call.get("toolName"))
                    if call_id is None:
                        normalization_error = "missing_call_id"
                        continue
                    observed_call_ids.add(call_id)
                    if tool_name is None or "input" not in raw_call:
                        requests[call_id] = None
                        normalization_error = "missing_duplicate_key"
                        continue
                    request_digest = _canonical_digest(raw_call["input"])
                    if request_digest is None:
                        requests[call_id] = None
                        normalization_error = "unserializable_duplicate_value"
                        continue
                    parsed_request_key = (tool_name, request_digest)
                    existing = requests.get(call_id)
                    if existing is not None and existing != parsed_request_key:
                        normalization_error = "conflicting_call_definition"
                        continue
                    requests[call_id] = parsed_request_key

        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            malformed_event_count += 1
            continue
        if part.get("type") != "tool":
            continue
        call_id = _optional_identifier(part.get("callID"))
        if call_id is None:
            normalization_error = "missing_call_id"
            continue
        observed_call_ids.add(call_id)
        state = part.get("state")
        if not isinstance(state, Mapping):
            malformed_event_count += 1
            continue
        status = _optional_identifier(state.get("status"))
        if status is None:
            malformed_event_count += 1
            continue
        normalized_status = status.lower()
        statuses = observed_statuses.setdefault(call_id, set())
        if normalized_status in statuses:
            duplicate_state_event_count += 1
        else:
            statuses.add(normalized_status)
        if normalized_status not in _TERMINAL_STATUSES:
            continue

        payload_found, payload = _research_response_payload(state)
        if not payload_found:
            terminal_payloads[call_id] = None
            continue
        payload_digest = _canonical_digest({"status": normalized_status, "payload": payload})
        if payload_digest is None:
            normalization_error = "unserializable_duplicate_value"
        else:
            terminal_payloads[call_id] = payload_digest

    if any(call_id not in requests or requests[call_id] is None for call_id in observed_call_ids):
        normalization_error = normalization_error or "missing_duplicate_key"

    comparable_call_count = 0
    exact_duplicate_call_count = 0
    observed_fingerprints: set[str] = set()
    for call_id, stored_request_key in requests.items():
        if stored_request_key is None:
            continue
        response_digest = terminal_payloads.get(call_id)
        if response_digest is None:
            continue
        comparable_call_count += 1
        fingerprint = _combined_digest(
            stored_request_key[0], stored_request_key[1], response_digest
        )
        if fingerprint in observed_fingerprints:
            exact_duplicate_call_count += 1
        else:
            observed_fingerprints.add(fingerprint)

    unique_call_count = len(observed_call_ids)
    return _result(
        trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
        mode="research",
        unique_call_count=unique_call_count,
        comparable_call_count=comparable_call_count,
        exact_duplicate_call_count=exact_duplicate_call_count,
        malformed_event_count=malformed_event_count,
        missing_response_payload_count=sum(
            terminal_payloads.get(call_id) is None for call_id in observed_call_ids
        ),
        duplicate_state_event_count=duplicate_state_event_count,
        normalization_error=normalization_error,
    )


def _chat_response_payload(response: object) -> tuple[bool, object]:
    if not isinstance(response, Mapping):
        return False, None
    for key in ("result", "rawResult"):
        if key in response and _has_content(response[key]):
            return True, response[key]
    return False, None


def _research_response_payload(state: Mapping[object, object]) -> tuple[bool, object]:
    for key in ("output", "raw"):
        if key in state and _has_content(state[key]):
            return True, state[key]
    return False, None


def _canonical_digest(value: object) -> str | None:
    normalized = _canonicalize(value)
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonicalize(value: object) -> object:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(decoded, (Mapping, list)):
            return _canonicalize(decoded)
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_canonicalize(child) for child in value]
    return value


def _combined_digest(tool_name: str, request_digest: str, response_digest: str) -> str:
    encoded = "\x1f".join((tool_name, request_digest, response_digest)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(
    *,
    trace_id: str | None,
    mode: str,
    unique_call_count: int = 0,
    comparable_call_count: int = 0,
    exact_duplicate_call_count: int = 0,
    malformed_event_count: int = 0,
    missing_response_payload_count: int = 0,
    duplicate_state_event_count: int = 0,
    normalization_error: str | None,
) -> NormalizedExactDuplicateTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "unique_call_count": unique_call_count,
        "comparable_call_count": comparable_call_count,
        "exact_duplicate_call_count": exact_duplicate_call_count,
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "missing_response_payload_count": missing_response_payload_count,
            "duplicate_state_event_count": duplicate_state_event_count,
        },
        "normalization_error": normalization_error,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _optional_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _has_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, Mapping, Sequence)):
        return len(value) > 0
    return True


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
