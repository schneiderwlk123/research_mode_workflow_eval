"""将聊天和研究日志中的工具调用事件规范化为无敏感正文的统一结构。"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, TypeGuard


@dataclass(frozen=True, slots=True)
class ToolOutcomeCondition:
    """用于匹配工具响应中安全状态字段的单条条件。"""

    path: tuple[str, ...]
    accepted_values: tuple[object, ...]

    def __post_init__(self) -> None:
        if not self.path or any(not segment.strip() for segment in self.path):
            raise ValueError("状态字段路径不能为空")
        if not self.accepted_values:
            raise ValueError("状态字段至少需要一个可接受值")


@dataclass(frozen=True, slots=True)
class ToolSuccessRule:
    """单个工具的可版本化业务成功判定规则。"""

    rule_id: str
    success_conditions: tuple[ToolOutcomeCondition, ...] = ()
    failure_conditions: tuple[ToolOutcomeCondition, ...] = ()
    completed_is_success: bool = False

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("成功规则 rule_id 不能为空")
        if (
            not self.success_conditions
            and not self.failure_conditions
            and not self.completed_is_success
        ):
            raise ValueError("成功规则必须包含状态条件或显式 completed 成功策略")


class NormalizedToolCall(TypedDict):
    """一次工具调用的最小事件完整性视图。"""

    call_id: str
    tool_name: str | None
    request_observed: bool
    terminal_observed: bool
    success_observed: bool | None
    success_rule_id: str | None
    source_event_count: int


class ToolCallDiagnostics(TypedDict):
    """不包含原始工具正文的规范化诊断计数。"""

    duplicate_state_event_count: int
    malformed_event_count: int


class NormalizedToolCallTrace(TypedDict):
    """供确定性评测器消费的最小 Trace 切片。"""

    trace_id: str | None
    mode: str
    tool_calls: tuple[NormalizedToolCall, ...]
    diagnostics: ToolCallDiagnostics
    normalization_error: str | None
    success_normalization_error: str | None


_REQUEST_STATUSES = frozenset({"pending", "running"})
_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


def normalize_chat_tool_call_events(
    trace: Mapping[str, object],
    *,
    success_rules: Mapping[str, ToolSuccessRule] | None = None,
) -> NormalizedToolCallTrace:
    """规范化单条聊天 Trace 的工具调用事件。

    当前聊天日志没有跨事件稳定调用标识，因此按 `tool_call_queue` 中工具调用对象的
    出现顺序生成会话内标识。规范化过程只记录字段是否存在，不复制请求或响应正文。
    """

    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        return _empty_trace(
            trace_id=_optional_identifier(trace.get("traceId")),
            mode="chat",
            normalization_error="invalid_tool_call_queue",
            malformed_event_count=1,
        )

    calls: list[NormalizedToolCall] = []
    malformed_event_count = 0
    success_normalization_error: str | None = None
    for event in raw_queue:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if event.get("event_type") != "tool_call":
            continue

        request = event.get("tool_request_args")
        response = event.get("tool_response_result")
        request_tool_name = _mapping_identifier(request, "toolName")
        response_tool_name = _mapping_identifier(response, "toolName")
        if (
            request_tool_name is not None
            and response_tool_name is not None
            and request_tool_name != response_tool_name
        ):
            success_normalization_error = "tool_name_mismatch"

        tool_name = request_tool_name or response_tool_name
        terminal_observed = _has_non_empty_value(event, "tool_response_result")
        success_observed: bool | None = False
        success_rule_id: str | None = None
        if terminal_observed:
            rule = success_rules.get(tool_name) if success_rules is not None and tool_name else None
            success_observed, success_rule_id = _classify_response(
                response,
                rule,
                completed=True,
            )

        call_index = len(calls)
        calls.append(
            {
                "call_id": f"chat-tool-call-{call_index}",
                "tool_name": tool_name,
                "request_observed": _has_non_empty_value(event, "tool_request_args"),
                "terminal_observed": terminal_observed,
                "success_observed": success_observed,
                "success_rule_id": success_rule_id,
                "source_event_count": 1,
            }
        )

    return {
        "trace_id": _optional_identifier(trace.get("traceId")),
        "mode": "chat",
        "tool_calls": tuple(calls),
        "diagnostics": {
            "duplicate_state_event_count": 0,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": None,
        "success_normalization_error": success_normalization_error,
    }


def normalize_research_tool_call_events(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
    success_rules: Mapping[str, ToolSuccessRule] | None = None,
) -> NormalizedToolCallTrace:
    """按 `callID` 合并研究模式工具状态事件。

    缺少 `callID` 时无法判断多条状态是否属于同一调用。严格策略会让整次会话不可
    计算，而不是生成临时标识并冒险放大分母。
    """

    calls: dict[str, NormalizedToolCall] = {}
    observed_statuses: dict[str, set[str]] = {}
    duplicate_state_event_count = 0
    malformed_event_count = 0
    resolved_session_id = session_id
    resolved_user_message_id = user_message_id
    tool_names, tool_name_mismatch = _collect_research_tool_names(events)

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        if resolved_session_id is None:
            resolved_session_id = _optional_identifier(event.get("sessionID"))
        if resolved_user_message_id is None:
            resolved_user_message_id = _optional_identifier(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue

        part = event.get("part")
        if not isinstance(part, Mapping):
            malformed_event_count += 1
            continue
        if part.get("type") != "tool":
            continue

        call_id = _required_identifier(part.get("callID"))
        if call_id is None:
            return _empty_trace(
                trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
                mode="research",
                normalization_error="missing_call_id",
                malformed_event_count=malformed_event_count + 1,
                duplicate_state_event_count=duplicate_state_event_count,
            )

        call = calls.setdefault(
            call_id,
            {
                "call_id": call_id,
                "tool_name": tool_names.get(call_id),
                "request_observed": False,
                "terminal_observed": False,
                "success_observed": None,
                "success_rule_id": None,
                "source_event_count": 0,
            },
        )
        call["source_event_count"] += 1

        state = part.get("state")
        if not isinstance(state, Mapping):
            malformed_event_count += 1
            continue
        status_value = state.get("status")
        if not isinstance(status_value, str) or not status_value.strip():
            malformed_event_count += 1
            continue
        status = status_value.strip().lower()

        statuses = observed_statuses.setdefault(call_id, set())
        if status in statuses:
            duplicate_state_event_count += 1
        else:
            statuses.add(status)

        if status in _REQUEST_STATUSES:
            call["request_observed"] = True
        if status in _TERMINAL_STATUSES:
            call["terminal_observed"] = True
        if status in _TERMINAL_STATUSES - {"completed"}:
            call["success_observed"] = False
            call["success_rule_id"] = None
        elif status == "completed" and call["success_observed"] is not False:
            tool_name = call["tool_name"]
            rule = success_rules.get(tool_name) if success_rules is not None and tool_name else None
            success_observed, success_rule_id = _classify_response(
                state,
                rule,
                completed=True,
            )
            if success_observed is False or call["success_observed"] is None:
                call["success_observed"] = success_observed
                call["success_rule_id"] = success_rule_id

    for call in calls.values():
        if call["terminal_observed"] is False:
            call["success_observed"] = False
            call["success_rule_id"] = None

    return {
        "trace_id": _research_trace_id(resolved_session_id, resolved_user_message_id),
        "mode": "research",
        "tool_calls": tuple(calls.values()),
        "diagnostics": {
            "duplicate_state_event_count": duplicate_state_event_count,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": None,
        "success_normalization_error": "tool_name_mismatch" if tool_name_mismatch else None,
    }


def _empty_trace(
    *,
    trace_id: str | None,
    mode: str,
    normalization_error: str,
    malformed_event_count: int,
    duplicate_state_event_count: int = 0,
) -> NormalizedToolCallTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "tool_calls": (),
        "diagnostics": {
            "duplicate_state_event_count": duplicate_state_event_count,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": normalization_error,
        "success_normalization_error": None,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _optional_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_identifier(value: object) -> str | None:
    return _optional_identifier(value)


def _mapping_identifier(value: object, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _optional_identifier(value.get(key))


def _collect_research_tool_names(
    events: Sequence[object],
) -> tuple[dict[str, str], bool]:
    """从模型工具请求中恢复 `callID -> toolName`，不复制参数正文。"""

    tool_names: dict[str, str] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        result = event.get("result")
        if not isinstance(result, Mapping):
            continue
        raw_tool_calls = result.get("toolCalls")
        if not _is_sequence(raw_tool_calls):
            continue
        for raw_tool_call in raw_tool_calls:
            if not isinstance(raw_tool_call, Mapping):
                continue
            call_id = _optional_identifier(raw_tool_call.get("toolCallId"))
            tool_name = _optional_identifier(raw_tool_call.get("toolName"))
            if call_id is None or tool_name is None:
                continue
            existing_name = tool_names.get(call_id)
            if existing_name is not None and existing_name != tool_name:
                return tool_names, True
            tool_names[call_id] = tool_name
    return tool_names, False


def _classify_response(
    response: object,
    rule: ToolSuccessRule | None,
    *,
    completed: bool,
) -> tuple[bool | None, str | None]:
    """应用显式规则判定响应，只返回布尔结果和规则版本。"""

    if rule is None:
        return None, None
    if any(_condition_matches(response, condition) for condition in rule.failure_conditions):
        return False, rule.rule_id
    if any(_condition_matches(response, condition) for condition in rule.success_conditions):
        return True, rule.rule_id
    if completed and rule.completed_is_success:
        return True, rule.rule_id
    return None, rule.rule_id


def _condition_matches(response: object, condition: ToolOutcomeCondition) -> bool:
    found, actual = _resolve_path(response, condition.path)
    if not found:
        return False
    return any(_same_scalar(actual, expected) for expected in condition.accepted_values)


def _resolve_path(value: object, path: tuple[str, ...]) -> tuple[bool, object]:
    current = value
    for segment in path:
        current = _decode_json_mapping(current)
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _decode_json_mapping(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded


def _same_scalar(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().casefold() == expected.strip().casefold()
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def _has_non_empty_value(mapping: Mapping[object, object], key: str) -> bool:
    if key not in mapping:
        return False
    value = mapping[key]
    if value is None:
        return False
    if isinstance(value, (str, bytes, Mapping, Sequence)):
        return len(value) > 0
    return True


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
