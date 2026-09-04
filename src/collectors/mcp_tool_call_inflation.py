"""规范化 MCP 工具调用膨胀率所需的调用计数，不保留原始入参。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypedDict, TypeGuard

from collectors.routing import CapabilityCatalog


class McpToolCallInflationDiagnostics(TypedDict):
    """不包含工具入参或响应正文的调用膨胀诊断。"""

    malformed_event_count: int
    out_of_scope_tool_call_count: int
    duplicate_call_observation_count: int


class NormalizedMcpToolCallInflationTrace(TypedDict):
    """供 MCP 工具调用膨胀率评估器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    manual_version: str
    mcp_call_count: int
    unique_request_signature_count: int
    inflated_call_count: int
    diagnostics: McpToolCallInflationDiagnostics
    normalization_error: str | None


def normalize_chat_mcp_tool_call_inflation(
    trace: Mapping[str, object], catalog: CapabilityCatalog
) -> NormalizedMcpToolCallInflationTrace:
    """按调用 ID 去重聊天事件，并比较 MCP 名称与规范化入参。"""

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _result(
            trace_id=_optional_identifier(trace.get("traceId")),
            mode="chat",
            manual_version=catalog.schema_version,
            normalization_error="invalid_tool_call_queue",
            malformed_event_count=1,
        )

    calls: dict[str, str] = {}
    malformed_event_count = 0
    out_of_scope_tool_call_count = 0
    duplicate_call_observation_count = 0
    normalization_error: str | None = None

    for event in raw_queue:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_tool_call_events"
            continue
        if event.get("event_type") != "tool_call":
            continue
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_tool_call_events"
            continue

        capability_id = catalog.resolve(request.get("toolName"))
        if capability_id is None:
            out_of_scope_tool_call_count += 1
            continue
        call_id = _optional_identifier(request.get("toolId"))
        if call_id is None:
            normalization_error = normalization_error or "missing_call_id"
            continue
        if "arguments" not in request:
            normalization_error = normalization_error or "missing_call_arguments"
            continue
        signature = _request_signature(capability_id, request["arguments"])
        if signature is None:
            normalization_error = normalization_error or "unserializable_call_arguments"
            continue

        existing = calls.get(call_id)
        if existing is None:
            calls[call_id] = signature
        elif existing == signature:
            duplicate_call_observation_count += 1
        else:
            normalization_error = normalization_error or "conflicting_call_definition"

    return _result(
        trace_id=_optional_identifier(trace.get("traceId")),
        mode="chat",
        manual_version=catalog.schema_version,
        calls=calls,
        malformed_event_count=malformed_event_count,
        out_of_scope_tool_call_count=out_of_scope_tool_call_count,
        duplicate_call_observation_count=duplicate_call_observation_count,
        normalization_error=normalization_error,
    )


def normalize_research_mcp_tool_call_inflation(
    events: Sequence[object],
    catalog: CapabilityCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedMcpToolCallInflationTrace:
    """按 `callID` 合并研究模式定义和生命周期事件，避免重复统计状态。"""

    calls: dict[str, str] = {}
    definition_priorities: dict[str, int] = {}
    capabilities_by_call_id: dict[str, str] = {}
    observed_mcp_call_ids: set[str] = set()
    out_of_scope_call_keys: set[str] = set()
    malformed_event_count = 0
    duplicate_call_observation_count = 0
    normalization_error: str | None = None
    resolved_session_id = session_id
    resolved_user_message_id = user_message_id

    def observe_call(
        *,
        tool_name: object,
        call_id_value: object,
        arguments_present: bool,
        arguments: object,
        observation_key: str,
        definition_priority: int,
    ) -> None:
        nonlocal duplicate_call_observation_count, normalization_error
        capability_id = catalog.resolve(tool_name)
        call_id = _optional_identifier(call_id_value)
        if capability_id is None:
            out_of_scope_call_keys.add(call_id or observation_key)
            return
        if call_id is None:
            normalization_error = normalization_error or "missing_call_id"
            return

        observed_mcp_call_ids.add(call_id)
        existing_capability = capabilities_by_call_id.get(call_id)
        if existing_capability is None:
            capabilities_by_call_id[call_id] = capability_id
        elif existing_capability != capability_id:
            normalization_error = normalization_error or "conflicting_call_definition"
            return
        if not arguments_present:
            return

        signature = _request_signature(capability_id, arguments)
        if signature is None:
            normalization_error = normalization_error or "unserializable_call_arguments"
            return
        existing_signature = calls.get(call_id)
        if existing_signature is None:
            calls[call_id] = signature
            definition_priorities[call_id] = definition_priority
        elif definition_priority > definition_priorities[call_id]:
            # 生命周期的 pending 输入可能只是空占位；模型请求定义具有更高可信度。
            calls[call_id] = signature
            definition_priorities[call_id] = definition_priority
        elif definition_priority < definition_priorities[call_id]:
            return
        elif existing_signature == signature:
            duplicate_call_observation_count += 1
        else:
            normalization_error = normalization_error or "conflicting_call_definition"

    for event_index, event in enumerate(events):
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_tool_call_events"
            continue
        if resolved_session_id is None:
            resolved_session_id = _optional_identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _optional_identifier(event.get("userMessageID"))

        result = event.get("result")
        if isinstance(result, Mapping):
            raw_tool_calls = result.get("toolCalls")
            if _is_sequence(raw_tool_calls):
                for call_index, raw_call in enumerate(raw_tool_calls):
                    if not isinstance(raw_call, Mapping):
                        malformed_event_count += 1
                        normalization_error = normalization_error or "invalid_tool_call_events"
                        continue
                    observe_call(
                        tool_name=raw_call.get("toolName"),
                        call_id_value=raw_call.get("toolCallId"),
                        arguments_present="input" in raw_call,
                        arguments=raw_call.get("input"),
                        observation_key=f"definition:{event_index}:{call_index}",
                        definition_priority=2,
                    )

        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_tool_call_events"
            continue
        if part.get("type") != "tool":
            continue
        state = part.get("state")
        pending_placeholder = (
            isinstance(state, Mapping)
            and str(state.get("status", "")).lower() == "pending"
            and state.get("input") == {}
        )
        arguments_present = (
            isinstance(state, Mapping) and "input" in state and not pending_placeholder
        )
        observe_call(
            tool_name=part.get("tool"),
            call_id_value=part.get("callID"),
            arguments_present=arguments_present,
            arguments=state.get("input") if isinstance(state, Mapping) else None,
            observation_key=f"lifecycle:{event_index}",
            definition_priority=1,
        )

    if observed_mcp_call_ids - calls.keys():
        normalization_error = normalization_error or "missing_call_arguments"

    return _result(
        trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
        mode="research",
        manual_version=catalog.schema_version,
        calls=calls,
        malformed_event_count=malformed_event_count,
        out_of_scope_tool_call_count=len(out_of_scope_call_keys),
        duplicate_call_observation_count=duplicate_call_observation_count,
        normalization_error=normalization_error,
    )


def _request_signature(capability_id: str, arguments: object) -> str | None:
    normalized = _canonicalize(arguments)
    try:
        encoded_arguments = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(f"{capability_id}\0{encoded_arguments}".encode()).hexdigest()


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


def _result(
    *,
    trace_id: str | None,
    mode: str,
    manual_version: str,
    calls: Mapping[str, str] | None = None,
    malformed_event_count: int = 0,
    out_of_scope_tool_call_count: int = 0,
    duplicate_call_observation_count: int = 0,
    normalization_error: str | None,
) -> NormalizedMcpToolCallInflationTrace:
    signatures = tuple((calls or {}).values())
    mcp_call_count = len(signatures)
    unique_request_signature_count = len(set(signatures))
    return {
        "trace_id": trace_id,
        "mode": mode,
        "manual_version": manual_version,
        "mcp_call_count": mcp_call_count,
        "unique_request_signature_count": unique_request_signature_count,
        "inflated_call_count": mcp_call_count - unique_request_signature_count,
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": out_of_scope_tool_call_count,
            "duplicate_call_observation_count": duplicate_call_observation_count,
        },
        "normalization_error": normalization_error,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _optional_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
