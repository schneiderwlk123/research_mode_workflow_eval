"""按版本化工具 SLA 规范化聊天和研究模式的超时判定。"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal, TypedDict, TypeGuard

TimeoutReason = Literal["explicit_timeout", "sla_exceeded", "missing_timestamps"]


@dataclass(frozen=True, slots=True)
class ToolTimeoutPolicyCatalog:
    """工具名称、别名和毫秒级服务时限的只读版本化目录。"""

    policy_version: str
    timeout_ms_by_tool: Mapping[str, int]
    aliases: Mapping[str, str]
    explicit_timeout_statuses: frozenset[str]

    def resolve(self, tool_name: object) -> tuple[str, int] | None:
        """将两种模式中的工具名解析为稳定名称及其 SLA。"""

        normalized = _text(tool_name)
        if normalized is None:
            return None
        canonical = self.aliases.get(normalized)
        if canonical is None and normalized.startswith("mcp__"):
            canonical = self.aliases.get(normalized.rsplit("__", 1)[-1])
        if canonical is None:
            return None
        return canonical, self.timeout_ms_by_tool[canonical]


class NormalizedTimeoutCall(TypedDict):
    """单次调用的安全超时判定，不包含工具参数或响应正文。"""

    call_id: str
    timeout_reason: TimeoutReason | None


class ToolTimeoutDiagnostics(TypedDict):
    """可聚合但不泄露调用正文的诊断计数。"""

    explicit_timeout_count: int
    sla_exceeded_count: int
    missing_time_timeout_count: int
    duplicate_state_event_count: int
    malformed_event_count: int


class NormalizedToolTimeoutTrace(TypedDict):
    """供超时率评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: str
    policy_version: str
    tool_calls: tuple[NormalizedTimeoutCall, ...]
    diagnostics: ToolTimeoutDiagnostics
    normalization_error: str | None


_DEFAULT_TIMEOUT_STATUSES = frozenset({"timeout", "timed_out"})
_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


def build_tool_timeout_policy_catalog(
    policy: Mapping[str, object],
) -> ToolTimeoutPolicyCatalog:
    """校验并构建工具 SLA 目录；无效策略在运行前直接失败。"""

    policy_version = _text(policy.get("policy_version"))
    raw_tools = policy.get("tools")
    if policy_version is None:
        raise ValueError("超时策略 policy_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("超时策略 tools 必须是数组")

    raw_statuses = policy.get("explicit_timeout_statuses", tuple(_DEFAULT_TIMEOUT_STATUSES))
    if not _is_sequence(raw_statuses):
        raise ValueError("explicit_timeout_statuses 必须是数组")
    timeout_statuses: set[str] = set()
    for raw_status in raw_statuses:
        status = _normalized_status(raw_status)
        if status is None:
            raise ValueError("显式超时状态必须是非空字符串")
        timeout_statuses.add(status)
    if not timeout_statuses:
        raise ValueError("显式超时状态集合不能为空")

    timeouts: dict[str, int] = {}
    aliases: dict[str, str] = {}
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("工具超时策略必须是对象")
        name = _text(raw_tool.get("name"))
        timeout_ms = raw_tool.get("timeout_ms")
        if name is None:
            raise ValueError("工具超时策略 name 必须是非空字符串")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise ValueError(f"工具 {name} 的 timeout_ms 必须是正整数")
        if name in timeouts:
            raise ValueError(f"工具超时策略重复: {name}")
        existing_name = aliases.get(name)
        if existing_name is not None and existing_name != name:
            raise ValueError(f"工具超时策略别名冲突: {name}")
        timeouts[name] = timeout_ms
        aliases[name] = name

        raw_aliases = raw_tool.get("aliases", ())
        if not _is_sequence(raw_aliases):
            raise ValueError(f"工具 {name} 的 aliases 必须是数组")
        for raw_alias in raw_aliases:
            alias = _text(raw_alias)
            if alias is None:
                raise ValueError(f"工具 {name} 的别名必须是非空字符串")
            existing = aliases.get(alias)
            if existing is not None and existing != name:
                raise ValueError(f"工具超时策略别名冲突: {alias}")
            aliases[alias] = name

    if not timeouts:
        raise ValueError("超时策略至少需要一个工具")
    return ToolTimeoutPolicyCatalog(
        policy_version=policy_version,
        timeout_ms_by_tool=MappingProxyType(timeouts),
        aliases=MappingProxyType(aliases),
        explicit_timeout_statuses=frozenset(timeout_statuses),
    )


def normalize_chat_tool_timeout_rate(
    trace: Mapping[str, object], catalog: ToolTimeoutPolicyCatalog
) -> NormalizedToolTimeoutTrace:
    """按新版 canvas 关联普通工具请求与结果，并依据结果耗时判定超时。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty(None, "chat", catalog, "invalid_chat_trace_structure", malformed=1)
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty(None, "chat", catalog, "invalid_chat_trace_structure", malformed=1)
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _empty(None, "chat", catalog, "missing_trace_id", malformed=1)
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _empty(trace_id, "chat", catalog, "invalid_chat_trace_structure", malformed=1)

    definitions: dict[str, tuple[str, int]] = {}
    results: dict[str, list[Mapping[object, object]]] = {}
    duplicate_count = 0
    malformed_count = 0
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            malformed_count += 1
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            malformed_count += 1
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                malformed_count += 1
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                malformed_count += 1
                continue

            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                malformed_count += 1
            else:
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_count += 1
                        continue
                    call_id = _text(raw_call.get("toolId"))
                    agent_name = _text(raw_call.get("agentName"))
                    if call_id is None and agent_name is not None:
                        continue
                    if call_id is None:
                        return _empty(
                            trace_id,
                            "chat",
                            catalog,
                            "missing_call_id",
                            malformed=malformed_count + 1,
                            duplicate=duplicate_count,
                        )
                    resolved = catalog.resolve(raw_call.get("toolName"))
                    if resolved is None:
                        return _empty(
                            trace_id,
                            "chat",
                            catalog,
                            "missing_tool_sla",
                            malformed=malformed_count,
                            duplicate=duplicate_count,
                        )
                    existing = definitions.get(call_id)
                    if existing is not None:
                        duplicate_count += 1
                        if existing[0] != resolved[0]:
                            return _empty(
                                trace_id,
                                "chat",
                                catalog,
                                "conflicting_call_definition",
                                malformed=malformed_count,
                                duplicate=duplicate_count,
                            )
                        continue
                    definitions[call_id] = resolved

            raw_results = content.get("tool_result", ())
            if raw_results is None:
                raw_results = ()
            if not _is_sequence(raw_results):
                malformed_count += 1
            else:
                for raw_result in raw_results:
                    if not isinstance(raw_result, Mapping):
                        malformed_count += 1
                        continue
                    call_id = _text(raw_result.get("toolId"))
                    if call_id is None:
                        return _empty(
                            trace_id,
                            "chat",
                            catalog,
                            "missing_call_id",
                            malformed=malformed_count + 1,
                            duplicate=duplicate_count,
                        )
                    observed = results.setdefault(call_id, [])
                    if observed:
                        duplicate_count += 1
                    observed.append(raw_result)

    if malformed_count:
        return _empty(
            trace_id,
            "chat",
            catalog,
            "invalid_chat_trace_structure",
            malformed=malformed_count,
            duplicate=duplicate_count,
        )

    calls: list[NormalizedTimeoutCall] = []
    for call_id, (canonical_name, timeout_ms) in definitions.items():
        observations = results.get(call_id, [])
        for response in observations:
            response_name = _text(response.get("toolName"))
            if response_name is None:
                continue
            response_policy = catalog.resolve(response_name)
            if response_policy is None:
                return _empty(
                    trace_id,
                    "chat",
                    catalog,
                    "missing_tool_sla",
                    duplicate=duplicate_count,
                )
            if response_policy[0] != canonical_name:
                return _empty(
                    trace_id,
                    "chat",
                    catalog,
                    "conflicting_call_definition",
                    duplicate=duplicate_count,
                )

        if any(_chat_explicit_timeout(response, catalog) for response in observations):
            calls.append({"call_id": call_id, "timeout_reason": "explicit_timeout"})
            continue

        duration_values: list[int] = []
        for response in observations:
            duration_status, duration_ms = _duration_ms(response.get("durationSeconds"))
            if duration_status == "invalid":
                return _empty(
                    trace_id,
                    "chat",
                    catalog,
                    "invalid_timestamp",
                    duplicate=duplicate_count,
                )
            if duration_status == "negative":
                return _empty(
                    trace_id,
                    "chat",
                    catalog,
                    "invalid_event_order",
                    duplicate=duplicate_count,
                )
            if duration_ms is not None:
                duration_values.append(duration_ms)
        if not duration_values:
            calls.append({"call_id": call_id, "timeout_reason": "missing_timestamps"})
            continue
        reason: TimeoutReason | None = "sla_exceeded" if max(duration_values) > timeout_ms else None
        calls.append({"call_id": call_id, "timeout_reason": reason})

    return _finalize(
        trace_id,
        "chat",
        catalog,
        calls,
        malformed=malformed_count,
        duplicate=duplicate_count,
    )


def normalize_research_tool_timeout_rate(
    events: Sequence[object],
    catalog: ToolTimeoutPolicyCatalog,
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedToolTimeoutTrace:
    """按 callID 合并研究工具生命周期并使用最早开始、终态结束时间。"""

    resolved_session_id = _text(session_id)
    resolved_user_message_id = _text(user_message_id)
    definitions: dict[str, str] = {}
    observed_call_ids: dict[str, None] = {}
    starts: dict[str, list[int]] = {}
    ends: dict[str, list[int]] = {}
    statuses: dict[str, set[str]] = {}
    explicit_timeouts: set[str] = set()
    duplicate_count = 0
    malformed_count = 0

    for event in events:
        if not isinstance(event, Mapping):
            malformed_count += 1
            continue
        resolved_session_id = resolved_session_id or _text(event.get("sessionID"))
        resolved_user_message_id = resolved_user_message_id or _text(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        call_id = _text(part.get("callID"))
        if call_id is None:
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                catalog,
                "missing_call_id",
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        observed_call_ids.setdefault(call_id, None)
        tool_name = _text(part.get("tool"))
        resolved = catalog.resolve(tool_name) if tool_name is not None else None
        previous = definitions.get(call_id)
        if tool_name is not None and resolved is None:
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                catalog,
                "missing_tool_sla",
                malformed=malformed_count,
                duplicate=duplicate_count,
            )
        canonical_name = resolved[0] if resolved is not None else previous
        if previous is not None and canonical_name is not None and previous != canonical_name:
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                catalog,
                "conflicting_call_definition",
                malformed=malformed_count,
                duplicate=duplicate_count,
            )
        if canonical_name is not None:
            definitions[call_id] = canonical_name

        state = part.get("state")
        if not isinstance(state, Mapping):
            malformed_count += 1
            continue
        status = _normalized_status(state.get("status"))
        if status is None:
            malformed_count += 1
            continue
        observed = statuses.setdefault(call_id, set())
        if status in observed:
            duplicate_count += 1
        else:
            observed.add(status)
        if status in catalog.explicit_timeout_statuses:
            explicit_timeouts.add(call_id)

        raw_time = state.get("time")
        if raw_time is None:
            continue
        if not isinstance(raw_time, Mapping):
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                catalog,
                "invalid_timestamp",
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        start_status, start_ms = _timestamp_ms(raw_time.get("start"))
        if start_status == "invalid":
            return _empty(
                _research_trace_id(resolved_session_id, resolved_user_message_id),
                "research",
                catalog,
                "invalid_timestamp",
                malformed=malformed_count + 1,
                duplicate=duplicate_count,
            )
        if start_ms is not None:
            starts.setdefault(call_id, []).append(start_ms)
        if status in _TERMINAL_STATUSES:
            end_status, end_ms = _timestamp_ms(raw_time.get("end"))
            if end_status == "invalid":
                return _empty(
                    _research_trace_id(resolved_session_id, resolved_user_message_id),
                    "research",
                    catalog,
                    "invalid_timestamp",
                    malformed=malformed_count + 1,
                    duplicate=duplicate_count,
                )
            if end_ms is not None:
                ends.setdefault(call_id, []).append(end_ms)

    trace_id = _research_trace_id(resolved_session_id, resolved_user_message_id)
    calls: list[NormalizedTimeoutCall] = []
    for call_id in observed_call_ids:
        tool_name = definitions.get(call_id)
        if tool_name is None:
            return _empty(
                trace_id,
                "research",
                catalog,
                "missing_tool_sla",
                malformed=malformed_count,
                duplicate=duplicate_count,
            )
        if call_id in explicit_timeouts:
            calls.append({"call_id": call_id, "timeout_reason": "explicit_timeout"})
            continue
        start_values = starts.get(call_id, [])
        end_values = ends.get(call_id, [])
        if not start_values or not end_values:
            calls.append({"call_id": call_id, "timeout_reason": "missing_timestamps"})
            continue
        start_ms = min(start_values)
        end_ms = max(end_values)
        if end_ms < start_ms:
            return _empty(
                trace_id,
                "research",
                catalog,
                "invalid_event_order",
                malformed=malformed_count,
                duplicate=duplicate_count,
            )
        timeout_ms = catalog.timeout_ms_by_tool[tool_name]
        reason: TimeoutReason | None = "sla_exceeded" if end_ms - start_ms > timeout_ms else None
        calls.append({"call_id": call_id, "timeout_reason": reason})
    return _finalize(
        trace_id,
        "research",
        catalog,
        calls,
        malformed=malformed_count,
        duplicate=duplicate_count,
    )


def _finalize(
    trace_id: str | None,
    mode: str,
    catalog: ToolTimeoutPolicyCatalog,
    calls: list[NormalizedTimeoutCall],
    *,
    malformed: int,
    duplicate: int = 0,
) -> NormalizedToolTimeoutTrace:
    error = None if calls else "no_tool_calls"
    reasons = [call["timeout_reason"] for call in calls]
    return {
        "trace_id": trace_id,
        "mode": mode,
        "policy_version": catalog.policy_version,
        "tool_calls": tuple(calls),
        "diagnostics": {
            "explicit_timeout_count": reasons.count("explicit_timeout"),
            "sla_exceeded_count": reasons.count("sla_exceeded"),
            "missing_time_timeout_count": reasons.count("missing_timestamps"),
            "duplicate_state_event_count": duplicate,
            "malformed_event_count": malformed,
        },
        "normalization_error": error,
    }


def _empty(
    trace_id: str | None,
    mode: str,
    catalog: ToolTimeoutPolicyCatalog,
    error: str,
    *,
    malformed: int = 0,
    duplicate: int = 0,
) -> NormalizedToolTimeoutTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "policy_version": catalog.policy_version,
        "tool_calls": (),
        "diagnostics": {
            "explicit_timeout_count": 0,
            "sla_exceeded_count": 0,
            "missing_time_timeout_count": 0,
            "duplicate_state_event_count": duplicate,
            "malformed_event_count": malformed,
        },
        "normalization_error": error,
    }


def _chat_explicit_timeout(
    response: Mapping[object, object] | None, catalog: ToolTimeoutPolicyCatalog
) -> bool:
    if response is None:
        return False
    candidates: list[object] = [response.get("status"), response.get("state")]
    for key in ("result", "rawResult"):
        nested = response.get(key)
        if isinstance(nested, Mapping):
            candidates.extend((nested.get("status"), nested.get("state")))
    return any(
        status in catalog.explicit_timeout_statuses
        for candidate in candidates
        if (status := _normalized_status(candidate)) is not None
    )


def _duration_ms(value: object) -> tuple[str, int | None]:
    """把聊天结果的秒级耗时转换为毫秒，并区分缺失、非法与负值。"""

    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "invalid", None
    try:
        numeric = float(value)
    except OverflowError:
        return "invalid", None
    if not math.isfinite(numeric):
        return "invalid", None
    if numeric < 0:
        return "negative", None
    return "valid", round(numeric * 1000)


def _timestamp_ms(value: object) -> tuple[str, int | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool):
        return "invalid", None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except OverflowError:
            return "invalid", None
        if not math.isfinite(numeric):
            return "invalid", None
        seconds = numeric / 1000 if abs(numeric) >= 100_000_000_000 else numeric
        try:
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return "invalid", None
        return "valid", round(parsed.timestamp() * 1000)
    if not isinstance(value, str):
        return "invalid", None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return "invalid", None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return "valid", round(parsed.timestamp() * 1000)


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _normalized_status(value: object) -> str | None:
    text = _text(value)
    return text.lower() if text is not None else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
