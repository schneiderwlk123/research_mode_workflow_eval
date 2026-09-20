"""规范化 Trace 级输出 Token 与总 Token，不保留模型正文。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class OutputTokenRatioDiagnostics(TypedDict):
    valid_step_count: int
    token_missing_count: int
    duplicate_token_snapshot_count: int


class NormalizedOutputTokenRatioTrace(TypedDict):
    trace_id: str | None
    mode: Literal["chat", "research"]
    output_token_consumption: int | None
    total_token_consumption: int | None
    diagnostics: OutputTokenRatioDiagnostics
    normalization_error: str | None


def normalize_chat_output_token_ratio(
    trace: Mapping[str, object],
) -> NormalizedOutputTokenRatioTrace:
    """汇总聊天模型步骤的输出与总 Token，排除 answer 重复快照。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure")
    trace_id = _text(meta.get("traceId"))
    outputs: list[int] = []
    totals: list[int] = []
    missing = 0
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
            if "durationSeconds" not in content and "usageTokens" not in content:
                continue
            usage = content.get("usageTokens")
            if usage is None:
                missing += 1
                continue
            if not isinstance(usage, Mapping):
                return _empty("chat", trace_id, "invalid_token_count")
            input_tokens = _token_count(usage.get("input_tokens"))
            output_tokens = _token_count(usage.get("output_tokens"))
            if input_tokens is None or output_tokens is None:
                return _empty("chat", trace_id, "invalid_token_count")
            outputs.append(output_tokens)
            totals.append(input_tokens + output_tokens)
    return _finalize("chat", trace_id, outputs, totals, missing)


def normalize_research_output_token_ratio(
    events: Sequence[object],
) -> NormalizedOutputTokenRatioTrace:
    """按 messageID 去重研究模式 step-finish Token 快照。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")
    session_id: str | None = None
    user_message_id: str | None = None
    snapshots: dict[str, tuple[int, int] | None] = {}
    duplicates = 0
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
        step_id = _text(part.get("messageID"))
        if step_id is None:
            return _empty("research", _trace_id(session_id, user_message_id), "missing_step_id")
        tokens = part.get("tokens")
        if tokens is None:
            snapshot = None
        elif not isinstance(tokens, Mapping):
            return _empty("research", _trace_id(session_id, user_message_id), "invalid_token_count")
        else:
            raw_output = tokens.get("output")
            raw_total = tokens.get("total")
            if raw_output is None or raw_total is None:
                snapshot = None
            else:
                output = _token_count(raw_output)
                total = _token_count(raw_total)
                if output is None or total is None:
                    return _empty(
                        "research",
                        _trace_id(session_id, user_message_id),
                        "invalid_token_count",
                    )
                if output > total:
                    return _empty(
                        "research",
                        _trace_id(session_id, user_message_id),
                        "inconsistent_token_components",
                    )
                snapshot = (output, total)
        if step_id not in snapshots:
            snapshots[step_id] = snapshot
        elif snapshots[step_id] == snapshot:
            duplicates += 1
        else:
            return _empty(
                "research",
                _trace_id(session_id, user_message_id),
                "conflicting_token_snapshot",
                duplicates=duplicates,
            )
    outputs = [snapshot[0] for snapshot in snapshots.values() if snapshot is not None]
    totals = [snapshot[1] for snapshot in snapshots.values() if snapshot is not None]
    missing = sum(snapshot is None for snapshot in snapshots.values())
    return _finalize(
        "research",
        _trace_id(session_id, user_message_id),
        outputs,
        totals,
        missing,
        duplicates,
    )


def _finalize(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    outputs: list[int],
    totals: list[int],
    missing: int,
    duplicates: int = 0,
) -> NormalizedOutputTokenRatioTrace:
    if not totals:
        error = "no_observable_token_usage"
    elif missing:
        error = "incomplete_token_coverage"
    elif sum(totals) == 0:
        error = "zero_total_token_consumption"
    else:
        error = None
    return {
        "trace_id": trace_id,
        "mode": mode,
        "output_token_consumption": sum(outputs) if outputs else None,
        "total_token_consumption": sum(totals) if totals else None,
        "diagnostics": {
            "valid_step_count": len(totals),
            "token_missing_count": missing,
            "duplicate_token_snapshot_count": duplicates,
        },
        "normalization_error": error,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    *,
    duplicates: int = 0,
) -> NormalizedOutputTokenRatioTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "output_token_consumption": None,
        "total_token_consumption": None,
        "diagnostics": {
            "valid_step_count": 0,
            "token_missing_count": 0,
            "duplicate_token_snapshot_count": duplicates,
        },
        "normalization_error": error,
    }


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
