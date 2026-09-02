"""规范化必填参数完整性，只输出计数而不保留工具参数正文。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypedDict, TypeGuard


class RequiredParameterDiagnostics(TypedDict):
    """不包含参数值的 Schema 覆盖与缺失诊断。"""

    unknown_schema_call_count: int
    zero_required_parameter_call_count: int
    malformed_arguments_call_count: int
    missing_required_parameter_count: int
    duplicate_call_definition_count: int
    malformed_event_count: int


class NormalizedRequiredParameterTrace(TypedDict):
    """供必填参数完整率评测器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    manual_version: str
    total_call_count: int
    schema_covered_call_count: int
    eligible_call_count: int
    required_parameter_count: int
    filled_required_parameter_count: int
    diagnostics: RequiredParameterDiagnostics
    normalization_error: str | None


@dataclass(frozen=True, slots=True)
class RequiredParameterCatalog:
    """版本化工具必填参数目录。"""

    manual_version: str
    required_parameters: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_parameters",
            MappingProxyType(dict(self.required_parameters)),
        )


_PLACEHOLDER_VALUES = frozenset(
    {"null", "none", "undefined", "n/a", "na", "todo", "tbd", "unknown", "待补充", "未知"}
)


def build_required_parameter_catalog(manual: Mapping[str, object]) -> RequiredParameterCatalog:
    """从 MCP 手册构建只读目录，并拒绝会导致口径不确定的重复或缺失定义。"""

    manual_version = _identifier(manual.get("manual_version"))
    raw_tools = manual.get("tools")
    if manual_version is None:
        raise ValueError("manual_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("tools 必须是数组")

    required_by_tool: dict[str, tuple[str, ...]] = {}
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("tools 中每一项都必须是对象")
        tool_name = _identifier(raw_tool.get("name"))
        raw_parameters = raw_tool.get("parameters", ())
        if tool_name is None:
            raise ValueError("工具 name 必须是非空字符串")
        if tool_name in required_by_tool:
            raise ValueError(f"工具 name 重复: {tool_name}")
        if not _is_sequence(raw_parameters):
            raise ValueError(f"工具 {tool_name} 的 parameters 必须是数组")

        seen_parameter_names: set[str] = set()
        required_names: list[str] = []
        for raw_parameter in raw_parameters:
            if not isinstance(raw_parameter, Mapping):
                raise ValueError(f"工具 {tool_name} 的参数定义必须是对象")
            parameter_name = _identifier(raw_parameter.get("name"))
            if parameter_name is None:
                raise ValueError(f"工具 {tool_name} 存在无名参数")
            if parameter_name in seen_parameter_names:
                raise ValueError(f"工具 {tool_name} 的参数重复: {parameter_name}")
            seen_parameter_names.add(parameter_name)
            if raw_parameter.get("required") is True:
                required_names.append(parameter_name)
        required_by_tool[tool_name] = tuple(required_names)

    return RequiredParameterCatalog(manual_version, required_by_tool)


def normalize_chat_required_parameters(
    trace: Mapping[str, object], catalog: RequiredParameterCatalog
) -> NormalizedRequiredParameterTrace:
    """统计聊天模式中 Schema 已覆盖调用的必填参数填充情况。"""

    result = _empty_result(
        trace_id=_identifier(trace.get("traceId")),
        mode="chat",
        manual_version=catalog.manual_version,
    )
    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        result["diagnostics"]["malformed_event_count"] = 1
        result["normalization_error"] = "invalid_tool_call_queue"
        return result

    for event in raw_queue:
        if not isinstance(event, Mapping):
            result["diagnostics"]["malformed_event_count"] += 1
            continue
        if event.get("event_type") != "tool_call":
            continue
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            result["total_call_count"] += 1
            result["diagnostics"]["malformed_event_count"] += 1
            result["normalization_error"] = result["normalization_error"] or "missing_tool_request"
            continue
        _accumulate_call(
            result,
            catalog,
            tool_name=request.get("toolName"),
            arguments=request.get("arguments", _MISSING),
        )
    return result


def normalize_research_required_parameters(
    events: Sequence[object],
    catalog: RequiredParameterCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedRequiredParameterTrace:
    """按 `toolCallId` 去重研究调用后统计，不把 input 正文带出本函数。"""

    resolved_session_id = session_id
    resolved_user_message_id = user_message_id
    definition_digests_by_id: dict[str, str | None] = {}
    malformed_event_count = 0
    duplicate_call_definition_count = 0
    normalization_error: str | None = None
    normalized = _empty_result(
        trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
        mode="research",
        manual_version=catalog.manual_version,
    )

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if resolved_session_id is None:
            resolved_session_id = _identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _identifier(event.get("userMessageID"))
        result_value = event.get("result")
        if not isinstance(result_value, Mapping):
            continue
        raw_calls = result_value.get("toolCalls")
        if not _is_sequence(raw_calls):
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                malformed_event_count += 1
                continue
            call_id = _identifier(raw_call.get("toolCallId"))
            if call_id is None:
                normalization_error = "missing_call_id"
                continue
            tool_name = raw_call.get("toolName")
            arguments = raw_call.get("input", _MISSING)
            definition_digest = _definition_digest(tool_name, arguments)
            if call_id in definition_digests_by_id:
                duplicate_call_definition_count += 1
                if definition_digests_by_id[call_id] != definition_digest:
                    normalization_error = normalization_error or "conflicting_call_definition"
                continue
            definition_digests_by_id[call_id] = definition_digest
            _accumulate_call(normalized, catalog, tool_name=tool_name, arguments=arguments)

    normalized["trace_id"] = _research_trace_id(resolved_session_id, resolved_user_message_id)
    normalized["diagnostics"]["malformed_event_count"] = malformed_event_count
    normalized["diagnostics"]["duplicate_call_definition_count"] = duplicate_call_definition_count
    normalized["normalization_error"] = normalization_error
    return normalized


_MISSING = object()


def _accumulate_call(
    result: NormalizedRequiredParameterTrace,
    catalog: RequiredParameterCatalog,
    *,
    tool_name: object,
    arguments: object,
) -> None:
    result["total_call_count"] += 1
    normalized_tool_name = _identifier(tool_name)
    if normalized_tool_name is None:
        result["diagnostics"]["malformed_event_count"] += 1
        result["normalization_error"] = result["normalization_error"] or "missing_tool_name"
        return

    required_names = catalog.required_parameters.get(normalized_tool_name)
    if required_names is None:
        result["diagnostics"]["unknown_schema_call_count"] += 1
        return
    result["schema_covered_call_count"] += 1
    if not required_names:
        result["diagnostics"]["zero_required_parameter_call_count"] += 1
        return

    result["eligible_call_count"] += 1
    result["required_parameter_count"] += len(required_names)
    parsed_arguments = _parse_arguments(arguments)
    if parsed_arguments is None:
        result["diagnostics"]["malformed_arguments_call_count"] += 1
        result["diagnostics"]["missing_required_parameter_count"] += len(required_names)
        return

    for parameter_name in required_names:
        if parameter_name in parsed_arguments and _is_filled(parsed_arguments[parameter_name]):
            result["filled_required_parameter_count"] += 1
        else:
            result["diagnostics"]["missing_required_parameter_count"] += 1


def _parse_arguments(value: object) -> Mapping[object, object] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _is_filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip()
        return bool(normalized) and normalized.casefold() not in _PLACEHOLDER_VALUES
    if isinstance(value, Mapping):
        return bool(value)
    if _is_sequence(value):
        return bool(value)
    return True


def _definition_digest(tool_name: object, arguments: object) -> str | None:
    try:
        serialized = json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _empty_result(
    *, trace_id: str | None, mode: str, manual_version: str
) -> NormalizedRequiredParameterTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "manual_version": manual_version,
        "total_call_count": 0,
        "schema_covered_call_count": 0,
        "eligible_call_count": 0,
        "required_parameter_count": 0,
        "filled_required_parameter_count": 0,
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "zero_required_parameter_call_count": 0,
            "malformed_arguments_call_count": 0,
            "missing_required_parameter_count": 0,
            "duplicate_call_definition_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }


def _identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
