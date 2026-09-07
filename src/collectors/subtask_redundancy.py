"""使用确定性文本规则统计聊天规划中的高置信度冗余子任务。"""

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, TypeGuard

ALGORITHM_VERSION = "character_bigram_jaccard_v1"
SIMILARITY_THRESHOLD = 0.9
MAX_NOVELTY_RATIO = 0.1

_CONSTRAINT_TOKEN = re.compile(r"[a-z0-9._%+-]*\d[a-z0-9._%+-]*")


class SubtaskRedundancyDiagnostics(TypedDict):
    """不含子任务正文的规则诊断计数。"""

    exact_duplicate_subtask_count: int
    similar_redundant_subtask_count: int
    malformed_event_count: int


class NormalizedSubtaskRedundancyTrace(TypedDict):
    """供冗余率评测器消费的安全聚合结果。"""

    trace_id: str | None
    mode: str
    total_subtask_count: int
    redundant_subtask_count: int
    algorithm_version: str
    similarity_threshold: float
    max_novelty_ratio: float
    diagnostics: SubtaskRedundancyDiagnostics
    normalization_error: str | None


@dataclass(frozen=True, slots=True)
class _SubtaskFingerprint:
    """仅在单次规范化期间存在，禁止写入评测结果或持久化介质。"""

    canonical_parts: tuple[str, ...]
    bigrams: frozenset[str]
    constraints: frozenset[str]


def normalize_chat_subtask_redundancy(
    trace: Mapping[str, object],
) -> NormalizedSubtaskRedundancyTrace:
    """从新版 canvas 提取子 Agent 任务并计算高置信度冗余下界。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty_result(
            trace_id=None,
            mode="chat",
            error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty_result(
            trace_id=None,
            mode="chat",
            error="invalid_chat_trace_structure",
            malformed_event_count=1,
        )
    trace_id = _identifier(meta.get("traceId"))
    if trace_id is None:
        return _empty_result(
            trace_id=None,
            mode="chat",
            error="missing_trace_id",
            malformed_event_count=1,
        )
    normalized = _empty_result(trace_id=trace_id, mode="chat")
    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        normalized["diagnostics"]["malformed_event_count"] = 1
        normalized["normalization_error"] = "invalid_chat_trace_structure"
        return normalized

    fingerprints: list[_SubtaskFingerprint] = []
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = (
                normalized["normalization_error"] or "invalid_chat_trace_structure"
            )
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = (
                normalized["normalization_error"] or "invalid_chat_trace_structure"
            )
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                normalized["diagnostics"]["malformed_event_count"] += 1
                normalized["normalization_error"] = (
                    normalized["normalization_error"] or "invalid_chat_trace_structure"
                )
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                normalized["diagnostics"]["malformed_event_count"] += 1
                normalized["normalization_error"] = (
                    normalized["normalization_error"] or "invalid_chat_trace_structure"
                )
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                normalized["diagnostics"]["malformed_event_count"] += 1
                normalized["normalization_error"] = (
                    normalized["normalization_error"] or "invalid_chat_trace_structure"
                )
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    normalized["diagnostics"]["malformed_event_count"] += 1
                    normalized["normalization_error"] = (
                        normalized["normalization_error"] or "invalid_chat_trace_structure"
                    )
                    continue
                if (
                    _identifier(raw_call.get("toolId")) is not None
                    or _identifier(raw_call.get("agentName")) is None
                ):
                    continue
                fingerprint = _fingerprint_from_call(raw_call)
                if fingerprint is None:
                    normalized["diagnostics"]["malformed_event_count"] += 1
                    normalized["normalization_error"] = (
                        normalized["normalization_error"] or "invalid_subtask_event"
                    )
                    continue

                duplicate_kind = _duplicate_kind(fingerprint, fingerprints)
                if duplicate_kind is not None:
                    normalized["redundant_subtask_count"] += 1
                    if duplicate_kind == "exact":
                        normalized["diagnostics"]["exact_duplicate_subtask_count"] += 1
                    else:
                        normalized["diagnostics"]["similar_redundant_subtask_count"] += 1
                fingerprints.append(fingerprint)
                normalized["total_subtask_count"] += 1
    return normalized


def normalize_research_subtask_redundancy(
    events: Sequence[object],
) -> NormalizedSubtaskRedundancyTrace:
    """研究日志尚无确认的结构化子任务契约，因此不得从描述文本推测。"""

    session_id: str | None = None
    user_message_id: str | None = None
    malformed_event_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            malformed_event_count += 1
            continue
        session_id = session_id or _identifier(event.get("sessionID"))
        user_message_id = user_message_id or _identifier(event.get("userMessageID"))
    trace_id = (
        f"{session_id}:{user_message_id}"
        if session_id is not None and user_message_id is not None
        else session_id or user_message_id
    )
    normalized = _empty_result(
        trace_id=trace_id,
        mode="research",
        error="missing_structured_subtasks",
    )
    normalized["diagnostics"]["malformed_event_count"] = malformed_event_count
    return normalized


def _fingerprint_from_call(call: Mapping[str, object]) -> _SubtaskFingerprint | None:
    arguments = _parse_arguments(call.get("arguments"))
    if arguments is None:
        return None
    task = _identifier(arguments.get("query")) or _identifier(arguments.get("task"))
    if task is None:
        return None
    canonical = _canonical_text(task)
    if not canonical:
        return None
    canonical_parts = (canonical,)
    bigrams = _character_bigrams(canonical)
    constraints = frozenset(
        _CONSTRAINT_TOKEN.findall(unicodedata.normalize("NFKC", task).casefold())
    )
    return _SubtaskFingerprint(canonical_parts, bigrams, constraints)


def _parse_arguments(value: object) -> Mapping[str, object] | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return parsed if isinstance(parsed, Mapping) else None


def _duplicate_kind(
    current: _SubtaskFingerprint, previous: Sequence[_SubtaskFingerprint]
) -> str | None:
    for candidate in previous:
        if current.canonical_parts == candidate.canonical_parts:
            return "exact"
        if current.constraints != candidate.constraints:
            continue
        union = current.bigrams | candidate.bigrams
        similarity = len(current.bigrams & candidate.bigrams) / len(union)
        novelty_ratio = len(current.bigrams - candidate.bigrams) / len(current.bigrams)
        if similarity >= SIMILARITY_THRESHOLD and novelty_ratio <= MAX_NOVELTY_RATIO:
            return "similar_redundant"
    return None


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _character_bigrams(value: str) -> frozenset[str]:
    if len(value) < 2:
        return frozenset((value,))
    return frozenset(value[index : index + 2] for index in range(len(value) - 1))


def _empty_result(
    *,
    trace_id: str | None,
    mode: str,
    error: str | None = None,
    malformed_event_count: int = 0,
) -> NormalizedSubtaskRedundancyTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "total_subtask_count": 0,
        "redundant_subtask_count": 0,
        "algorithm_version": ALGORITHM_VERSION,
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "max_novelty_ratio": MAX_NOVELTY_RATIO,
        "diagnostics": {
            "exact_duplicate_subtask_count": 0,
            "similar_redundant_subtask_count": 0,
            "malformed_event_count": malformed_event_count,
        },
        "normalization_error": error,
    }


def _identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
