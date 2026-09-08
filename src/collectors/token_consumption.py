"""规范化聊天与研究模式的模型 Token 消耗。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class TokenConsumptionDiagnostics(TypedDict):
    """Token 覆盖与重复快照的安全诊断。"""

    valid_step_count: int
    token_missing_count: int
    duplicate_token_snapshot_count: int


class NormalizedTokenConsumptionTrace(TypedDict):
    """供 Token 消耗评估器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    step_token_totals: tuple[int, ...]
    diagnostics: TokenConsumptionDiagnostics
    normalization_error: str | None


def normalize_chat_token_consumption(
    trace: Mapping[str, object],
) -> NormalizedTokenConsumptionTrace:
    """汇总 Agent 模型步骤的输入与输出 Token，忽略缓存字段和 answer 重复快照。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure")

    trace_id = _text(meta.get("traceId"))
    totals: list[int] = []
    missing_count = 0
    for agent in agents:
        if not isinstance(agent, Mapping):
            return _empty("chat", trace_id, "invalid_chat_trace_structure")
        messages = agent.get("messages")
        if not _is_sequence(messages):
            return _empty("chat", trace_id, "invalid_chat_trace_structure")
        for message in messages:
            if not isinstance(message, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure")
            content = message.get("content")
            if not isinstance(content, Mapping):
                return _empty("chat", trace_id, "invalid_chat_trace_structure")
            # 仅含 tool_result 的消息是工具结果投影，并不代表一次新的模型调用。
            if "durationSeconds" not in content and "usageTokens" not in content:
                continue
            usage = content.get("usageTokens")
            if usage is None:
                missing_count += 1
                continue
            if not isinstance(usage, Mapping):
                return _empty("chat", trace_id, "invalid_token_count")
            input_tokens = _token_count(usage.get("input_tokens"))
            output_tokens = _token_count(usage.get("output_tokens"))
            if input_tokens is None or output_tokens is None:
                return _empty("chat", trace_id, "invalid_token_count")
            # cache_tokens 的上游包含关系未确认，按已确认口径不读取也不派生指标。
            totals.append(input_tokens + output_tokens)
    return _finalize("chat", trace_id, totals, missing_count)


def normalize_research_token_consumption(
    events: Sequence[object],
) -> NormalizedTokenConsumptionTrace:
    """按 messageID 去重 step-finish，并使用其权威 total 字段。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    session_id: str | None = None
    user_message_id: str | None = None
    snapshots: dict[str, int | None] = {}
    duplicate_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "turn.start":
            session_id = session_id or _text(event.get("sessionID"))
            user_message_id = user_message_id or _text(event.get("userMessageID"))
            continue
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "step-finish":
            continue
        message_id = _text(part.get("messageID"))
        if message_id is None:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "missing_step_id",
                duplicate_count=duplicate_count,
            )
        raw_tokens = part.get("tokens")
        if raw_tokens is None:
            total = None
        elif not isinstance(raw_tokens, Mapping):
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "invalid_token_count",
                duplicate_count=duplicate_count,
            )
        else:
            raw_total = raw_tokens.get("total")
            if raw_total is None:
                total = None
            else:
                total = _token_count(raw_total)
                if total is None:
                    return _empty(
                        "research",
                        _research_trace_id(session_id, user_message_id),
                        "invalid_token_count",
                        duplicate_count=duplicate_count,
                    )
        if message_id not in snapshots:
            snapshots[message_id] = total
        elif snapshots[message_id] == total:
            duplicate_count += 1
        else:
            return _empty(
                "research",
                _research_trace_id(session_id, user_message_id),
                "conflicting_token_snapshot",
                duplicate_count=duplicate_count,
            )

    totals = [total for total in snapshots.values() if total is not None]
    missing_count = sum(total is None for total in snapshots.values())
    return _finalize(
        "research",
        _research_trace_id(session_id, user_message_id),
        totals,
        missing_count,
        duplicate_count,
    )


def _finalize(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    totals: list[int],
    missing_count: int,
    duplicate_count: int = 0,
) -> NormalizedTokenConsumptionTrace:
    if not totals:
        error = "no_observable_token_usage"
    elif missing_count:
        error = "incomplete_token_coverage"
    else:
        error = None
    return {
        "trace_id": trace_id,
        "mode": mode,
        "step_token_totals": tuple(totals),
        "diagnostics": {
            "valid_step_count": len(totals),
            "token_missing_count": missing_count,
            "duplicate_token_snapshot_count": duplicate_count,
        },
        "normalization_error": error,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    *,
    duplicate_count: int = 0,
) -> NormalizedTokenConsumptionTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "step_token_totals": (),
        "diagnostics": {
            "valid_step_count": 0,
            "token_missing_count": 0,
            "duplicate_token_snapshot_count": duplicate_count,
        },
        "normalization_error": error,
    }


def _token_count(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _research_trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
