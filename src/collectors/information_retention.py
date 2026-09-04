"""提取信息保留率 Judge 所需的最终产物与上游证据。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, TypeGuard

from collectors.evidence_quality import (
    EvidenceDiagnostics,
    EvidencePrecisionCandidate,
    EvidenceSourceCatalog,
    EvidenceUnit,
    NormalizedEvidenceTrace,
    normalize_chat_evidence,
    normalize_research_evidence,
)

_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "error", "cancelled", "canceled", "timeout", "timed_out"}
)


@dataclass(frozen=True, slots=True)
class InformationRetentionCandidate:
    """仅在 Judge 生命周期内保留的原始需求、最终产物和上游事实来源。"""

    query: str
    answer: str
    evidence_units: tuple[EvidenceUnit, ...]


class NormalizedInformationRetentionTrace(TypedDict):
    """信息保留率的规范化输入。"""

    trace_id: str | None
    mode: str
    schema_version: str
    evidence_scope_version: str
    evidence_unit_count: int
    candidate: InformationRetentionCandidate | None
    diagnostics: EvidenceDiagnostics
    normalization_error: str | None


def normalize_chat_information_retention(
    trace: Mapping[str, object], catalog: EvidenceSourceCatalog
) -> NormalizedInformationRetentionTrace:
    """规范化聊天回答；没有可靠上游事实时不估算信息保留率。"""

    evidence = normalize_chat_evidence(trace, catalog)
    query = _text(trace.get("query"))
    answer = _text(trace.get("answer"))
    error = _retention_evidence_error(
        evidence["normalization_error"],
        observation_complete=_chat_evidence_observation_complete(trace, catalog),
    )
    if query is None:
        error = "missing_query"
    elif answer is None:
        error = "missing_final_answer"
    elif _is_service_failure(answer):
        answer = ""
    return _build_result(evidence, query=query, answer=answer, error=error)


def normalize_research_information_retention(
    events: Sequence[object], catalog: EvidenceSourceCatalog
) -> NormalizedInformationRetentionTrace:
    """规范化研究最终文本；完整空输出保留为可计算的零分候选。"""

    evidence = normalize_research_evidence(events, catalog)
    query = _research_query(events)
    answer, turn_ended = _research_final_answer(events)
    error = _retention_evidence_error(
        evidence["normalization_error"],
        observation_complete=_research_evidence_observation_complete(events, catalog),
    )
    if query is None:
        error = "missing_query"
    elif not turn_ended:
        error = "incomplete_session_observation"
    elif answer is None and _has_successful_artifact_write(events):
        error = "missing_final_artifact_content"
    elif answer is None:
        answer = ""
    return _build_result(evidence, query=query, answer=answer, error=error)


def _build_result(
    evidence: NormalizedEvidenceTrace,
    *,
    query: str | None,
    answer: str | None,
    error: str | None,
) -> NormalizedInformationRetentionTrace:
    raw_candidate = evidence.get("candidate")
    units = (
        raw_candidate.evidence_units
        if isinstance(raw_candidate, EvidencePrecisionCandidate)
        else ()
    )
    candidate = (
        InformationRetentionCandidate(query, answer, units)
        if query is not None and answer is not None and units and error is None
        else None
    )
    return {
        "trace_id": evidence["trace_id"] if isinstance(evidence["trace_id"], str) else None,
        "mode": str(evidence["mode"]),
        "schema_version": str(evidence["schema_version"]),
        "evidence_scope_version": str(evidence["evidence_scope_version"]),
        "evidence_unit_count": evidence["evidence_unit_count"],
        "candidate": candidate,
        "diagnostics": evidence["diagnostics"],
        "normalization_error": error,
    }


def _retention_evidence_error(error: object, *, observation_complete: bool) -> str | None:
    if error is None:
        return None
    if error == "no_evidence_calls":
        return "no_upstream_evidence"
    if error == "no_successful_evidence_responses":
        return (
            "no_successful_upstream_results"
            if observation_complete
            else "incomplete_evidence_observation"
        )
    if error == "no_evidence_units":
        return "no_upstream_facts"
    if error in {"no_stable_evidence_units", "incomplete_evidence_segmentation"}:
        return "incomplete_fact_segmentation"
    return str(error)


def _chat_evidence_observation_complete(
    trace: Mapping[str, object], catalog: EvidenceSourceCatalog
) -> bool:
    raw_queue = trace.get("tool_call_queue", ())
    if not _is_sequence(raw_queue):
        return False
    for event in raw_queue:
        if not isinstance(event, Mapping) or event.get("event_type") != "tool_call":
            continue
        request = event.get("tool_request_args")
        if not isinstance(request, Mapping) or catalog.resolve(request.get("toolName")) is None:
            continue
        response = event.get("tool_response_result")
        if not isinstance(response, Mapping) or not response:
            return False
        status = _text(response.get("status"))
        if status is not None and status.lower() not in _TERMINAL_STATUSES:
            return False
    return True


def _research_evidence_observation_complete(
    events: Sequence[object], catalog: EvidenceSourceCatalog
) -> bool:
    evidence_call_ids: set[str] = set()
    terminal_call_ids: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        result = event.get("result")
        raw_calls = result.get("toolCalls") if isinstance(result, Mapping) else None
        if _is_sequence(raw_calls):
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    continue
                call_id = _text(raw_call.get("toolCallId"))
                if call_id is not None and catalog.resolve(raw_call.get("toolName")) is not None:
                    evidence_call_ids.add(call_id)
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        status = _text(state.get("status")) if isinstance(state, Mapping) else None
        call_id = _text(part.get("callID"))
        if call_id is not None and status is not None and status.lower() in _TERMINAL_STATUSES:
            terminal_call_ids.add(call_id)
    return evidence_call_ids.issubset(terminal_call_ids)


def _research_query(events: Sequence[object]) -> str | None:
    query: str | None = None
    for event in events:
        if not isinstance(event, Mapping):
            continue
        query = query or _text(event.get("query"))
        body = event.get("body")
        if isinstance(body, Mapping):
            query = query or _text(body.get("query"))
    return query


def _research_final_answer(events: Sequence[object]) -> tuple[str | None, bool]:
    assistant_ids: set[str] = set()
    answer: str | None = None
    turn_ended = False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "turn.end":
            turn_ended = True
            break
        if event.get("type") == "message.updated":
            info = event.get("info")
            if isinstance(info, Mapping) and info.get("role") == "assistant":
                message_id = _text(info.get("id"))
                if message_id is not None:
                    assistant_ids.add(message_id)
            continue
        if event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "text":
            continue
        if _text(part.get("messageID")) not in assistant_ids:
            continue
        answer = _text(part.get("text")) or answer
    return answer, turn_ended


def _has_successful_artifact_write(events: Sequence[object]) -> bool:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, Mapping) or _text(state.get("status")) != "completed":
            continue
        payload = state.get("output") or state.get("raw")
        if isinstance(payload, Mapping) and any(
            payload.get(key) not in (None, "")
            for key in ("path", "file_path", "filePath", "artifact_id", "artifactId")
        ):
            return True
    return False


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_service_failure(answer: str) -> bool:
    return answer.rstrip("。") == "模型服务暂时不可用，请稍后重试"


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
