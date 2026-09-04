"""规范化模型与工具之间的完整循环，不保留请求、响应或模型正文。"""

from collections.abc import Mapping, Sequence
from typing import TypedDict

_TERMINAL_TOOL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


class ModelToolLoopDiagnostics(TypedDict):
    """只保留结构完整性计数，避免泄露模型或工具正文。"""

    malformed_event_count: int
    duplicate_loop_observation_count: int
    duplicate_tool_observation_count: int


class NormalizedModelToolLoopTrace(TypedDict):
    """供模型工具循环深度评测器消费的会话级安全聚合。"""

    trace_id: str | None
    mode: str
    model_round_count: int
    tool_calling_round_count: int
    completed_loop_count: int
    unique_tool_call_count: int
    diagnostics: ModelToolLoopDiagnostics
    normalization_error: str | None


def normalize_chat_model_tool_loop_depth(
    trace: Mapping[str, object],
) -> NormalizedModelToolLoopTrace:
    """聊天日志没有模型轮次边界，禁止用工具调用数估算循环深度。"""

    return _result(
        trace_id=_optional_identifier(trace.get("traceId")),
        mode="chat",
        normalization_error="missing_model_iteration_boundary",
    )


def normalize_research_model_tool_loop_depth(
    events: Sequence[object],
    *,
    session_id: str | None = None,
    user_message_id: str | None = None,
) -> NormalizedModelToolLoopTrace:
    """按研究模式 loop 和 callID 计算完整的模型→工具→模型循环。"""

    resolved_session_id = session_id
    resolved_user_message_id = user_message_id
    rounds: dict[int, tuple[str, tuple[str, ...]]] = {}
    requested_call_rounds: dict[str, int] = {}
    terminal_call_ids: set[str] = set()
    observed_tool_call_ids: set[str] = set()
    malformed_event_count = 0
    duplicate_loop_observation_count = 0
    duplicate_tool_observation_count = 0
    turn_terminal_observed = False
    normalization_error: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_model_loop_events"
            continue

        resolved_session_id = resolved_session_id or _optional_identifier(event.get("sessionID"))
        resolved_user_message_id = resolved_user_message_id or _optional_identifier(
            event.get("userMessageID")
        )
        event_type = event.get("type")
        if event_type == "turn.end":
            turn_terminal_observed = True
            continue
        if event_type == "llm.response":
            loop_index = _positive_int(event.get("loop"))
            if loop_index is None:
                normalization_error = normalization_error or "missing_model_iteration_boundary"
                continue
            result = event.get("result")
            if not isinstance(result, Mapping):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_model_loop_events"
                continue
            finish_reason = _optional_identifier(result.get("finishReason"))
            if finish_reason is None:
                normalization_error = normalization_error or "missing_model_finish_reason"
                continue
            raw_tool_calls = result.get("toolCalls", ())
            if raw_tool_calls is None:
                raw_tool_calls = ()
            if not _is_sequence(raw_tool_calls):
                malformed_event_count += 1
                normalization_error = normalization_error or "invalid_model_loop_events"
                continue

            call_ids: list[str] = []
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, Mapping):
                    malformed_event_count += 1
                    normalization_error = normalization_error or "invalid_model_loop_events"
                    continue
                call_id = _optional_identifier(raw_call.get("toolCallId"))
                if call_id is None:
                    normalization_error = normalization_error or "missing_tool_call_id"
                    continue
                call_ids.append(call_id)

            unique_call_ids = tuple(sorted(set(call_ids)))
            if finish_reason == "tool-calls" and not unique_call_ids:
                normalization_error = normalization_error or "missing_tool_call_id"
            elif finish_reason != "tool-calls" and unique_call_ids:
                normalization_error = normalization_error or "conflicting_model_loop_definition"

            signature = (finish_reason, unique_call_ids)
            existing = rounds.get(loop_index)
            if existing is None:
                rounds[loop_index] = signature
                for call_id in unique_call_ids:
                    previous_round = requested_call_rounds.get(call_id)
                    if previous_round is not None and previous_round != loop_index:
                        normalization_error = (
                            normalization_error or "conflicting_model_loop_definition"
                        )
                    requested_call_rounds[call_id] = loop_index
            elif existing == signature:
                duplicate_loop_observation_count += 1
            else:
                normalization_error = normalization_error or "conflicting_model_loop_definition"
            continue

        if event_type != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping):
            malformed_event_count += 1
            normalization_error = normalization_error or "invalid_model_loop_events"
            continue
        if part.get("type") != "tool":
            continue
        call_id = _optional_identifier(part.get("callID"))
        if call_id is None:
            normalization_error = normalization_error or "missing_tool_call_id"
            continue
        if call_id in observed_tool_call_ids:
            duplicate_tool_observation_count += 1
        observed_tool_call_ids.add(call_id)
        state = part.get("state")
        if isinstance(state, Mapping):
            status = _optional_identifier(state.get("status"))
            if status is not None and status.lower() in _TERMINAL_TOOL_STATUSES:
                terminal_call_ids.add(call_id)

    ordered_loop_indexes = sorted(rounds)
    tool_rounds = {
        loop_index: signature[1]
        for loop_index, signature in rounds.items()
        if signature[0] == "tool-calls"
    }
    if not rounds:
        normalization_error = normalization_error or "missing_model_iteration_boundary"
    elif ordered_loop_indexes != list(range(1, ordered_loop_indexes[-1] + 1)):
        normalization_error = normalization_error or "non_contiguous_loop_index"
    elif tool_rounds and max(tool_rounds) == ordered_loop_indexes[-1]:
        normalization_error = normalization_error or "incomplete_model_tool_cycle"

    requested_call_ids = set(requested_call_rounds)
    if requested_call_ids - terminal_call_ids:
        normalization_error = normalization_error or "missing_tool_terminal_state"
    if not turn_terminal_observed:
        normalization_error = normalization_error or "missing_turn_terminal"

    return _result(
        trace_id=_research_trace_id(resolved_session_id, resolved_user_message_id),
        mode="research",
        model_round_count=len(rounds),
        tool_calling_round_count=len(tool_rounds),
        completed_loop_count=len(tool_rounds) if normalization_error is None else 0,
        unique_tool_call_count=len(requested_call_ids),
        malformed_event_count=malformed_event_count,
        duplicate_loop_observation_count=duplicate_loop_observation_count,
        duplicate_tool_observation_count=duplicate_tool_observation_count,
        normalization_error=normalization_error,
    )


def _result(
    *,
    trace_id: str | None,
    mode: str,
    model_round_count: int = 0,
    tool_calling_round_count: int = 0,
    completed_loop_count: int = 0,
    unique_tool_call_count: int = 0,
    malformed_event_count: int = 0,
    duplicate_loop_observation_count: int = 0,
    duplicate_tool_observation_count: int = 0,
    normalization_error: str | None,
) -> NormalizedModelToolLoopTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "model_round_count": model_round_count,
        "tool_calling_round_count": tool_calling_round_count,
        "completed_loop_count": completed_loop_count,
        "unique_tool_call_count": unique_tool_call_count,
        "diagnostics": {
            "malformed_event_count": malformed_event_count,
            "duplicate_loop_observation_count": duplicate_loop_observation_count,
            "duplicate_tool_observation_count": duplicate_tool_observation_count,
        },
        "normalization_error": normalization_error,
    }


def _research_trace_id(session_id: str | None, user_message_id: str | None) -> str | None:
    if session_id and user_message_id:
        return f"{session_id}:{user_message_id}"
    return session_id or user_message_id


def _optional_identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
