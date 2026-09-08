"""提取路由评测所需的意图片段和实际 MCP 能力集合。"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypedDict, TypeGuard


@dataclass(frozen=True, slots=True)
class CapabilityParameter:
    """提供给路由 Judge 的最小参数语义，不包含任何调用参数值。"""

    name: str
    parameter_type: str
    required: bool
    description: str


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """一个可路由 MCP 能力的版本化定义。"""

    capability_id: str
    description: str
    parameters: tuple[CapabilityParameter, ...]


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    """MCP 工具名、兼容别名与稳定能力 ID 的只读目录。"""

    schema_version: str
    capabilities: Mapping[str, CapabilityDefinition]
    aliases: Mapping[str, str]

    def resolve(self, tool_name: object) -> str | None:
        """将日志工具名解析为稳定能力 ID；未知框架工具不进入指标范围。"""

        normalized = _identifier(tool_name)
        if normalized is None:
            return None
        direct = self.aliases.get(normalized)
        if direct is not None:
            return direct
        if normalized.startswith("mcp__"):
            suffix = normalized.rsplit("__", 1)[-1]
            return self.aliases.get(suffix)
        return None


class RoutingDiagnostics(TypedDict):
    """不包含用户正文的路由规范化诊断。"""

    malformed_event_count: int
    out_of_scope_tool_call_count: int
    unknown_mcp_tool_call_count: int
    duplicate_call_definition_count: int


class NormalizedRoutingCandidate(TypedDict):
    """路由 Judge 的内存候选；意图片段不得进入最终评测结果。"""

    trace_id: str | None
    mode: str
    intent_fragments: tuple[str, ...]
    actual_capability_ids: tuple[str, ...]
    assumed_matching_mcp_route_count: int
    diagnostics: RoutingDiagnostics
    normalization_error: str | None


def build_capability_catalog(
    manual: Mapping[str, object], *, aliases: Mapping[str, str] | None = None
) -> CapabilityCatalog:
    """从 MCP 手册构建能力目录，并允许显式登记跨模式工具别名。"""

    schema_version = _identifier(manual.get("manual_version"))
    raw_tools = manual.get("tools")
    if schema_version is None:
        raise ValueError("MCP 手册 manual_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("MCP 手册 tools 必须是数组")

    definitions: dict[str, CapabilityDefinition] = {}
    resolved_aliases: dict[str, str] = {}
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("MCP 工具定义必须是对象")
        name = _identifier(raw_tool.get("name"))
        description = _identifier(raw_tool.get("description"))
        if name is None or description is None:
            raise ValueError("MCP 工具 name 和 description 必须是非空字符串")
        if name in definitions:
            raise ValueError(f"MCP 工具 name 重复: {name}")
        parameters = _parse_parameters(raw_tool.get("parameters", ()), context=f"工具 {name}")
        definitions[name] = CapabilityDefinition(name, description, parameters)
        resolved_aliases[name] = name

    for raw_alias, raw_capability_id in (aliases or {}).items():
        alias = _identifier(raw_alias)
        capability_id = _identifier(raw_capability_id)
        if alias is None or capability_id is None:
            raise ValueError("能力别名和 capability_id 必须是非空字符串")
        if capability_id not in definitions:
            raise ValueError(f"能力别名指向未知 capability_id: {capability_id}")
        existing = resolved_aliases.get(alias)
        if existing is not None and existing != capability_id:
            raise ValueError(f"能力别名冲突: {alias}")
        resolved_aliases[alias] = capability_id

    return CapabilityCatalog(
        schema_version=schema_version,
        capabilities=MappingProxyType(definitions),
        aliases=MappingProxyType(resolved_aliases),
    )


def normalize_chat_routing(
    trace: Mapping[str, object], catalog: CapabilityCatalog
) -> NormalizedRoutingCandidate:
    """从新版 canvas 提取整体意图、子任务和去重后的 MCP 能力集合。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty_candidate(
            trace_id=None,
            mode="chat",
            intents=[],
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty_candidate(
            trace_id=None,
            mode="chat",
            intents=[],
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    trace_id = _identifier(meta.get("traceId"))
    if trace_id is None:
        return _empty_candidate(
            trace_id=None,
            mode="chat",
            intents=[],
            normalization_error="missing_trace_id",
            malformed_event_count=1,
        )
    intents: list[str] = []
    _append_unique(intents, meta.get("query"))
    declared_mcp_tools = _declared_chat_mcp_tools(meta.get("tools"))
    actual_capabilities: set[str] = set()
    assumed_matching_mcp_tools: set[str] = set()
    malformed_event_count = 0
    out_of_scope_count = 0
    unknown_mcp_call_count = 0
    ordinary_call_count = 0
    duplicate_count = 0
    calls: dict[str, str] = {}
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _empty_candidate(
            trace_id=trace_id,
            mode="chat",
            intents=intents,
            normalization_error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )

    normalization_error: str | None = None
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    malformed_event_count += 1
                    normalization_error = normalization_error or "invalid_chat_trace_structure"
                    continue
                call_id = _identifier(raw_call.get("toolId"))
                tool_name = _identifier(raw_call.get("toolName"))
                agent_name = _identifier(raw_call.get("agentName"))
                if call_id is None and agent_name is not None:
                    arguments = _parse_arguments(raw_call.get("arguments"))
                    if arguments is None:
                        malformed_event_count += 1
                        continue
                    task = _identifier(arguments.get("query")) or _identifier(arguments.get("task"))
                    if task is None:
                        malformed_event_count += 1
                    else:
                        _append_unique(intents, task)
                    continue

                if call_id is not None or tool_name is not None or "arguments" in raw_call:
                    ordinary_call_count += 1
                if call_id is None and (tool_name is not None or "arguments" in raw_call):
                    malformed_event_count += 1
                    normalization_error = normalization_error or "missing_call_id"
                    continue
                if call_id is not None and tool_name is None:
                    malformed_event_count += 1
                    normalization_error = normalization_error or "missing_tool_name"
                    continue
                if call_id is None or tool_name is None:
                    malformed_event_count += 1
                    normalization_error = normalization_error or "invalid_chat_trace_structure"
                    continue

                previous = calls.get(call_id)
                if previous is not None:
                    if previous == tool_name:
                        duplicate_count += 1
                    else:
                        normalization_error = normalization_error or "conflicting_call_definition"
                    continue
                calls[call_id] = tool_name
                capability_id = catalog.resolve(tool_name)
                if capability_id is None:
                    if tool_name in declared_mcp_tools:
                        assumed_matching_mcp_tools.add(tool_name)
                        unknown_mcp_call_count += 1
                    else:
                        out_of_scope_count += 1
                else:
                    actual_capabilities.add(capability_id)

    if ordinary_call_count == 0 and _is_pre_routing_failure(_chat_answer(canvas)):
        normalization_error = "pre_routing_failure"
    elif not intents:
        normalization_error = normalization_error or "missing_intent"
    return {
        "trace_id": trace_id,
        "mode": "chat",
        "intent_fragments": tuple(intents),
        "actual_capability_ids": tuple(sorted(actual_capabilities)),
        "assumed_matching_mcp_route_count": len(assumed_matching_mcp_tools),
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "unknown_mcp_tool_call_count": unknown_mcp_call_count,
            "duplicate_call_definition_count": duplicate_count,
        },
        "normalization_error": normalization_error,
    }


def normalize_research_routing(
    events: Sequence[object], catalog: CapabilityCatalog
) -> NormalizedRoutingCandidate:
    """按调用 ID 去重研究日志，并提取可见的整体及步骤意图。"""

    trace_id: str | None = None
    session_id: str | None = None
    user_message_id: str | None = None
    intents: list[str] = []
    calls: dict[str, str] = {}
    declared_mcp_tools = _declared_research_mcp_tools(events)
    actual_capabilities: set[str] = set()
    assumed_matching_mcp_tools: set[str] = set()
    malformed_event_count = 0
    out_of_scope_count = 0
    unknown_mcp_call_count = 0
    duplicate_count = 0
    normalization_error: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        session_id = session_id or _identifier(event.get("sessionID"))
        user_message_id = user_message_id or _identifier(event.get("userMessageID"))
        _append_unique(intents, event.get("query"))
        body = event.get("body")
        if isinstance(body, Mapping):
            _append_unique(intents, body.get("query"))
            _append_unique(intents, body.get("task"))
            _append_unique(intents, body.get("description"))

        result = event.get("result")
        if not isinstance(result, Mapping):
            continue
        raw_calls = result.get("toolCalls")
        if raw_calls is None:
            continue
        if not _is_sequence(raw_calls):
            malformed_event_count += 1
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                malformed_event_count += 1
                continue
            call_id = _identifier(raw_call.get("toolCallId"))
            tool_name = _identifier(raw_call.get("toolName"))
            if call_id is None:
                normalization_error = normalization_error or "missing_call_id"
                continue
            if tool_name is None:
                normalization_error = normalization_error or "missing_tool_name"
                continue
            previous = calls.get(call_id)
            if previous is not None:
                if previous == tool_name:
                    duplicate_count += 1
                else:
                    normalization_error = "conflicting_call_definition"
                continue
            calls[call_id] = tool_name
            capability_id = catalog.resolve(tool_name)
            if capability_id is None:
                if tool_name in declared_mcp_tools:
                    assumed_matching_mcp_tools.add(tool_name)
                    unknown_mcp_call_count += 1
                else:
                    out_of_scope_count += 1
            else:
                actual_capabilities.add(capability_id)

    if session_id is not None and user_message_id is not None:
        trace_id = f"{session_id}:{user_message_id}"
    else:
        trace_id = session_id or user_message_id
    if not intents:
        normalization_error = normalization_error or "missing_intent"
    return {
        "trace_id": trace_id,
        "mode": "research",
        "intent_fragments": tuple(intents),
        "actual_capability_ids": tuple(sorted(actual_capabilities)),
        "assumed_matching_mcp_route_count": len(assumed_matching_mcp_tools),
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "unknown_mcp_tool_call_count": unknown_mcp_call_count,
            "duplicate_call_definition_count": duplicate_count,
        },
        "normalization_error": normalization_error,
    }


def _parse_parameters(raw_parameters: object, *, context: str) -> tuple[CapabilityParameter, ...]:
    if not _is_sequence(raw_parameters):
        raise ValueError(f"{context} parameters 必须是数组")
    parameters: list[CapabilityParameter] = []
    for raw_parameter in raw_parameters:
        if not isinstance(raw_parameter, Mapping):
            raise ValueError(f"{context} 参数必须是对象")
        name = _identifier(raw_parameter.get("name"))
        parameter_type = _identifier(raw_parameter.get("type"))
        description = _identifier(raw_parameter.get("description"))
        required = raw_parameter.get("required", False)
        if name is None or parameter_type is None or description is None:
            raise ValueError(f"{context} 参数 name、type、description 必须是非空字符串")
        if not isinstance(required, bool):
            raise ValueError(f"{context} 参数 required 必须是布尔值")
        parameters.append(CapabilityParameter(name, parameter_type, required, description))
    return tuple(parameters)


def _append_unique(destination: list[str], value: object) -> None:
    normalized = _identifier(value)
    if normalized is not None and normalized not in destination:
        destination.append(normalized)


def _parse_arguments(value: object) -> Mapping[str, object] | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(parsed, Mapping):
        return None
    return {str(key): item for key, item in parsed.items()}


def _declared_chat_mcp_tools(raw_tools: object) -> frozenset[str]:
    """读取聊天日志声明的 MCP 工具名；无可靠声明时不猜测工具类别。"""

    if not _is_sequence(raw_tools):
        return frozenset()
    return frozenset(
        tool_name for value in raw_tools if (tool_name := _identifier(value)) is not None
    )


def _declared_research_mcp_tools(events: Sequence[object]) -> frozenset[str]:
    """从 agent.mcp 事件提取完整名称和可验证的服务前缀短名称。"""

    tools: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "agent.mcp":
            continue
        raw_servers = event.get("mcp")
        if not _is_sequence(raw_servers):
            continue
        for raw_server in raw_servers:
            if not isinstance(raw_server, Mapping):
                continue
            server_name = _identifier(raw_server.get("name"))
            raw_tools = raw_server.get("tools")
            if not _is_sequence(raw_tools):
                continue
            for raw_tool in raw_tools:
                tool_name = _identifier(raw_tool)
                if tool_name is None:
                    continue
                tools.add(tool_name)
                if "___" in tool_name:
                    tools.add(tool_name.rsplit("___", 1)[-1])
                if server_name is not None and tool_name.startswith(f"{server_name}_"):
                    tools.add(tool_name[len(server_name) + 1 :])
    return frozenset(tools)


def _chat_answer(canvas: Mapping[object, object]) -> object:
    answer = canvas.get("answer")
    return answer.get("answer") if isinstance(answer, Mapping) else None


def _empty_candidate(
    *,
    trace_id: str | None,
    mode: str,
    intents: list[str],
    normalization_error: str,
    malformed_event_count: int,
) -> NormalizedRoutingCandidate:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "intent_fragments": tuple(intents),
        "actual_capability_ids": (),
        "assumed_matching_mcp_route_count": 0,
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": 0,
            "unknown_mcp_tool_call_count": 0,
            "duplicate_call_definition_count": 0,
        },
        "normalization_error": normalization_error,
    }


def _identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_pre_routing_failure(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().rstrip("。")
    return normalized == "模型服务暂时不可用，请稍后重试"


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
