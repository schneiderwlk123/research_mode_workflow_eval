"""规范化 Trace 级缓存 Token 与真实输入 Token，不保留模型正文。"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class CacheInputTokenRatioDiagnostics(TypedDict):
    """缓存 Token 覆盖与重复快照的安全诊断。"""

    valid_step_count: int
    token_missing_count: int
    duplicate_token_snapshot_count: int


class NormalizedCacheInputTokenRatioTrace(TypedDict):
    """供缓存 Token 比率评估器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    cache_token_consumption: int | None
    effective_input_token_consumption: int | None
    diagnostics: CacheInputTokenRatioDiagnostics
    normalization_error: str | None


def normalize_chat_cache_input_token_ratio(
    trace: Mapping[str, object],
) -> NormalizedCacheInputTokenRatioTrace:
    """按已确认的“input 包含 cache”语义汇总聊天模型步骤。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas.get("meta")
    agents = canvas.get("agents")
    if not isinstance(meta, Mapping) or not _is_sequence(agents):
        return _empty("chat", None, "invalid_chat_trace_structure")

    trace_id = _text(meta.get("traceId"))
    cache_totals: list[int] = []
    effective_inputs: list[int] = []
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
            if "durationSeconds" not in content and "usageTokens" not in content:
                continue
            usage = content.get("usageTokens")
            if usage is None:
                missing_count += 1
                continue
            if not isinstance(usage, Mapping):
                return _empty("chat", trace_id, "invalid_token_count")
            raw_input = usage.get("input_tokens")
            raw_cache = usage.get("cache_tokens")
            if raw_input is None or raw_cache is None:
                missing_count += 1
                continue
            input_tokens = _token_count(raw_input)
            cache_tokens = _token_count(raw_cache)
            if input_tokens is None or cache_tokens is None:
                return _empty("chat", trace_id, "invalid_token_count")
            if cache_tokens > input_tokens:
                return _empty("chat", trace_id, "inconsistent_cache_components")
            cache_totals.append(cache_tokens)
            effective_inputs.append(input_tokens)
    return _finalize("chat", trace_id, cache_totals, effective_inputs, missing_count)


def normalize_research_cache_input_token_ratio(
    events: Sequence[object],
) -> NormalizedCacheInputTokenRatioTrace:
    """按 messageID 去重研究步骤，并把 input 与 cache 合为真实输入。"""

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    session_id: str | None = None
    user_message_id: str | None = None
    snapshots: dict[str, tuple[int, int, int, int, int, int] | None] = {}
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
        step_id = _text(part.get("messageID"))
        if step_id is None:
            return _empty(
                "research",
                _trace_id(session_id, user_message_id),
                "missing_step_id",
                duplicates=duplicate_count,
            )
        tokens = part.get("tokens")
        snapshot, error = _research_snapshot(tokens)
        if error is not None:
            return _empty(
                "research",
                _trace_id(session_id, user_message_id),
                error,
                duplicates=duplicate_count,
            )
        if step_id not in snapshots:
            snapshots[step_id] = snapshot
        elif snapshots[step_id] == snapshot:
            duplicate_count += 1
        else:
            return _empty(
                "research",
                _trace_id(session_id, user_message_id),
                "conflicting_token_snapshot",
                duplicates=duplicate_count,
            )

    complete = [snapshot for snapshot in snapshots.values() if snapshot is not None]
    cache_totals = [snapshot[3] + snapshot[4] for snapshot in complete]
    effective_inputs = [snapshot[0] + snapshot[3] + snapshot[4] for snapshot in complete]
    missing_count = sum(snapshot is None for snapshot in snapshots.values())
    return _finalize(
        "research",
        _trace_id(session_id, user_message_id),
        cache_totals,
        effective_inputs,
        missing_count,
        duplicate_count,
    )


def _research_snapshot(
    value: object,
) -> tuple[tuple[int, int, int, int, int, int] | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, "invalid_token_count"
    cache = value.get("cache")
    required = (value.get("input"), value.get("output"), value.get("reasoning"), value.get("total"))
    if any(item is None for item in required) or cache is None:
        return None, None
    if not isinstance(cache, Mapping):
        return None, "invalid_token_count"
    cache_read = cache.get("read")
    cache_write = cache.get("write")
    if cache_read is None or cache_write is None:
        return None, None
    input_tokens = _token_count(required[0])
    output_tokens = _token_count(required[1])
    reasoning_tokens = _token_count(required[2])
    parsed_cache_read = _token_count(cache_read)
    parsed_cache_write = _token_count(cache_write)
    total = _token_count(required[3])
    if any(
        item is None
        for item in (
            input_tokens,
            output_tokens,
            reasoning_tokens,
            parsed_cache_read,
            parsed_cache_write,
            total,
        )
    ):
        return None, "invalid_token_count"
    assert input_tokens is not None
    assert output_tokens is not None
    assert reasoning_tokens is not None
    assert parsed_cache_read is not None
    assert parsed_cache_write is not None
    assert total is not None
    snapshot = (
        input_tokens,
        output_tokens,
        reasoning_tokens,
        parsed_cache_read,
        parsed_cache_write,
        total,
    )
    if sum(snapshot[:5]) != snapshot[5]:
        return None, "inconsistent_token_breakdown"
    return snapshot, None


def _finalize(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    cache_totals: list[int],
    effective_inputs: list[int],
    missing_count: int,
    duplicates: int = 0,
) -> NormalizedCacheInputTokenRatioTrace:
    if not effective_inputs:
        error = "no_observable_cache_usage"
    elif missing_count:
        error = "incomplete_cache_token_coverage"
    elif sum(effective_inputs) == 0:
        error = "zero_effective_input_token_consumption"
    else:
        error = None
    return {
        "trace_id": trace_id,
        "mode": mode,
        "cache_token_consumption": sum(cache_totals) if effective_inputs else None,
        "effective_input_token_consumption": (sum(effective_inputs) if effective_inputs else None),
        "diagnostics": {
            "valid_step_count": len(effective_inputs),
            "token_missing_count": missing_count,
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
) -> NormalizedCacheInputTokenRatioTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "cache_token_consumption": None,
        "effective_input_token_consumption": None,
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
