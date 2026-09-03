"""为参数值语义评测构造仅在内存中存在的 Judge 候选。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypedDict, TypeGuard


class ParameterValueDiagnostics(TypedDict):
    """不包含 query 或参数值的候选构造诊断。"""

    unknown_schema_call_count: int
    malformed_arguments_call_count: int
    duplicate_call_definition_count: int
    malformed_event_count: int


@dataclass(frozen=True, slots=True)
class ParameterValueToolDefinition:
    """Judge 理解一个工具及其参数语义所需的最小手册切片。"""

    name: str
    description: str
    parameter_descriptions: Mapping[str, str]
    schema_field_paths: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameter_descriptions",
            MappingProxyType(dict(self.parameter_descriptions)),
        )


@dataclass(frozen=True, slots=True)
class ParameterValueCatalog:
    """版本化、只读的工具语义目录。"""

    manual_version: str
    tools: Mapping[str, ParameterValueToolDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))


@dataclass(frozen=True, slots=True)
class ParameterValueCandidate:
    """传给 Judge 的临时候选；不得持久化 query 或 arguments。"""

    call_id: str
    query: str
    tool_name: str
    tool_description: str
    parameter_descriptions: Mapping[str, str]
    schema_field_paths: frozenset[str]
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parameter_descriptions",
            MappingProxyType(dict(self.parameter_descriptions)),
        )
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


class NormalizedParameterValueTrace(TypedDict):
    """包含临时 Judge 候选和安全诊断的单 Trace 规范化结果。"""

    trace_id: str | None
    mode: str
    manual_version: str
    total_call_count: int
    eligible_call_count: int
    candidates: tuple[ParameterValueCandidate, ...]
    diagnostics: ParameterValueDiagnostics
    normalization_error: str | None


def build_parameter_value_catalog(manual: Mapping[str, object]) -> ParameterValueCatalog:
    """从 MCP 手册构建字段描述目录，不推断描述文本中的业务规则。"""

    manual_version = _identifier(manual.get("manual_version"))
    raw_tools = manual.get("tools")
    if manual_version is None:
        raise ValueError("manual_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("tools 必须是数组")

    tools: dict[str, ParameterValueToolDefinition] = {}
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("tools 中每一项都必须是对象")
        name = _identifier(raw_tool.get("name"))
        description = _identifier(raw_tool.get("description"))
        if name is None:
            raise ValueError("工具 name 必须是非空字符串")
        if description is None:
            raise ValueError(f"工具 {name} 的 description 必须是非空字符串")
        if name in tools:
            raise ValueError(f"工具 name 重复: {name}")

        parameter_descriptions: dict[str, str] = {}
        _collect_parameter_descriptions(
            raw_tool.get("parameters", ()),
            parameter_descriptions,
            context=f"工具 {name}",
        )
        tools[name] = ParameterValueToolDefinition(
            name=name,
            description=description,
            parameter_descriptions=parameter_descriptions,
            schema_field_paths=frozenset(parameter_descriptions),
        )
    return ParameterValueCatalog(manual_version=manual_version, tools=tools)


def normalize_chat_parameter_value_candidates(
    trace: Mapping[str, object], catalog: ParameterValueCatalog
) -> NormalizedParameterValueTrace:
    """提取聊天调用候选；原始 query 与参数仅随返回对象在内存中流转。"""

    normalized = _empty_result(
        trace_id=_identifier(trace.get("traceId")),
        mode="chat",
        manual_version=catalog.manual_version,
    )
    query = _identifier(trace.get("query"))
    if query is None:
        normalized["normalization_error"] = "missing_query"
        return normalized

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        normalized["diagnostics"]["malformed_event_count"] = 1
        normalized["normalization_error"] = "invalid_tool_call_queue"
        return normalized

    seen_definitions: dict[str, str] = {}
    candidates: list[ParameterValueCandidate] = []
    for event in raw_queue:
        if not isinstance(event, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        if event.get("event_type") != "tool_call":
            continue
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = (
                normalized["normalization_error"] or "missing_tool_request"
            )
            continue
        _accumulate_candidate(
            normalized,
            candidates,
            seen_definitions,
            catalog,
            query=query,
            call_id=request.get("toolId"),
            tool_name=request.get("toolName"),
            arguments=request.get("arguments", _MISSING),
        )
    normalized["candidates"] = tuple(candidates)
    return normalized


def normalize_research_parameter_value_candidates(
    events: Sequence[object],
    catalog: ParameterValueCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedParameterValueTrace:
    """按 toolCallId 去重研究调用，并使用原始用户 query 构造候选。"""

    resolved_session_id = session_id
    resolved_user_message_id = user_message_id
    query = _research_query(events)
    normalized = _empty_result(
        trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
        mode="research",
        manual_version=catalog.manual_version,
    )
    if query is None:
        normalized["normalization_error"] = "missing_query"
        return normalized

    seen_definitions: dict[str, str] = {}
    candidates: list[ParameterValueCandidate] = []
    for event in events:
        if not isinstance(event, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        if resolved_session_id is None:
            resolved_session_id = _identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _identifier(event.get("userMessageID"))
        result = event.get("result")
        if not isinstance(result, Mapping):
            continue
        raw_calls = result.get("toolCalls")
        if not _is_sequence(raw_calls):
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                normalized["diagnostics"]["malformed_event_count"] += 1
                continue
            _accumulate_candidate(
                normalized,
                candidates,
                seen_definitions,
                catalog,
                query=query,
                call_id=raw_call.get("toolCallId"),
                tool_name=raw_call.get("toolName"),
                arguments=raw_call.get("input", _MISSING),
            )

    normalized["trace_id"] = _research_trace_id(resolved_session_id, resolved_user_message_id)
    normalized["candidates"] = tuple(candidates)
    return normalized


def _accumulate_candidate(
    normalized: NormalizedParameterValueTrace,
    candidates: list[ParameterValueCandidate],
    seen_definitions: dict[str, str],
    catalog: ParameterValueCatalog,
    *,
    query: str,
    call_id: object,
    tool_name: object,
    arguments: object,
) -> None:
    normalized_call_id = _identifier(call_id)
    normalized_tool_name = _identifier(tool_name)
    if normalized_call_id is None:
        normalized["diagnostics"]["malformed_event_count"] += 1
        normalized["normalization_error"] = normalized["normalization_error"] or "missing_call_id"
        return
    if normalized_tool_name is None:
        normalized["diagnostics"]["malformed_event_count"] += 1
        normalized["normalization_error"] = normalized["normalization_error"] or "missing_tool_name"
        return

    parsed_arguments = _parse_arguments(arguments)
    if parsed_arguments is None:
        normalized["diagnostics"]["malformed_arguments_call_count"] += 1
        definition_digest = _definition_digest(normalized_tool_name, {"__malformed__": True})
    else:
        definition_digest = _definition_digest(normalized_tool_name, parsed_arguments)
    if definition_digest is None:
        normalized["diagnostics"]["malformed_event_count"] += 1
        return

    previous_digest = seen_definitions.get(normalized_call_id)
    if previous_digest is not None:
        if previous_digest != definition_digest:
            normalized["normalization_error"] = (
                normalized["normalization_error"] or "conflicting_call_definition"
            )
        else:
            normalized["diagnostics"]["duplicate_call_definition_count"] += 1
        return

    seen_definitions[normalized_call_id] = definition_digest
    normalized["total_call_count"] += 1
    tool = catalog.tools.get(normalized_tool_name)
    if tool is None:
        normalized["diagnostics"]["unknown_schema_call_count"] += 1
        return
    if parsed_arguments is None:
        return

    candidates.append(
        ParameterValueCandidate(
            call_id=normalized_call_id,
            query=query,
            tool_name=normalized_tool_name,
            tool_description=tool.description,
            parameter_descriptions=tool.parameter_descriptions,
            schema_field_paths=tool.schema_field_paths,
            arguments=parsed_arguments,
        )
    )
    normalized["eligible_call_count"] += 1


def _collect_parameter_descriptions(
    raw_parameters: object,
    destination: dict[str, str],
    *,
    context: str,
    prefix: str = "",
) -> None:
    if not _is_sequence(raw_parameters):
        raise ValueError(f"{context} 的 parameters/nested 必须是数组")
    for raw_parameter in raw_parameters:
        if not isinstance(raw_parameter, Mapping):
            raise ValueError(f"{context} 的参数必须是对象")
        name = _identifier(raw_parameter.get("name"))
        description = _identifier(raw_parameter.get("description"))
        parameter_type = _identifier(raw_parameter.get("type"))
        if name is None or description is None or parameter_type is None:
            raise ValueError(f"{context} 的参数 name、type、description 必须是非空字符串")
        field_path = f"{prefix}.{name}" if prefix else name
        nested = raw_parameter.get("nested")
        if parameter_type == "object" and _is_sequence(nested) and nested:
            _collect_parameter_descriptions(
                nested,
                destination,
                context=f"{context} 参数 {field_path}",
                prefix=field_path,
            )
        else:
            destination[field_path] = description


def _research_query(events: Sequence[object]) -> str | None:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        direct = _identifier(event.get("query"))
        if direct is not None:
            return direct
    for event in events:
        if not isinstance(event, Mapping):
            continue
        body = event.get("body")
        if isinstance(body, Mapping):
            nested = _identifier(body.get("query"))
            if nested is not None:
                return nested
    return None


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id is None or user_message_id is None:
        return None
    return f"{session_id}:{user_message_id}"


def _definition_digest(tool_name: str, arguments: object) -> str | None:
    try:
        canonical = json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_arguments(arguments: object) -> dict[str, object] | None:
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(parsed, Mapping):
        return None
    return {str(key): value for key, value in parsed.items()}


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze(value_item) for key, value_item in value.items()})


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping({str(key): item for key, item in value.items()})
    if _is_sequence(value):
        return tuple(_freeze(item) for item in value)
    return value


def _identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


_MISSING = object()


def _empty_result(
    *, trace_id: str | None, mode: str, manual_version: str
) -> NormalizedParameterValueTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "manual_version": manual_version,
        "total_call_count": 0,
        "eligible_call_count": 0,
        "candidates": (),
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "malformed_arguments_call_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
