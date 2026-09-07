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
    """扫描新版 canvas，并按 `traceId + toolId` 去重普通 MCP 调用。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _result(
            trace_id=None,
            mode="chat",
            manual_version=catalog.schema_version,
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _result(
            trace_id=None,
            mode="chat",
            manual_version=catalog.schema_version,
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    trace_id = _optional_identifier(meta.get("traceId"))
    if trace_id is None:
        return _result(
            trace_id=None,
            mode="chat",
            manual_version=catalog.schema_version,
            normalization_error="missing_trace_id",
            malformed_event_count=1,
        )

    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _result(
            trace_id=trace_id,
            mode="chat",
            manual_version=catalog.schema_version,
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )

    calls: dict[str, str] = {}
    definitions: dict[str, str] = {}
    out_of_scope_call_ids: set[str] = set()
    malformed_event_count = 0
    duplicate_call_observation_count = 0
    normalization_error: str | None = None

    def record_error(reason: str) -> None:
        nonlocal malformed_event_count, normalization_error
        malformed_event_count += 1
        normalization_error = normalization_error or reason

    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            record_error("invalid_chat_trace_structure")
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            record_error("invalid_chat_trace_structure")
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                record_error("invalid_chat_trace_structure")
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                record_error("invalid_chat_trace_structure")
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                record_error("invalid_chat_trace_structure")
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    record_error("invalid_chat_trace_structure")
                    continue

                call_id = _optional_identifier(raw_call.get("toolId"))
                tool_name = _optional_identifier(raw_call.get("toolName"))
                agent_name = _optional_identifier(raw_call.get("agentName"))
                if call_id is None and tool_name is None and agent_name is not None:
                    # 子 Agent 调用与 MCP 工具调用不是同一类事件，不能进入分母。
                    continue
                if call_id is None and (tool_name is not None or "arguments" in raw_call):
                    record_error("missing_call_id")
                    continue
                if call_id is not None and tool_name is None:
                    record_error("missing_tool_name")
                    continue
                if call_id is None or tool_name is None:
                    record_error("invalid_chat_trace_structure")
                    continue
                if "arguments" not in raw_call:
                    record_error("missing_call_arguments")
                    continue

                capability_id = catalog.resolve(tool_name)
                definition_identity = capability_id or f"out_of_scope:{tool_name}"
                signature = _request_signature(definition_identity, raw_call["arguments"])
                if signature is None:
                    record_error("unserializable_call_arguments")
                    continue

                existing = definitions.get(call_id)
                if existing is None:
                    definitions[call_id] = signature
                elif existing == signature:
                    duplicate_call_observation_count += 1
                    continue
                else:
                    normalization_error = normalization_error or "conflicting_call_definition"
                    continue

                if capability_id is None:
                    out_of_scope_call_ids.add(call_id)
                else:
                    calls[call_id] = signature

    return _result(
        trace_id=trace_id,
        mode="chat",
        manual_version=catalog.schema_version,
        calls=calls,
        malformed_event_count=malformed_event_count,
        out_of_scope_tool_call_count=len(out_of_scope_call_ids),
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
        definition_priority: int,
    ) -> None:
        nonlocal duplicate_call_observation_count, normalization_error
        call_id = _optional_identifier(call_id_value)
        if call_id is None:
            normalization_error = normalization_error or "missing_call_id"
            return
        normalized_tool_name = _optional_identifier(tool_name)
        if normalized_tool_name is None:
            normalization_error = normalization_error or "missing_tool_name"
            return

        capability_id = catalog.resolve(normalized_tool_name)
        call_identity = capability_id or f"out_of_scope:{normalized_tool_name}"

        existing_capability = capabilities_by_call_id.get(call_id)
        if existing_capability is None:
            capabilities_by_call_id[call_id] = call_identity
        elif existing_capability != call_identity:
            normalization_error = normalization_error or "conflicting_call_definition"
            return
        if capability_id is None:
            out_of_scope_call_keys.add(call_id)
            return

        observed_mcp_call_ids.add(call_id)
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

    for event in events:
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
                for raw_call in raw_tool_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_event_count += 1
                        normalization_error = normalization_error or "invalid_tool_call_events"
                        continue
                    observe_call(
                        tool_name=raw_call.get("toolName"),
                        call_id_value=raw_call.get("toolCallId"),
                        arguments_present="input" in raw_call,
                        arguments=raw_call.get("input"),
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
