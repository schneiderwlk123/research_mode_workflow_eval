"""按模型步骤关联业务工具与 Token，不保留参数或消息正文。"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class PerToolStepTokenStatistics(TypedDict):
    """单个工具名关联的模型步骤 Token 统计。"""

    tool_name: str
    call_count: int
    tool_step_count: int
    single_tool_step_count: int
    multi_tool_step_count: int
    token_observed_count: int
    token_missing_count: int
    tool_associated_token_total: int


class ToolStepTokenDiagnostics(TypedDict):
    shared_token_step_count: int
    duplicate_call_observation_count: int
    duplicate_token_snapshot_count: int
    excluded_agent_delegation_count: int
    excluded_agent_return_count: int
    malformed_event_count: int


class NormalizedToolStepTokenTrace(TypedDict):
    trace_id: str | None
    mode: Literal["chat", "research"]
    per_tool_statistics: tuple[PerToolStepTokenStatistics, ...]
    diagnostics: ToolStepTokenDiagnostics
    normalization_error: str | None


_CHAT_AGENT_RETURN_TOOL_NAMES = frozenset({"respond"})


def normalize_chat_tool_step_tokens(
    trace: Mapping[str, object],
) -> NormalizedToolStepTokenTrace:
    """把聊天工具调用关联到同一消息的 input/output Token。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure", malformed=1)
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _empty("chat", None, "missing_trace_id", malformed=1)

    definitions: dict[str, tuple[str, str]] = {}
    step_usage: dict[str, object] = {}
    duplicate_calls = 0
    excluded_delegations = 0
    for agent_index, raw_agent in enumerate(agents):
        if not isinstance(raw_agent, Mapping):
            return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
        messages = raw_agent.get("messages", ())
        if messages is None:
            messages = ()
        if not _is_sequence(messages):
            return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
        for message_index, raw_message in enumerate(messages):
            if not isinstance(raw_message, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            step_id = f"{agent_index}:{message_index}"
            step_usage[step_id] = content.get("usageTokens")
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    return _empty("chat", trace_id, "invalid_chat_trace_structure", malformed=1)
                call_id = _text(raw_call.get("toolId"))
                tool_name = _text(raw_call.get("toolName"))
                if call_id is None and tool_name is None and _text(raw_call.get("agentName")):
                    excluded_delegations += 1
                    continue
                if call_id is None:
                    return _empty("chat", trace_id, "missing_call_id", malformed=1)
                if tool_name is None:
                    return _empty("chat", trace_id, "missing_tool_name", malformed=1)
                existing = definitions.get(call_id)
                if existing is not None:
                    duplicate_calls += 1
                    if existing[0] != tool_name:
                        return _empty(
                            "chat", trace_id, "conflicting_call_definition", duplicate_calls
                        )
                    continue
                definitions[call_id] = (tool_name, step_id)

    business_definitions = {
        call_id: definition
        for call_id, definition in definitions.items()
        if definition[0].casefold() not in _CHAT_AGENT_RETURN_TOOL_NAMES
    }
    excluded_returns = len(definitions) - len(business_definitions)
    if not business_definitions:
        return _empty(
            "chat",
            trace_id,
            "no_tool_calls",
            duplicate=duplicate_calls,
            excluded_delegation=excluded_delegations,
            excluded_return=excluded_returns,
        )
    return _aggregate(
        "chat",
        trace_id,
        business_definitions,
        step_usage,
        duplicate_calls=duplicate_calls,
        excluded_delegations=excluded_delegations,
        excluded_returns=excluded_returns,
    )


def normalize_research_tool_step_tokens(
    events: Sequence[object],
) -> NormalizedToolStepTokenTrace:
    """按 messageID 关联研究工具生命周期与 step-finish Token。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure", malformed=1)
    session_id: str | None = None
    user_message_id: str | None = None
    definitions: dict[str, tuple[str, str]] = {}
    snapshots: dict[str, object] = {}
    duplicate_calls = 0
    duplicate_tokens = 0
    for event in events:
        if not isinstance(event, Mapping):
            return _empty("research", None, "invalid_research_trace_structure", malformed=1)
        session_id = session_id or _text(event.get("sessionID"))
        user_message_id = user_message_id or _text(event.get("userMessageID"))
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "invalid_research_trace_structure",
                malformed=1,
            )
        part_type = part.get("type")
        if part_type == "tool":
            call_id = _text(part.get("callID"))
            tool_name = _text(part.get("tool"))
            step_id = _text(part.get("messageID"))
            if call_id is None:
                return _empty(
                    "research",
                    _research_trace_id(session_id, user_message_id),
                    "missing_call_id",
                    malformed=1,
                )
            if tool_name is None:
                return _empty(
                    "research",
                    _research_trace_id(session_id, user_message_id),
                    "missing_tool_name",
                    malformed=1,
                )
            if step_id is None:
                return _empty(
                    "research",
                    _research_trace_id(session_id, user_message_id),
                    "missing_step_id",
                    malformed=1,
                )
            existing = definitions.get(call_id)
            if existing is not None:
                duplicate_calls += 1
                if existing != (tool_name, step_id):
                    return _empty(
                        "research",
                        _research_trace_id(session_id, user_message_id),
                        "conflicting_call_definition",
                        duplicate=duplicate_calls,
                    )
                continue
            definitions[call_id] = (tool_name, step_id)
        elif part_type == "step-finish":
            step_id = _text(part.get("messageID"))
            if step_id is None:
                return _empty(
                    "research",
                    _research_trace_id(session_id, user_message_id),
                    "missing_step_id",
                    malformed=1,
                )
            tokens = part.get("tokens")
            if tokens is None:
                total: object = None
            elif not isinstance(tokens, Mapping):
                return _empty(
                    "research",
                    _research_trace_id(session_id, user_message_id),
                    "invalid_token_count",
                )
            else:
                total = tokens.get("total")
                if total is not None and _token_count(total) is None:
                    return _empty(
                        "research",
                        _research_trace_id(session_id, user_message_id),
                        "invalid_token_count",
                    )
            if step_id in snapshots:
                if snapshots[step_id] != total:
                    return _empty(
                        "research",
                        _research_trace_id(session_id, user_message_id),
                        "conflicting_token_snapshot",
                        duplicate_tokens=duplicate_tokens,
                    )
                duplicate_tokens += 1
            else:
                snapshots[step_id] = total

    trace_id = _research_trace_id(session_id, user_message_id)
    if not definitions:
        return _empty("research", trace_id, "no_tool_calls", duplicate_tokens=duplicate_tokens)
    return _aggregate(
        "research",
        trace_id,
        definitions,
        snapshots,
        duplicate_calls=duplicate_calls,
        duplicate_tokens=duplicate_tokens,
    )


def _aggregate(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    definitions: Mapping[str, tuple[str, str]],
    raw_step_tokens: Mapping[str, object],
    *,
    duplicate_calls: int = 0,
    duplicate_tokens: int = 0,
    excluded_delegations: int = 0,
    excluded_returns: int = 0,
) -> NormalizedToolStepTokenTrace:
    calls = Counter(tool_name for tool_name, _ in definitions.values())
    step_names: dict[str, set[str]] = defaultdict(set)
    for tool_name, step_id in definitions.values():
        step_names[step_id].add(tool_name)

    statistics: list[PerToolStepTokenStatistics] = []
    shared_steps = sum(len(names) > 1 for names in step_names.values())
    for tool_name in sorted(calls):
        associated_steps = [step_id for step_id, names in step_names.items() if tool_name in names]
        observed = 0
        missing = 0
        total = 0
        single = 0
        multi = 0
        for step_id in associated_steps:
            if len(step_names[step_id]) > 1:
                multi += 1
            else:
                single += 1
            raw_total = raw_step_tokens.get(step_id)
            if raw_total is None:
                missing += 1
                continue
            token_total = _chat_step_total(raw_total) if mode == "chat" else _token_count(raw_total)
            if token_total is None:
                return _empty(mode, trace_id, "invalid_token_count")
            observed += 1
            total += token_total
        statistics.append(
            {
                "tool_name": tool_name,
                "call_count": calls[tool_name],
                "tool_step_count": len(associated_steps),
                "single_tool_step_count": single,
                "multi_tool_step_count": multi,
                "token_observed_count": observed,
                "token_missing_count": missing,
                "tool_associated_token_total": total,
            }
        )
    return {
        "trace_id": trace_id,
        "mode": mode,
        "per_tool_statistics": tuple(statistics),
        "diagnostics": {
            "shared_token_step_count": shared_steps,
            "duplicate_call_observation_count": duplicate_calls,
            "duplicate_token_snapshot_count": duplicate_tokens,
            "excluded_agent_delegation_count": excluded_delegations,
            "excluded_agent_return_count": excluded_returns,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }


def _chat_step_total(value: object) -> int | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = _token_count(value.get("input_tokens"))
    output_tokens = _token_count(value.get("output_tokens"))
    return (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    duplicate: int = 0,
    *,
    duplicate_tokens: int = 0,
    excluded_delegation: int = 0,
    excluded_return: int = 0,
    malformed: int = 0,
) -> NormalizedToolStepTokenTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "per_tool_statistics": (),
        "diagnostics": {
            "shared_token_step_count": 0,
            "duplicate_call_observation_count": duplicate,
            "duplicate_token_snapshot_count": duplicate_tokens,
            "excluded_agent_delegation_count": excluded_delegation,
            "excluded_agent_return_count": excluded_return,
            "malformed_event_count": malformed,
        },
        "normalization_error": error,
    }


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
