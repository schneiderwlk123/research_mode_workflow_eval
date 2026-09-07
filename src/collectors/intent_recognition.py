"""提取意图识别 Judge 所需的用户 query 与代理行为证据。"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict, TypeGuard

from collectors.routing import CapabilityCatalog, CapabilityDefinition


@dataclass(frozen=True, slots=True)
class IntentEvidenceFragment:
    """一段仅在 Judge 调用生命周期内存在的代理行为证据。"""

    kind: str
    content: str


@dataclass(frozen=True, slots=True)
class IntentRecognitionCandidate:
    """意图对齐候选；正文不得进入持久化评测结果。"""

    query: str
    evidence_fragments: tuple[IntentEvidenceFragment, ...]
    capabilities: tuple[CapabilityDefinition, ...]


class IntentRecognitionDiagnostics(TypedDict):
    """不包含用户或代理正文的安全诊断计数。"""

    malformed_event_count: int
    out_of_scope_tool_call_count: int
    duplicate_call_definition_count: int
    duplicate_evidence_count: int


class NormalizedIntentRecognitionTrace(TypedDict):
    """意图识别规范化结果；candidate 只能在内存中传给 Judge。"""

    trace_id: str | None
    mode: str
    schema_version: str
    evidence_fragment_count: int
    actual_capability_count: int
    candidate: IntentRecognitionCandidate | None
    diagnostics: IntentRecognitionDiagnostics
    normalization_error: str | None


def normalize_chat_intent_recognition(
    trace: Mapping[str, object], catalog: CapabilityCatalog
) -> NormalizedIntentRecognitionTrace:
    """从新版 canvas 规范化 query、子任务、MCP 能力和最终回答证据。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _empty_chat_result(catalog, error="invalid_chat_trace_structure")
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _empty_chat_result(catalog, error="invalid_chat_trace_structure")
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _empty_chat_result(catalog, error="missing_trace_id")
    query = _text(meta.get("query"))
    fragments: list[IntentEvidenceFragment] = []
    seen_evidence: set[str] = set()
    calls: dict[str, str] = {}
    capabilities: set[str] = set()
    malformed_count = 0
    out_of_scope_count = 0
    duplicate_call_count = 0
    duplicate_evidence_count = 0
    normalization_error: str | None = None

    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _empty_chat_result(
            catalog,
            trace_id=trace_id,
            query=query,
            error="invalid_chat_trace_structure",
        )

    for raw_agent in raw_agents:
        if not isinstance(raw_agent, Mapping):
            malformed_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        raw_messages = raw_agent.get("messages", ())
        if raw_messages is None:
            raw_messages = ()
        if not _is_sequence(raw_messages):
            malformed_count += 1
            normalization_error = normalization_error or "invalid_chat_trace_structure"
            continue
        for raw_message in raw_messages:
            if not isinstance(raw_message, Mapping):
                malformed_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            content = raw_message.get("content", {})
            if not isinstance(content, Mapping):
                malformed_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            raw_calls = content.get("tool_call", ())
            if raw_calls is None:
                raw_calls = ()
            if not _is_sequence(raw_calls):
                malformed_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
                continue
            for raw_call in raw_calls:
                if not isinstance(raw_call, Mapping):
                    malformed_count += 1
                    normalization_error = normalization_error or "invalid_chat_trace_structure"
                    continue
                call_id = _text(raw_call.get("toolId"))
                tool_name = _text(raw_call.get("toolName"))
                agent_name = _text(raw_call.get("agentName"))
                if call_id is None and agent_name is not None:
                    arguments = _parse_arguments(raw_call.get("arguments"))
                    if arguments is None:
                        malformed_count += 1
                        continue
                    task = _text(arguments.get("query")) or _text(arguments.get("task"))
                    if task is None:
                        malformed_count += 1
                    else:
                        duplicate_evidence_count += _append_evidence(
                            fragments, seen_evidence, "subtask_query", task
                        )
                    continue

                if call_id is None and (tool_name is not None or "arguments" in raw_call):
                    malformed_count += 1
                    normalization_error = normalization_error or "missing_call_id"
                    continue
                if call_id is not None and tool_name is None:
                    malformed_count += 1
                    normalization_error = normalization_error or "missing_tool_name"
                    continue
                if call_id is None or tool_name is None:
                    continue
                previous = calls.get(call_id)
                if previous is not None:
                    if previous == tool_name:
                        duplicate_call_count += 1
                    else:
                        normalization_error = normalization_error or "conflicting_call_definition"
                    continue
                calls[call_id] = tool_name
                capability_id = catalog.resolve(tool_name)
                if capability_id is None:
                    out_of_scope_count += 1
                else:
                    capabilities.add(capability_id)

    answer = _text(_chat_answer(canvas))
    if answer is not None and not _is_pre_intent_failure(answer):
        duplicate_evidence_count += _append_evidence(
            fragments, seen_evidence, "final_answer", answer
        )

    if query is None:
        normalization_error = normalization_error or "missing_query"
    elif not fragments and not capabilities and _is_pre_intent_failure(answer):
        normalization_error = normalization_error or "pre_intent_failure"
    elif not fragments and not capabilities:
        normalization_error = normalization_error or "no_intent_evidence"

    return _build_result(
        trace_id=trace_id,
        mode="chat",
        query=query,
        fragments=fragments,
        capability_ids=capabilities,
        catalog=catalog,
        malformed_count=malformed_count,
        out_of_scope_count=out_of_scope_count,
        duplicate_call_count=duplicate_call_count,
        duplicate_evidence_count=duplicate_evidence_count,
        normalization_error=normalization_error,
    )


def _empty_chat_result(
    catalog: CapabilityCatalog,
    *,
    trace_id: str | None = None,
    query: str | None = None,
    error: str,
) -> NormalizedIntentRecognitionTrace:
    return _build_result(
        trace_id=trace_id,
        mode="chat",
        query=query,
        fragments=[],
        capability_ids=set(),
        catalog=catalog,
        malformed_count=1,
        out_of_scope_count=0,
        duplicate_call_count=0,
        duplicate_evidence_count=0,
        normalization_error=error,
    )


def normalize_research_intent_recognition(
    events: Sequence[object], catalog: CapabilityCatalog
) -> NormalizedIntentRecognitionTrace:
    """按调用 ID 去重研究日志，并提取规划文本与 MCP 能力作为行为证据。"""

    query: str | None = None
    session_id: str | None = None
    user_message_id: str | None = None
    fragments: list[IntentEvidenceFragment] = []
    seen_evidence: set[str] = set()
    calls: dict[str, str] = {}
    capabilities: set[str] = set()
    malformed_count = 0
    out_of_scope_count = 0
    duplicate_call_count = 0
    duplicate_evidence_count = 0
    normalization_error: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            malformed_count += 1
            continue
        session_id = session_id or _text(event.get("sessionID"))
        user_message_id = user_message_id or _text(event.get("userMessageID"))
        query = query or _text(event.get("query"))
        body = event.get("body")
        if isinstance(body, Mapping):
            query = query or _text(body.get("query"))
            duplicate_evidence_count += _append_evidence(
                fragments, seen_evidence, "plan_task", _text(body.get("task"))
            )
            duplicate_evidence_count += _append_evidence(
                fragments,
                seen_evidence,
                "plan_description",
                _text(body.get("description")),
            )
        elif body is not None:
            malformed_count += 1

        result = event.get("result")
        if not isinstance(result, Mapping):
            continue
        raw_calls = result.get("toolCalls")
        if raw_calls is None:
            continue
        if not _is_sequence(raw_calls):
            malformed_count += 1
            continue
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                malformed_count += 1
                continue
            call_id = _text(raw_call.get("toolCallId"))
            tool_name = _text(raw_call.get("toolName"))
            if call_id is None:
                normalization_error = normalization_error or "missing_call_id"
                continue
            if tool_name is None:
                malformed_count += 1
                continue
            previous = calls.get(call_id)
            if previous is not None:
                if previous == tool_name:
                    duplicate_call_count += 1
                else:
                    normalization_error = "conflicting_call_definition"
                continue
            calls[call_id] = tool_name
            capability_id = catalog.resolve(tool_name)
            if capability_id is None:
                out_of_scope_count += 1
            else:
                capabilities.add(capability_id)

    if query is None:
        normalization_error = normalization_error or "missing_query"
    elif not fragments and not capabilities:
        normalization_error = normalization_error or "no_intent_evidence"
    trace_id = (
        f"{session_id}:{user_message_id}"
        if session_id is not None and user_message_id is not None
        else session_id or user_message_id
    )
    return _build_result(
        trace_id=trace_id,
        mode="research",
        query=query,
        fragments=fragments,
        capability_ids=capabilities,
        catalog=catalog,
        malformed_count=malformed_count,
        out_of_scope_count=out_of_scope_count,
        duplicate_call_count=duplicate_call_count,
        duplicate_evidence_count=duplicate_evidence_count,
        normalization_error=normalization_error,
    )


def _build_result(
    *,
    trace_id: str | None,
    mode: str,
    query: str | None,
    fragments: list[IntentEvidenceFragment],
    capability_ids: set[str],
    catalog: CapabilityCatalog,
    malformed_count: int,
    out_of_scope_count: int,
    duplicate_call_count: int,
    duplicate_evidence_count: int,
    normalization_error: str | None,
) -> NormalizedIntentRecognitionTrace:
    capability_definitions = tuple(
        catalog.capabilities[capability_id] for capability_id in sorted(capability_ids)
    )
    candidate = (
        IntentRecognitionCandidate(query, tuple(fragments), capability_definitions)
        if query is not None and normalization_error is None
        else None
    )
    return {
        "trace_id": trace_id,
        "mode": mode,
        "schema_version": catalog.schema_version,
        "evidence_fragment_count": len(fragments),
        "actual_capability_count": len(capability_definitions),
        "candidate": candidate,
        "diagnostics": {
            "malformed_event_count": malformed_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "duplicate_call_definition_count": duplicate_call_count,
            "duplicate_evidence_count": duplicate_evidence_count,
        },
        "normalization_error": normalization_error,
    }


def _append_evidence(
    fragments: list[IntentEvidenceFragment],
    seen: set[str],
    kind: str,
    content: str | None,
) -> int:
    if content is None:
        return 0
    if content in seen:
        return 1
    seen.add(content)
    fragments.append(IntentEvidenceFragment(kind, content))
    return 0


def _parse_arguments(value: object) -> Mapping[str, object] | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return parsed if isinstance(parsed, Mapping) else None


def _chat_answer(canvas: Mapping[object, object]) -> object:
    answer = canvas.get("answer")
    return answer.get("answer") if isinstance(answer, Mapping) else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_pre_intent_failure(value: object) -> bool:
    text = _text(value)
    return text is not None and text.rstrip("。") == "模型服务暂时不可用，请稍后重试"


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
