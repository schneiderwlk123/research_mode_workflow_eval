"""从聊天与研究日志中提取可独立判断相关性的证据单元。"""

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypedDict, TypeGuard

_ENVELOPE_KEYS = ("data", "items", "results", "documents", "chunks", "records")
_EVIDENCE_EXTRACTION_VERSION = "2.0.0"
_FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "canceled", "timeout", "timed_out"})
_STATUS_ONLY_KEYS = frozenset(
    {
        "code",
        "error",
        "errorcode",
        "errordesc",
        "failed_query",
        "failures",
        "message",
        "msg",
        "status",
        "success",
        "tipType",
    }
)
_SOURCE_ID_KEYS = (
    "chunk_id",
    "record_id",
    "row_id",
    "source_id",
    "document_id",
    "doc_id",
    "url",
    "link",
)


@dataclass(frozen=True, slots=True)
class EvidenceSourceCatalog:
    """显式声明哪些 MCP 工具返回外部来源证据。"""

    schema_version: str
    scope_version: str
    evidence_tool_names: frozenset[str]
    aliases: Mapping[str, str]

    def resolve(self, tool_name: object) -> str | None:
        """解析聊天及研究模式中的工具别名，框架与派生计算工具保持在范围外。"""

        normalized = _text(tool_name)
        if normalized is None:
            return None
        resolved = self.aliases.get(normalized)
        if resolved is None and normalized.startswith("mcp__"):
            resolved = self.aliases.get(normalized.rsplit("__", 1)[-1])
        return resolved if resolved in self.evidence_tool_names else None


@dataclass(frozen=True, slots=True)
class EvidenceUnit:
    """仅在 Judge 生命周期内保留的来源原生证据单元。"""

    unit_id: str
    tool_name: str
    content: str


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """共享证据候选；query 与正文不得进入持久化结果。"""

    query: str
    evidence_units: tuple[EvidenceUnit, ...]


class EvidenceDiagnostics(TypedDict):
    """不含 query、来源正文或内容摘要的安全诊断计数。"""

    malformed_event_count: int
    out_of_scope_tool_call_count: int
    evidence_call_count: int
    successful_evidence_response_count: int
    unsegmentable_evidence_response_count: int
    duplicate_evidence_unit_count: int
    duplicate_call_definition_count: int
    duplicate_result_observation_count: int
    orphan_result_count: int


class NormalizedEvidenceTrace(TypedDict):
    """供证据依赖指标复用的规范化输入。"""

    trace_id: str | None
    mode: str
    schema_version: str
    evidence_scope_version: str
    evidence_unit_count: int
    candidate: EvidenceCandidate | None
    diagnostics: EvidenceDiagnostics
    normalization_error: str | None


def build_evidence_source_catalog(
    manual: Mapping[str, object],
    *,
    evidence_tool_names: Set[str],
    aliases: Mapping[str, str] | None = None,
) -> EvidenceSourceCatalog:
    """从 MCP 手册校验并构建显式证据工具目录，避免把计算工具误当来源。"""

    schema_version = _text(manual.get("manual_version"))
    raw_tools = manual.get("tools")
    if schema_version is None:
        raise ValueError("MCP 手册 manual_version 必须是非空字符串")
    if not _is_sequence(raw_tools):
        raise ValueError("MCP 手册 tools 必须是数组")
    known_tools: set[str] = set()
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping):
            raise ValueError("MCP 工具定义必须是对象")
        name = _text(raw_tool.get("name"))
        if name is None:
            raise ValueError("MCP 工具 name 必须是非空字符串")
        if name in known_tools:
            raise ValueError(f"MCP 工具 name 重复: {name}")
        known_tools.add(name)

    selected_values: set[str] = set()
    for raw_name in evidence_tool_names:
        name = _text(raw_name)
        if name is None:
            raise ValueError("证据工具名称必须是非空字符串")
        selected_values.add(name)
    selected = frozenset(selected_values)
    if not selected:
        raise ValueError("证据工具名单不能为空")
    unknown = sorted(selected - known_tools)
    if unknown:
        raise ValueError(f"证据工具包含未知 MCP 工具: {', '.join(unknown)}")
    resolved_aliases = {name: name for name in known_tools}
    for raw_alias, raw_name in (aliases or {}).items():
        alias = _text(raw_alias)
        name = _text(raw_name)
        if alias is None or name is None:
            raise ValueError("证据工具别名和目标名称必须是非空字符串")
        if name not in known_tools:
            raise ValueError(f"证据工具别名指向未知 MCP 工具: {name}")
        existing = resolved_aliases.get(alias)
        if existing is not None and existing != name:
            raise ValueError(f"证据工具别名冲突: {alias}")
        resolved_aliases[alias] = name
    scope_payload = {
        "evidence_extraction_version": _EVIDENCE_EXTRACTION_VERSION,
        "evidence_tool_names": sorted(selected),
        "aliases": sorted(
            (alias, target)
            for alias, target in resolved_aliases.items()
            if target in selected and alias != target
        ),
    }
    scope_digest = hashlib.sha256(
        json.dumps(scope_payload, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return EvidenceSourceCatalog(
        schema_version,
        f"evidence-scope-{scope_digest}",
        selected,
        MappingProxyType(resolved_aliases),
    )


def normalize_chat_evidence(
    trace: Mapping[str, object], catalog: EvidenceSourceCatalog
) -> NormalizedEvidenceTrace:
    """按新版 canvas 跨 Agent 和消息关联证据请求与结果。"""

    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return _chat_structure_error(catalog, trace_id=None, reason="invalid_chat_trace_structure")
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return _chat_structure_error(catalog, trace_id=None, reason="invalid_chat_trace_structure")
    trace_id = _text(meta.get("traceId"))
    if trace_id is None:
        return _chat_structure_error(catalog, trace_id=None, reason="missing_trace_id")
    query = _text(meta.get("query"))
    if query is None:
        return _chat_structure_error(catalog, trace_id=trace_id, reason="missing_query")

    raw_agents = canvas.get("agents", ())
    if raw_agents is None:
        raw_agents = ()
    if not _is_sequence(raw_agents):
        return _chat_structure_error(
            catalog,
            trace_id=trace_id,
            reason="invalid_chat_trace_structure",
        )

    malformed_count = 0
    out_of_scope_count = 0
    duplicate_call_count = 0
    duplicate_result_count = 0
    orphan_result_count = 0
    normalization_error: str | None = None
    calls: dict[str, tuple[str, str | None]] = {}
    results: list[Mapping[str, object]] = []

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
            else:
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        malformed_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    call_id = _text(raw_call.get("toolId"))
                    raw_tool_name = _text(raw_call.get("toolName"))
                    agent_name = _text(raw_call.get("agentName"))
                    if call_id is None and raw_tool_name is None and agent_name is not None:
                        continue
                    if call_id is None and raw_tool_name is not None:
                        malformed_count += 1
                        normalization_error = normalization_error or "missing_call_id"
                        continue
                    if call_id is not None and raw_tool_name is None:
                        malformed_count += 1
                        normalization_error = normalization_error or "missing_tool_name"
                        continue
                    if call_id is None or raw_tool_name is None:
                        malformed_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    tool_name = catalog.resolve(raw_tool_name)
                    previous = calls.get(call_id)
                    definition = (raw_tool_name, tool_name)
                    if previous is not None:
                        duplicate_call_count += 1
                        if previous != definition:
                            normalization_error = (
                                normalization_error or "conflicting_call_definition"
                            )
                        continue
                    calls[call_id] = definition
                    if tool_name is None:
                        out_of_scope_count += 1

            raw_results = content.get("tool_result", ())
            if raw_results is None:
                raw_results = ()
            if not _is_sequence(raw_results):
                malformed_count += 1
                normalization_error = normalization_error or "invalid_chat_trace_structure"
            else:
                for raw_result in raw_results:
                    if not isinstance(raw_result, Mapping):
                        malformed_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    if not all(isinstance(key, str) for key in raw_result):
                        malformed_count += 1
                        normalization_error = normalization_error or "invalid_chat_trace_structure"
                        continue
                    if _text(raw_result.get("toolId")) is None:
                        malformed_count += 1
                        normalization_error = normalization_error or "missing_call_id"
                        continue
                    results.append({str(key): value for key, value in raw_result.items()})

    evidence_calls = {
        call_id: tool_name for call_id, (_, tool_name) in calls.items() if tool_name is not None
    }
    successful_response_count = 0
    unsegmentable_count = 0
    duplicate_unit_count = 0
    units: list[EvidenceUnit] = []
    seen_source_units: set[str] = set()
    result_digests: dict[str, str] = {}
    for response in results:
        call_id = _text(response.get("toolId"))
        assert call_id is not None
        call_definition = calls.get(call_id)
        if call_definition is None:
            orphan_result_count += 1
            continue
        raw_tool_name, tool_name = call_definition
        result_tool_name = _text(response.get("toolName"))
        if result_tool_name is not None and result_tool_name != raw_tool_name:
            normalization_error = normalization_error or "conflicting_call_definition"
        payload = _preferred_payload(response)
        status = _text(response.get("status"))
        response_digest = _canonical_digest(
            {
                "status": status.lower() if status is not None else None,
                "payload": payload,
            }
        )
        if response_digest is None:
            normalization_error = normalization_error or "conflicting_call_result"
            continue
        previous_digest = result_digests.get(call_id)
        if previous_digest is not None:
            duplicate_result_count += 1
            if previous_digest != response_digest:
                normalization_error = normalization_error or "conflicting_call_result"
            continue
        result_digests[call_id] = response_digest
        if tool_name is None or _is_failed_response(response):
            continue
        if payload is None:
            continue
        successful_response_count += 1
        contents = _extract_stable_contents(payload)
        if contents is None:
            unsegmentable_count += 1
            continue
        duplicate_unit_count += _append_unique_units(
            units,
            seen_source_units,
            call_id=call_id,
            tool_name=tool_name,
            contents=contents,
        )

    error = _normalization_error(
        query=query,
        evidence_call_count=len(evidence_calls),
        successful_response_count=successful_response_count,
        evidence_unit_count=len(units),
        unsegmentable_response_count=unsegmentable_count,
        existing=normalization_error,
    )
    return _build_result(
        trace_id=trace_id,
        mode="chat",
        schema_version=catalog.schema_version,
        scope_version=catalog.scope_version,
        query=query,
        units=units,
        diagnostics={
            "malformed_event_count": malformed_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "evidence_call_count": len(evidence_calls),
            "successful_evidence_response_count": successful_response_count,
            "unsegmentable_evidence_response_count": unsegmentable_count,
            "duplicate_evidence_unit_count": duplicate_unit_count,
            "duplicate_call_definition_count": duplicate_call_count,
            "duplicate_result_observation_count": duplicate_result_count,
            "orphan_result_count": orphan_result_count,
        },
        normalization_error=error,
    )


def _chat_structure_error(
    catalog: EvidenceSourceCatalog,
    *,
    trace_id: str | None,
    reason: str,
) -> NormalizedEvidenceTrace:
    return _build_result(
        trace_id=trace_id,
        mode="chat",
        schema_version=catalog.schema_version,
        scope_version=catalog.scope_version,
        query=None,
        units=[],
        diagnostics={
            "malformed_event_count": 1,
            "out_of_scope_tool_call_count": 0,
            "evidence_call_count": 0,
            "successful_evidence_response_count": 0,
            "unsegmentable_evidence_response_count": 0,
            "duplicate_evidence_unit_count": 0,
            "duplicate_call_definition_count": 0,
            "duplicate_result_observation_count": 0,
            "orphan_result_count": 0,
        },
        normalization_error=reason,
    )


def normalize_research_evidence(
    events: Sequence[object], catalog: EvidenceSourceCatalog
) -> NormalizedEvidenceTrace:
    """使用 callID 连接研究模式的工具定义与成功终态响应。"""

    query: str | None = None
    session_id: str | None = None
    user_message_id: str | None = None
    calls: dict[str, tuple[str, str | None]] = {}
    malformed_count = 0
    out_of_scope_count = 0
    duplicate_call_count = 0
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
            raw_tool_name = _text(raw_call.get("toolName"))
            if call_id is None:
                normalization_error = normalization_error or "missing_call_id"
                continue
            if raw_tool_name is None:
                malformed_count += 1
                continue
            tool_name = catalog.resolve(raw_tool_name)
            previous = calls.get(call_id)
            definition = (tool_name or raw_tool_name, tool_name)
            if previous is not None:
                if previous == definition:
                    duplicate_call_count += 1
                else:
                    normalization_error = "conflicting_call_definition"
                continue
            calls[call_id] = definition
            if tool_name is None:
                out_of_scope_count += 1

    evidence_calls = {
        call_id: tool_name for call_id, (_, tool_name) in calls.items() if tool_name is not None
    }
    successful_response_count = 0
    unsegmentable_count = 0
    duplicate_unit_count = 0
    duplicate_result_count = 0
    orphan_result_count = 0
    units: list[EvidenceUnit] = []
    seen_source_units: set[str] = set()
    seen_terminal_calls: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "tool":
            continue
        state = part.get("state")
        if not isinstance(state, Mapping) or _text(state.get("status")) != "completed":
            continue
        call_id = _text(part.get("callID"))
        if call_id is None:
            normalization_error = normalization_error or "missing_call_id"
            continue
        if call_id not in calls:
            orphan_result_count += 1
            continue
        tool_name = evidence_calls.get(call_id)
        if tool_name is None:
            continue
        if call_id in seen_terminal_calls:
            duplicate_result_count += 1
            continue
        seen_terminal_calls.add(call_id)
        payload = _preferred_payload(state, primary="output", fallback="raw")
        if payload is None:
            continue
        successful_response_count += 1
        contents = _extract_stable_contents(payload)
        if contents is None:
            unsegmentable_count += 1
            continue
        duplicate_unit_count += _append_unique_units(
            units,
            seen_source_units,
            call_id=call_id,
            tool_name=tool_name,
            contents=contents,
        )

    normalization_error = _normalization_error(
        query=query,
        evidence_call_count=len(evidence_calls),
        successful_response_count=successful_response_count,
        evidence_unit_count=len(units),
        unsegmentable_response_count=unsegmentable_count,
        existing=normalization_error,
    )
    trace_id = (
        f"{session_id}:{user_message_id}"
        if session_id is not None and user_message_id is not None
        else session_id or user_message_id
    )
    return _build_result(
        trace_id=trace_id,
        mode="research",
        schema_version=catalog.schema_version,
        scope_version=catalog.scope_version,
        query=query,
        units=units,
        diagnostics={
            "malformed_event_count": malformed_count,
            "out_of_scope_tool_call_count": out_of_scope_count,
            "evidence_call_count": len(evidence_calls),
            "successful_evidence_response_count": successful_response_count,
            "unsegmentable_evidence_response_count": unsegmentable_count,
            "duplicate_evidence_unit_count": duplicate_unit_count,
            "duplicate_call_definition_count": duplicate_call_count,
            "duplicate_result_observation_count": duplicate_result_count,
            "orphan_result_count": orphan_result_count,
        },
        normalization_error=normalization_error,
    )


def _build_result(
    *,
    trace_id: str | None,
    mode: str,
    schema_version: str,
    scope_version: str,
    query: str | None,
    units: list[EvidenceUnit],
    diagnostics: EvidenceDiagnostics,
    normalization_error: str | None,
) -> NormalizedEvidenceTrace:
    candidate = (
        EvidenceCandidate(query, tuple(units))
        if query is not None and normalization_error is None
        else None
    )
    return {
        "trace_id": trace_id,
        "mode": mode,
        "schema_version": schema_version,
        "evidence_scope_version": scope_version,
        "evidence_unit_count": len(units),
        "candidate": candidate,
        "diagnostics": diagnostics,
        "normalization_error": normalization_error,
    }


def _normalization_error(
    *,
    query: str | None,
    evidence_call_count: int,
    successful_response_count: int,
    evidence_unit_count: int,
    unsegmentable_response_count: int,
    existing: str | None,
) -> str | None:
    if existing is not None:
        return existing
    if query is None:
        return "missing_query"
    if evidence_call_count == 0:
        return "no_evidence_calls"
    if successful_response_count == 0:
        return "no_successful_evidence_responses"
    if evidence_unit_count == 0:
        return "no_stable_evidence_units" if unsegmentable_response_count else "no_evidence_units"
    if unsegmentable_response_count:
        return "incomplete_evidence_segmentation"
    return None


def _append_unique_units(
    units: list[EvidenceUnit],
    seen_source_units: set[str],
    *,
    call_id: str,
    tool_name: str,
    contents: tuple[str, ...],
) -> int:
    duplicate_count = 0
    next_index = 0
    for content in contents:
        source_key = _stable_source_key(tool_name, content)
        if source_key is not None and source_key in seen_source_units:
            duplicate_count += 1
            continue
        if source_key is not None:
            seen_source_units.add(source_key)
        units.append(EvidenceUnit(f"{call_id}:{next_index}", tool_name, content))
        next_index += 1
    return duplicate_count


def _stable_source_key(tool_name: str, content: str) -> str | None:
    """仅在记录自带来源标识时去重，避免合并不同来源的相同事实。"""

    parsed = json.loads(content)
    if not isinstance(parsed, Mapping):
        return None
    identifiers = {key: parsed[key] for key in _SOURCE_ID_KEYS if parsed.get(key) is not None}
    if not identifiers:
        return None
    identity = json.dumps(identifiers, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    content_digest = hashlib.sha256(content.encode()).hexdigest()
    return hashlib.sha256(f"{tool_name}\0{identity}\0{content_digest}".encode()).hexdigest()


def _canonical_digest(value: object) -> str | None:
    """仅为同一调用的重复结果生成内存指纹，不持久化响应正文。"""

    try:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_stable_contents(payload: object) -> tuple[str, ...] | None:
    parsed = payload
    if isinstance(parsed, str):
        text = parsed.strip()
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # 陈述支持率的分母是原子陈述，不是证据记录数。对已登记证据工具的完整文本响应，
            # 保留为响应级证据材料；后续仅为传输限额切块，不猜测其内部来源条数。
            return (json.dumps(text, ensure_ascii=False),)
    raw_units: Sequence[object]
    if isinstance(parsed, Mapping):
        selected: object | None = None
        for key in _ENVELOPE_KEYS:
            value = parsed.get(key)
            if value is not None:
                selected = value
                break
        if selected is None:
            raw_units = () if not parsed or set(parsed).issubset(_STATUS_ONLY_KEYS) else (parsed,)
        elif _is_sequence(selected):
            raw_units = selected
        elif isinstance(selected, Mapping):
            if not all(isinstance(key, str) for key in selected):
                return ()
            raw_units = tuple({key: selected[key]} for key in sorted(selected))
        elif isinstance(selected, str):
            return _extract_stable_contents(selected)
        else:
            return None
    elif _is_sequence(parsed):
        raw_units = parsed
    else:
        return None
    encoded: list[str] = []
    for raw_unit in raw_units:
        if raw_unit is None or raw_unit == "" or raw_unit == () or raw_unit == [] or raw_unit == {}:
            continue
        try:
            encoded.append(
                json.dumps(raw_unit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        except (TypeError, ValueError):
            return None
    return tuple(encoded)


def _preferred_payload(
    container: Mapping[str, object], *, primary: str = "result", fallback: str = "rawResult"
) -> object | None:
    primary_value = container.get(primary)
    if _has_content(primary_value):
        return primary_value
    fallback_value = container.get(fallback)
    return fallback_value if _has_content(fallback_value) else None


def _has_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping) or _is_sequence(value):
        return bool(value)
    return True


def _is_failed_response(response: Mapping[str, object]) -> bool:
    status = _text(response.get("status"))
    return status is not None and status.lower() in _FAILED_STATUSES


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
