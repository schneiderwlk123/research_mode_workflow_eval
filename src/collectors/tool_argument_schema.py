"""校验工具参数的类型、枚举与嵌套结构，只输出安全聚合计数。"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypedDict, TypeGuard


class ToolArgumentSchemaDiagnostics(TypedDict):
    """调用级 Schema 违规分类；同一非法调用可命中多个分类。"""

    unknown_schema_call_count: int
    malformed_arguments_call_count: int
    type_violation_call_count: int
    enum_violation_call_count: int
    missing_nested_required_call_count: int
    undeclared_argument_call_count: int
    duplicate_call_definition_count: int
    malformed_event_count: int


class NormalizedToolArgumentSchemaTrace(TypedDict):
    """供 Schema 合法率评测器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    manual_version: str
    total_call_count: int
    schema_covered_call_count: int
    schema_valid_call_count: int
    schema_invalid_call_count: int
    diagnostics: ToolArgumentSchemaDiagnostics
    normalization_error: str | None


@dataclass(frozen=True, slots=True)
class ParameterSchema:
    """手册中一个可递归参数字段的可执行结构约束。"""

    name: str
    parameter_type: str
    required: bool
    enum_values: tuple[object, ...]
    nested: tuple["ParameterSchema", ...]


@dataclass(frozen=True, slots=True)
class ToolArgumentSchemaCatalog:
    """版本化、只读的工具参数 Schema 目录。"""

    manual_version: str
    schemas: Mapping[str, tuple[ParameterSchema, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schemas", MappingProxyType(dict(self.schemas)))


@dataclass(frozen=True, slots=True)
class _ValidationOutcome:
    violations: frozenset[str]
    undeclared_argument_observed: bool


_SUPPORTED_TYPES = frozenset(
    {
        "array",
        "boolean",
        "integer",
        "integer[]",
        "number",
        "number[]",
        "object",
        "object[]",
        "string",
        "string[]",
    }
)


def build_tool_argument_schema_catalog(
    manual: Mapping[str, object],
) -> ToolArgumentSchemaCatalog:
    """把手册的 `type/enum/nested/required` 转换为可执行的递归约束。"""

    manual_version = _identifier(manual.get("manual_version"))
    raw_tools = manual.get("tools")
    if manual_version is None:
        raise ValueError("manual_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("tools 必须是数组")

    schemas: dict[str, tuple[ParameterSchema, ...]] = {}
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("tools 中每一项都必须是对象")
        tool_name = _identifier(raw_tool.get("name"))
        if tool_name is None:
            raise ValueError("工具 name 必须是非空字符串")
        if tool_name in schemas:
            raise ValueError(f"工具 name 重复: {tool_name}")
        schemas[tool_name] = _parse_parameter_list(
            raw_tool.get("parameters", ()), context=f"工具 {tool_name}"
        )
    return ToolArgumentSchemaCatalog(manual_version, schemas)


def normalize_chat_tool_argument_schema(
    trace: Mapping[str, object], catalog: ToolArgumentSchemaCatalog
) -> NormalizedToolArgumentSchemaTrace:
    """对聊天工具调用执行 Schema 校验，未知工具只进入覆盖率诊断。"""

    normalized = _empty_result(
        trace_id=_identifier(trace.get("traceId")),
        mode="chat",
        manual_version=catalog.manual_version,
    )
    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        normalized["diagnostics"]["malformed_event_count"] = 1
        normalized["normalization_error"] = "invalid_tool_call_queue"
        return normalized

    for event in raw_queue:
        if not isinstance(event, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            continue
        if event.get("event_type") != "tool_call":
            continue
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping):
            normalized["total_call_count"] += 1
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = (
                normalized["normalization_error"] or "missing_tool_request"
            )
            continue
        _accumulate_call(
            normalized,
            catalog,
            tool_name=request.get("toolName"),
            arguments=request.get("arguments", _MISSING),
        )
    return normalized


def normalize_research_tool_argument_schema(
    events: Sequence[object],
    catalog: ToolArgumentSchemaCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolArgumentSchemaTrace:
    """按 `toolCallId` 去重研究调用，并仅长期保留定义摘要。"""

    resolved_session_id = session_id
    resolved_user_message_id = user_message_id
    definition_digests_by_id: dict[str, str | None] = {}
    duplicate_call_definition_count = 0
    malformed_event_count = 0
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
    normalized["diagnostics"]["duplicate_call_definition_count"] = duplicate_call_definition_count
    normalized["diagnostics"]["malformed_event_count"] = malformed_event_count
    normalized["normalization_error"] = normalization_error
    return normalized


_MISSING = object()


def _parse_parameter_list(raw_fields: object, *, context: str) -> tuple[ParameterSchema, ...]:
    if not _is_sequence(raw_fields):
        raise ValueError(f"{context} 的参数定义必须是数组")
    parsed: list[ParameterSchema] = []
    seen_names: set[str] = set()
    for raw_field in raw_fields:
        if not isinstance(raw_field, Mapping):
            raise ValueError(f"{context} 的参数定义必须是对象")
        name = _identifier(raw_field.get("name"))
        parameter_type = _identifier(raw_field.get("type"))
        if name is None:
            raise ValueError(f"{context} 存在无名参数")
        if name in seen_names:
            raise ValueError(f"{context} 的参数重复: {name}")
        if parameter_type not in _SUPPORTED_TYPES:
            raise ValueError(f"{context}.{name} 使用不支持的参数类型: {parameter_type}")
        seen_names.add(name)

        raw_enum = raw_field.get("enum", ())
        if raw_enum is None:
            raw_enum = ()
        if not _is_sequence(raw_enum):
            raise ValueError(f"{context}.{name} 的 enum 必须是数组")
        raw_nested = raw_field.get("nested", ())
        if raw_nested is None:
            raw_nested = ()
        nested = _parse_parameter_list(raw_nested, context=f"{context}.{name}")
        if nested and parameter_type not in {"object", "object[]"}:
            raise ValueError(f"{context}.{name} 只有 object/object[] 可以声明 nested")
        parsed.append(
            ParameterSchema(
                name=name,
                parameter_type=parameter_type,
                required=raw_field.get("required") is True,
                enum_values=tuple(raw_enum),
                nested=nested,
            )
        )
    return tuple(parsed)


def _accumulate_call(
    normalized: NormalizedToolArgumentSchemaTrace,
    catalog: ToolArgumentSchemaCatalog,
    *,
    tool_name: object,
    arguments: object,
) -> None:
    normalized["total_call_count"] += 1
    normalized_tool_name = _identifier(tool_name)
    if normalized_tool_name is None:
        normalized["diagnostics"]["malformed_event_count"] += 1
        normalized["normalization_error"] = normalized["normalization_error"] or "missing_tool_name"
        return
    schema = catalog.schemas.get(normalized_tool_name)
    if schema is None:
        normalized["diagnostics"]["unknown_schema_call_count"] += 1
        return

    normalized["schema_covered_call_count"] += 1
    parsed_arguments = _parse_arguments(arguments)
    if parsed_arguments is None:
        normalized["schema_invalid_call_count"] += 1
        normalized["diagnostics"]["malformed_arguments_call_count"] += 1
        return

    outcome = _validate_object(parsed_arguments, schema, depth=0)
    if outcome.undeclared_argument_observed:
        normalized["diagnostics"]["undeclared_argument_call_count"] += 1
    if not outcome.violations:
        normalized["schema_valid_call_count"] += 1
        return

    normalized["schema_invalid_call_count"] += 1
    if "type_violation" in outcome.violations:
        normalized["diagnostics"]["type_violation_call_count"] += 1
    if "enum_violation" in outcome.violations:
        normalized["diagnostics"]["enum_violation_call_count"] += 1
    if "missing_nested_required" in outcome.violations:
        normalized["diagnostics"]["missing_nested_required_call_count"] += 1


def _validate_object(
    value: Mapping[object, object], fields: tuple[ParameterSchema, ...], *, depth: int
) -> _ValidationOutcome:
    violations: set[str] = set()
    declared_names = {field.name for field in fields}
    undeclared = any(key not in declared_names for key in value)
    for field in fields:
        if field.name not in value:
            if depth > 0 and field.required:
                violations.add("missing_nested_required")
            continue
        field_value = value[field.name]
        if not _matches_type(field_value, field.parameter_type):
            violations.add("type_violation")
            continue
        if field.enum_values and not _matches_enum(
            field_value, field.parameter_type, field.enum_values
        ):
            violations.add("enum_violation")
        if field.nested and field.parameter_type == "object" and isinstance(field_value, Mapping):
            nested = _validate_object(field_value, field.nested, depth=depth + 1)
            violations.update(nested.violations)
            undeclared = undeclared or nested.undeclared_argument_observed
        elif field.nested and field.parameter_type == "object[]" and _is_sequence(field_value):
            for item in field_value:
                if not isinstance(item, Mapping):
                    continue
                nested = _validate_object(item, field.nested, depth=depth + 1)
                violations.update(nested.violations)
                undeclared = undeclared or nested.undeclared_argument_observed
    return _ValidationOutcome(frozenset(violations), undeclared)


def _matches_type(value: object, parameter_type: str) -> bool:
    if parameter_type == "string":
        return isinstance(value, str)
    if parameter_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if parameter_type == "number":
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    if parameter_type == "boolean":
        return isinstance(value, bool)
    if parameter_type == "object":
        return isinstance(value, Mapping)
    if parameter_type == "array":
        return _is_sequence(value)
    if parameter_type == "string[]":
        return _is_sequence(value) and all(isinstance(item, str) for item in value)
    if parameter_type == "integer[]":
        return _is_sequence(value) and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        )
    if parameter_type == "number[]":
        return _is_sequence(value) and all(
            isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
            for item in value
        )
    if parameter_type == "object[]":
        return _is_sequence(value) and all(isinstance(item, Mapping) for item in value)
    return False


def _matches_enum(value: object, parameter_type: str, enum_values: tuple[object, ...]) -> bool:
    if parameter_type in {"string[]", "integer[]", "number[]"}:
        if not _is_sequence(value):
            return False
        return all(any(item == candidate for candidate in enum_values) for item in value)
    return any(value == candidate for candidate in enum_values)


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
) -> NormalizedToolArgumentSchemaTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "manual_version": manual_version,
        "total_call_count": 0,
        "schema_covered_call_count": 0,
        "schema_valid_call_count": 0,
        "schema_invalid_call_count": 0,
        "diagnostics": {
            "unknown_schema_call_count": 0,
            "malformed_arguments_call_count": 0,
            "type_violation_call_count": 0,
            "enum_violation_call_count": 0,
            "missing_nested_required_call_count": 0,
            "undeclared_argument_call_count": 0,
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
