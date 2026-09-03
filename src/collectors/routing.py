"""提取路由评测所需的意图片段和实际 MCP 能力集合。"""

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
    duplicate_call_definition_count: int


class NormalizedRoutingCandidate(TypedDict):
    """路由 Judge 的内存候选；意图片段不得进入最终评测结果。"""

    trace_id: str | None
    mode: str
    intent_fragments: tuple[str, ...]
    actual_capability_ids: tuple[str, ...]
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
    """提取聊天 Trace 的整体意图、全部子任务和已调用 MCP 能力。"""

    trace_id = _identifier(trace.get("traceId"))
    intents: list[str] = []
    _append_unique(intents, trace.get("query"))
    actual_capabilities: set[str] = set()
    malformed_event_count = 0
    out_of_scope_count = 0
    tool_call_count = 0
    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _empty_candidate(
            trace_id=trace_id,
            mode="chat",
            intents=intents,
            normalization_error="invalid_tool_call_queue",
            malformed_event_count=1,
        )

    normalization_error: str | None = None
    for event in raw_queue:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        event_type = event.get("event_type")
        if event_type == "call_sub_agent_begin":
            payload = event.get("request_payload")
            if not isinstance(payload, Mapping):
                malformed_event_count += 1
                continue
            _append_unique(intents, payload.get("taskName"))
            arguments = payload.get("arguments")
            if isinstance(arguments, Mapping):
                _append_unique(intents, arguments.get("query"))
            continue
        if event_type != "tool_call":
            continue
        tool_call_count += 1
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "missing_tool_name"
            continue
        tool_name = _identifier(request.get("toolName"))
        if tool_name is None:
            malformed_event_count += 1
            normalization_error = normalization_error or "missing_tool_name"
            continue
        capability_id = catalog.resolve(tool_name)
        if capability_id is None:
            out_of_scope_count += 1
        else:
            actual_capabilities.add(capability_id)

    if tool_call_count == 0 and _is_pre_routing_failure(trace.get("answer")):
        normalization_error = "pre_routing_failure"
    elif not intents:
        normalization_error = normalization_error or "missing_intent"
    return {
        "trace_id": trace_id,
        "mode": "chat",
        "intent_fragments": tuple(intents),
        "actual_capability_ids": tuple(sorted(actual_capabilities)),
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "duplicate_call_definition_count": 0,
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
    actual_capabilities: set[str] = set()
    malformed_event_count = 0
    out_of_scope_count = 0
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
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
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
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "out_of_scope_tool_call_count": 0,
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
