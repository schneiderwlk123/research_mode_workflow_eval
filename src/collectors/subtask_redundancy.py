"""使用确定性文本规则统计聊天规划中的高置信度冗余子任务。"""

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
    """提取聊天子任务并计算确定性的高置信度冗余下界。"""

    normalized = _empty_result(trace_id=_identifier(trace.get("traceId")), mode="chat")
    raw_queue = trace.get("tool_call_queue", ())
    if raw_queue is None:
        raw_queue = ()
    if not _is_sequence(raw_queue):
        normalized["diagnostics"]["malformed_event_count"] = 1
        normalized["normalization_error"] = "invalid_tool_call_queue"
        return normalized

    fingerprints: list[_SubtaskFingerprint] = []
    for event in raw_queue:
        if not isinstance(event, Mapping):
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = "invalid_subtask_event"
            continue
        if event.get("event_type") != "call_sub_agent_begin":
            continue
        fingerprint = _fingerprint_from_event(event)
        if fingerprint is None:
            normalized["diagnostics"]["malformed_event_count"] += 1
            normalized["normalization_error"] = "invalid_subtask_event"
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


def _fingerprint_from_event(event: Mapping[str, object]) -> _SubtaskFingerprint | None:
    payload = event.get("request_payload")
    if not isinstance(payload, Mapping):
        return None
    task_name = _identifier(payload.get("taskName")) or _identifier(payload.get("taskDescription"))
    raw_arguments = payload.get("arguments")
    if raw_arguments is not None and not isinstance(raw_arguments, Mapping):
        return None
    query = (
        _identifier(raw_arguments.get("query")) or _identifier(raw_arguments.get("task"))
        if isinstance(raw_arguments, Mapping)
        else None
    )
    raw_parts = tuple(part for part in (task_name, query) if part is not None)
    canonical_parts = tuple(dict.fromkeys(_canonical_text(part) for part in raw_parts))
    if not canonical_parts or any(not part for part in canonical_parts):
        return None
    bigrams = frozenset(gram for part in canonical_parts for gram in _character_bigrams(part))
    constraints = frozenset(
        token
        for part in raw_parts
        for token in _CONSTRAINT_TOKEN.findall(unicodedata.normalize("NFKC", part).casefold())
    )
    return _SubtaskFingerprint(canonical_parts, bigrams, constraints)


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
    *, trace_id: str | None, mode: str, error: str | None = None
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
            "malformed_event_count": 0,
        },
        "normalization_error": error,
    }


def _identifier(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
